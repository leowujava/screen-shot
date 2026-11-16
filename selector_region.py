"""选区几何：逻辑选区 ↔ 物理截图裁剪 / 坐标换算 / 撤销快照预算。

从 selector.py 拆出的 RegionMixin：方法全部操作 Selector 实例状态
（self._region / self._screens / self.full_img / self.undo 等，
定义见 selector.Selector.__init__），通过多重继承组合，不反向依赖 selector。
"""
from PIL import Image, ImageChops


# 标注层二值化 LUT（0 → 0，其余 → 255）。
# .point() 传 LUT 走 C 级查表，替代逐像素 Python lambda
# （选区移动重定基时对整张选区图执行，lambda 版百万像素级耗时）。
_THRESHOLD_LUT = (0,) + (255,) * 255

# 撤销快照内存预算：每份快照 ≈ 选区宽 × 高 × 3 字节（RGB），
# 快照总数按选区尺寸动态计算（见 Selector._undo_maxlen_for）。
# 固定 50 份对小选区足够"撤销回干净底图"，但 4K 选区（≈24.9MB/份）
# 50 份会逼近 1.3GB；按预算折算后 4K ≈ 10 份、1080p ≈ 43 份，
# 撤销内存始终有界，小选区体验不变。
_UNDO_MEMORY_BUDGET = 256 * 1024 * 1024
_UNDO_MAXLEN = 50
_UNDO_MINLEN = 5


class RegionMixin:
    """选区 crop 与坐标换算（与 Selector 组合使用，见模块 docstring）。"""

    def _region_scale_for(self, region):
        """选区覆盖屏幕中最大的物理/逻辑缩放比（输出分辨率不降级）。

        单屏高 DPI：等于该屏 DPR；普通屏：1.0。选区跨多屏混合 DPI 时取最大
        值，保证输出至少和最高 DPI 屏幕的物理像素一一对应。
        """
        l, t, r, b = region
        scales = [max(m.sx, m.sy) for m in self._screens
                  if m.intersects(l, t, r, b)]
        if scales:
            return max(scales)
        return self._scale_x or 1.0

    def _region_img_size(self, region):
        """当前缩放比下，选区图应有的物理像素尺寸。"""
        s = self._region_scale
        l, t, r, b = region
        return (max(1, int(round((r - l) * s))),
                max(1, int(round((b - t) * s))))

    def _crop_region(self, region, scale):
        """按逻辑选区从整屏物理截图裁出选区图（物理分辨率）。

        普通屏走与原实现等价的单次 crop；高 DPI / 多屏时逐显示器裁剪后
        缩放到 ``逻辑尺寸 × scale`` 并拼接，保证输出为原始物理分辨率。
        """
        if self.full_img is None:
            return None
        l, t, r, b = region
        ow = max(1, int(round((r - l) * scale)))
        oh = max(1, int(round((b - t) * scale)))
        out = Image.new("RGB", (ow, oh))
        iw, ih = self.full_img.size
        # 混合 DPI 下逻辑屏幕矩形可能重叠：按缩放比从低到高粘贴，
        # 让高分辨率屏幕的物理内容覆盖重叠区（与 _local_to_full 一致）。
        for m in sorted(self._screens, key=lambda m: max(m.sx, m.sy)):
            il = max(l, m.lx)
            it = max(t, m.ly)
            ir = min(r, m.lx + m.lw)
            ib = min(b, m.ly + m.lh)
            if ir <= il or ib <= it:
                continue
            fx0, fy0 = m.local_to_physical(il, it)
            fx1, fy1 = m.local_to_physical(ir, ib)
            box = (max(0, min(int(round(fx0)), iw)),
                   max(0, min(int(round(fy0)), ih)),
                   max(0, min(int(round(fx1)), iw)),
                   max(0, min(int(round(fy1)), ih)))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            crop = self.full_img.crop(box)
            tw = max(1, int(round((ir - il) * scale)))
            th = max(1, int(round((ib - it) * scale)))
            if crop.size != (tw, th):
                crop = crop.resize((tw, th), Image.LANCZOS)
            out.paste(crop, (int(round((il - l) * scale)),
                             int(round((it - t) * scale))))
        return out

    def _init_region_img(self):
        """从 full_img crop 出 region_img，并按选区尺寸调整撤销快照上限。"""
        if self.full_img is None:
            return
        scale = self._region_scale_for(self._region)
        img = self._crop_region(self._region, scale)
        if img is None:
            return
        self._region_scale = scale
        self._region_img = img
        self.undo.maxlen = self._undo_maxlen_for(img.width, img.height)

    def _region_point(self, x, y):
        """overlay 逻辑坐标 → 选区图物理坐标（越界自动 clamp）。

        普通屏 scale=1 时与旧实现 ``x - left`` 完全等价。
        """
        l, t, r, b = self._region
        s = self._region_scale
        rx = int(round((x - l) * s))
        ry = int(round((y - t) * s))
        max_x = max(0, int(round((r - l) * s)) - 1)
        max_y = max(0, int(round((b - t) * s)) - 1)
        return max(0, min(rx, max_x)), max(0, min(ry, max_y))

    @staticmethod
    def _undo_maxlen_for(w, h):
        """撤销快照份数上限：min(_UNDO_MAXLEN, 预算 // 单份字节数)，下限 _UNDO_MINLEN。"""
        per = max(1, w * h * 3)
        return max(_UNDO_MINLEN, min(_UNDO_MAXLEN, _UNDO_MEMORY_BUDGET // per))

    def _rebase_annotations(self, img, old_base, new_base):
        """选区移动（尺寸不变）：把 img 的标注层重新定基到新位置。

        标注层 = img 与旧位置底图 old_base 的像素差（含抗锯齿边缘）；
        贴到新位置底图 new_base 上——新位置的屏幕内容可见，标注跟随保留。
        无标注（纯白/原图）时返回 new_base 的副本（仅位置变化）。
        """
        if img.size != new_base.size:
            return img
        # C 级 LUT 二值化（任意非零差 → 255），替代逐像素 Python lambda
        mask = ImageChops.difference(img, old_base).convert("L").point(
            _THRESHOLD_LUT)
        if mask.getbbox() is None:
            return new_base.copy()
        base = new_base.copy()
        base.paste(img, (0, 0), mask)
        return base

    def _adjust_region(self):
        """调整选区后重新 crop、清空标注和撤销栈。"""
        if self._region is None:
            # 手柄拖动到选区尺寸 < MIN_SIZE 时 _normalize 返回 None（_region 被置空）。
            # 此时选区无效，无法 crop / 清空标注，直接返回避免解包 None 崩溃。
            return
        l, t, r, b = self._region
        W, H = self._actual_w, self._actual_h
        l = max(0, min(l, W))
        t = max(0, min(t, H))
        r = max(0, min(r, W))
        b = max(0, min(b, H))
        if r - l < self.MIN_SIZE or b - t < self.MIN_SIZE:
            return
        self._init_region_img()
        self.undo.clear()
        # 缩放/贴边裁剪后 undo 已清空 → 选区内容必然等于定格底图 crop：
        # 走底图透出的轻量渲染（与移动路径同），避免手柄拖动缩放时
        # 每帧整图 PIL→QPixmap 转换（resize 帧开销主因）。
        self._render_moved_region()


    def _to_virtual(self, wx, wy):
        return (wx + self.origin_x, wy + self.origin_y)

    def _calc_region(self, wx1, wy1, wx2, wy2):
        vx1, vy1 = self._to_virtual(wx1, wy1)
        vx2, vy2 = self._to_virtual(wx2, wy2)
        left = min(vx1, vx2)
        top = min(vy1, vy2)
        w = abs(vx2 - vx1)
        h = abs(vy2 - vy1)
        if w < self.MIN_SIZE or h < self.MIN_SIZE:
            return None
        return (left, top, w, h)

    @staticmethod
    def _is_tiny_region(region):
        """region 是否仍是按下时的 1x1 占位框（纯点击、未拖出真实选区）。

        框选过渡中 mouseMoveEvent 只在实际宽或高 ≥ MIN_SIZE 时才更新选区，
        因此选区仍 ≤1px 即代表用户只是点了一下。
        """
        l, t, r, b = region
        return (r - l) <= 1 and (b - t) <= 1

    def _local_to_full(self, x, y):
        """overlay 逻辑坐标 → 整屏截图物理坐标（full_img 像素）。

        精确逐屏映射时按所在显示器换算（混合 DPI 也正确）；逻辑屏幕矩形
        可能重叠（Qt 混合 DPI），重叠区取缩放比最大的屏幕（与选区裁剪时
        高分辨率块覆盖低分辨率块的规则一致）。否则按全局缩放比退化。
        """
        best = None
        for m in self._screens:
            if m.contains(x, y):
                if best is None or max(m.sx, m.sy) >= max(best.sx, best.sy):
                    best = m
        if best is not None:
            fx, fy = best.local_to_physical(x, y)
            return int(round(fx)), int(round(fy))
        return int(round(x * self._scale_x)), int(round(y * self._scale_y))

    def _to_virtual_region(self):
        """当前选区 → mss 虚拟桌面坐标 (left, top, width, height)；无选区返回 None。"""
        if self._region is None:
            return None
        l, t, r, b = self._region
        if self._mapping_exact:
            fx_l, fy_t = self._local_to_full(l, t)
            fx_r, fy_b = self._local_to_full(r, b)
        else:
            fx_l, fy_t = int(l * self._scale_x), int(t * self._scale_y)
            fx_r, fy_b = int(r * self._scale_x), int(b * self._scale_y)
        return (fx_l + self.origin_x, fy_t + self.origin_y,
                fx_r - fx_l, fy_b - fy_t)

    def _normalize(self, region):
        l, t, r, b = region
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        W, H = self._actual_w, self._actual_h
        if r < 0 or b < 0 or l >= W or t >= H:
            return None
        l = max(0, min(l, W))
        t = max(0, min(t, H))
        r = max(0, min(r, W))
        b = max(0, min(b, H))
        if r - l < self.MIN_SIZE or b - t < self.MIN_SIZE:
            return None
        return (l, t, r, b)

    def _handle_coords(self, l, t, r, b):
        return {
            "nw": (l, t), "n": ((l + r) // 2, t), "ne": (r, t),
            "e": (r, (t + b) // 2), "se": (r, b),
            "s": ((l + r) // 2, b), "sw": (l, b), "w": (l, (t + b) // 2),
        }

