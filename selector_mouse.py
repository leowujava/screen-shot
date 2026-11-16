"""选区鼠标交互：框选 / 手柄缩放 / 移动 / 标注绘制（MouseMixin）。

从 selector.py 拆出：方法全部操作 Selector 实例状态（self._region /
self._drag_* / self.drawing 等，定义见 selector.Selector.__init__），
通过多重继承组合，不反向依赖 selector。

渲染与裁剪仍由 selector.py 提供（_render_all / _refresh_region_pixmap /
_crop_region 等）：测试通过 monkeypatch selector._pil_to_qpixmap 统计
整图转换次数，转换调用点必须留在 selector 模块命名空间。
"""
from PIL import ImageDraw

from PySide6.QtCore import Qt

from drawing import (
    alt_pressed, draw_arrow, draw_brush, draw_ellipse, draw_rect, mosaic,
)
from qtutil import _event_pos


class MouseMixin:
    """鼠标事件状态机（与 Selector 组合使用，见模块 docstring）。"""


    def _hit_test(self, x, y):
        if self._mode != "adjust" or self._region is None:
            return None
        # 画过标注后选区锁定：undo 栈非空表示已有标注，手柄不可拖动
        if self.undo:
            return None
        l, t, r, b = self._region
        s = self.HANDLE_SIZE // 2  # 6px 热区，避免标注误触手柄
        for name, (hx, hy) in self._handle_coords(l, t, r, b).items():
            if abs(x - hx) <= s and abs(y - hy) <= s:
                return name
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        x, y = _event_pos(event)
        # 点击工具条区域时忽略（工具条是独立窗口，正常不会收到这里的点击）
        if self._is_over_toolbar(x, y):
            return
        if self._mode == "select":
            # 按下立刻创建最小框并进入 adjust 模式
            self.start = (x, y)
            self._mode = "adjust"
            self._dragging_box = True
            W, H = self._actual_w, self._actual_h
            x = max(0, min(x, W - 1))
            y = max(0, min(y, H - 1))
            self._region = (x, y, x + 1, y + 1)
            self._init_region_img()
            self._set_toolbar_enabled(True)
            self._render_all()
            return
        if self._region is None:
            return
        hit = self._hit_test(x, y)
        if hit:
            self._drag_op = (hit, "resize")
            self._drag_start = (x, y)
            self._drag_region = self._region
            self._set_cursor_shape(self._CURSORS[hit])
            self._crosshair = None
            return
        l, t, r, b = self._region
        if self.tool == "move" and l <= x <= r and t <= y <= b:
            self._drag_op = ("move", "move")
            self._drag_start = (x, y)
            self._drag_region = self._region
            self._masks_hidden = True
            self._set_cursor_shape(self._CURSORS["move"])
            self._crosshair = None
            self.update()
            return
        # 选区内 → 开始标注
        if l <= x <= r and t <= y <= b:
            if self._region_img is None:
                return
            rx, ry = self._region_point(x, y)
            if self.tool == "text":
                self._input_text(rx, ry)
                self.drawing = False
                self.draw_start = None
                self._draw_start_logical = None
                return
            self.drawing = True
            self.draw_start = (rx, ry)
            self._draw_start_logical = (x, y)
            self._brush_last = (rx, ry)
            self._crosshair = None
            self.undo.push(self._region_img.copy())
            if self.tool == "brush":
                d = ImageDraw.Draw(self._region_img)
                d.point(self.draw_start, fill=self.color)
                # 起笔整图转换一次作底图，并重置累计 patch 状态
                # （bbox=None：首帧由 _expand_brush_bbox 从 _brush_last/当前点初始化）
                self._brush_moves = 0
                self._brush_bbox = None
                self._refresh_region_pixmap()
                self.update()
            self._update_cursor()

    def mouseMoveEvent(self, event):
        x, y = _event_pos(event)
        # select 模式：十字准星跟随
        if self._mode == "select":
            self._crosshair = (x, y, 0, 0)
            self._update_cursor()
            self.update()
            return
        if self._drag_op is None and self.start is not None:
            # select→adjust 过渡：更新框大小
            sx, sy = self.start
            x1, y1 = min(sx, x), min(sy, y)
            x2, y2 = max(sx, x), max(sy, y)
            W, H = self._actual_w, self._actual_h
            if sx <= 1 or x <= 1:
                x1 = 0
            if sy <= 1 or y <= 1:
                y1 = 0
            if sx >= W - 2 or x >= W - 2:
                x2 = W
            if sy >= H - 2 or y >= H - 2:
                y2 = H
            w = x2 - x1
            h = y2 - y1
            if w < self.MIN_SIZE and h < self.MIN_SIZE:
                return
            self._region = self._normalize((x1, y1, x2, y2))
            if self._region:
                l, t, r, b = self._region
                # 标签显示实际输出（物理）尺寸，高 DPI 下与保存结果一致
                scale = self._region_scale_for(self._region)
                self._crosshair = (
                    x, y,
                    int(round((r - l) * scale)),
                    int(round((b - t) * scale)),
                )
            self._render_all()
            return
        # 手柄 resize / 移动
        if self._drag_op is not None and self._drag_start is not None:
            op, kind = self._drag_op
            dx = x - self._drag_start[0]
            dy = y - self._drag_start[1]
            l, t, r, b = self._drag_region
            if kind == "move":
                l, t, r, b = l + dx, t + dy, r + dx, b + dy
            else:
                if op in ("w", "sw", "nw"):
                    l = l + dx
                if op in ("e", "ne", "se"):
                    r = r + dx
                if op in ("n", "nw", "ne"):
                    t = t + dy
                if op in ("s", "sw", "se"):
                    b = b + dy
            previous_region = self._region
            self._region = self._normalize((l, t, r, b))
            if (
                kind == "move"
                and self.full_img is not None
                and self._region is not None
                and (self._region[2] - self._region[0],
                     self._region[3] - self._region[1])
                == (previous_region[2] - previous_region[0],
                    previous_region[3] - previous_region[1])
            ):
                self._render_moved_region()
            else:
                self._adjust_region()
            return
        # 标注
        if self.drawing and self._region_img is not None:
            rx, ry = self._region_point(x, y)
            s = self._region_scale
            if self.tool == "brush":
                draw_brush(self._region_img, [self._brush_last, (rx, ry)],
                           self.color, self.width_attr * s)
                # 局部 patch 增量更新：只转换"自上次整图转换以来"累计线段
                # 包围盒，避免每帧整图转换，也不能只转最后一段（会丢轨迹）。
                # 每 _BRUSH_FULL_REFRESH_EVERY 段整图补齐一次，防止包围盒膨胀。
                self._brush_moves += 1
                if self._brush_moves >= self._BRUSH_FULL_REFRESH_EVERY:
                    self._brush_moves = 0
                    self._brush_bbox = None
                    self._refresh_region_pixmap()
                else:
                    x0, y0, x1, y1 = self._expand_brush_bbox(
                        self._brush_last, (rx, ry))
                    self._refresh_brush_patch(x0, y0, x1, y1)
                self._brush_last = (rx, ry)
                self.update()
            else:
                # 预览始终用 overlay 逻辑坐标（QPainter 直接画在窗口上）
                sx, sy = self._draw_start_logical or (x, y)
                if self.tool == "ellipse" and alt_pressed(event):
                    ex0, ey0, ex1, ey1 = self._constrain_ellipse(sx, sy, x, y)
                    self._set_preview(min(ex0, ex1), min(ey0, ey1),
                                      max(ex0, ex1), max(ey0, ey1))
                else:
                    self._set_preview(sx, sy, x, y)
        self._update_cursor(x, y)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x, y = _event_pos(event)
        completed_move = (
            self._drag_op is not None and self._drag_op[1] == "move"
        )
        moved_from = self._drag_region  # 拖动开始时的选区（移动重定基用）
        self.start = None
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None

        self._update_cursor(x, y)
        self._crosshair = None
        # 画框结束：切换到完整渲染（定格 + 重裁选区图）
        if self._dragging_box:
            self._dragging_box = False
            if self._region is None or self._is_tiny_region(self._region):
                # 纯点击（未拖出 ≥ MIN_SIZE 的框，选区仍是按下时的 1x1 占位框）：
                # 回到 select 模式且不定格——一次点击不应触发 hide→截屏→show
                # 的整屏捕获与闪烁，也不应留下 1x1 选区（与 MIN_SIZE 语义一致）。
                self._region = None
                self._mode = "select"
                self._set_toolbar_enabled(False)
                self._tb_hide()
                self._render_all()
                self.update()
                return
            try:
                self._freeze_selection()
                # _freeze_selection 里 hide → 截图 → show 会让 overlay 窗口重映射，
                # 键盘焦点可能随之丢失（落到之前的应用）。
                # 强制把焦点拉回 overlay，保证松开鼠标后直接可用键盘
                # （Ctrl+Z / Esc / Enter 等）操作选区。
                self._force_focus()
                self._init_region_img()
                self._render_all()
            except Exception as exc:
                # 锁屏 / RDP 断开 / 显示器变化等导致截屏失败时，异常若穿透
                # Qt 事件循环会直接结束进程，这里统一回退到 select 模式。
                self._abort_capture(exc)
                return
            self.update()
            return
        if self._region is None:
            # 手柄拖到选区 < MIN_SIZE（_normalize 置空）后落笔：回到 select 模式，
            # 收起工具条（此前工具条会残留悬浮在 select 模式的画面上）。
            self._mode = "select"
            self._set_toolbar_enabled(False)
            self._tb_hide()
            self._render_all()
            self.update()
            return
        if completed_move and self.full_img is not None:
            # 移动保持尺寸：标注跟随内容——把标注层（旧图与旧位置底图的差）贴到
            # 新位置底图上；撤销快照同样重新定基，Ctrl+Z 依然有效。
            l, t, r, b = self._region
            old_img = self._region_img
            old_region = moved_from
            if (old_img is not None and old_region is not None
                    and old_img.size == self._region_img_size(self._region)):
                old_base = self._crop_region(old_region, self._region_scale)
                if old_base.size == old_img.size:
                    new_base = self._crop_region(self._region, self._region_scale)
                    self._region_img = self._rebase_annotations(
                        old_img, old_base, new_base)
                    self.undo.map(
                        lambda im: self._rebase_annotations(
                            im, old_base, new_base))
                else:
                    self._init_region_img()
            else:
                # 尺寸变了（贴边被裁剪；移动中 _adjust_region 已重裁并清空标注）
                self._init_region_img()
            self._masks_hidden = False
            self._render_all()
            self.update()
            return
        if not self.drawing or self._region_img is None:
            self._preview = None
            self._draw_start_logical = None
            self.update()
            return
        self.drawing = False
        rx, ry = self._region_point(x, y)
        sx, sy = self.draw_start
        self._draw_start_logical = None
        s = self._region_scale
        self._preview = None
        if self.tool == "brush":
            # 画笔已在拖动中增量绘制；重置累计 patch 状态（下方统一整图转换）
            self._brush_moves = 0
            self._brush_bbox = None
        elif self.tool == "rect":
            draw_rect(self._region_img,
                      [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                      self.color, self.width_attr * s)
        elif self.tool == "ellipse":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                self._refresh_region_pixmap()
                self.update()
                return
            # 按住 Alt 约束为正圆
            ex0, ey0, ex1, ey1 = sx, sy, rx, ry
            if alt_pressed(event):
                ex0, ey0, ex1, ey1 = self._constrain_ellipse(sx, sy, rx, ry)
            draw_ellipse(self._region_img,
                         [min(ex0, ex1), min(ey0, ey1), max(ex0, ex1), max(ey0, ey1)],
                         self.color, self.width_attr * s)
        elif self.tool == "arrow":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                self._refresh_region_pixmap()
                self.update()
                return
            draw_arrow(self._region_img, (sx, sy), (rx, ry),
                       self.color, self.width_attr * s)
        elif self.tool == "mosaic":
            self._region_img = mosaic(
                self._region_img,
                min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry),
                block=24 * s)
        self._refresh_region_pixmap()
        self.update()
        # 标注完成后确保工具条保持在最前（topmost 标志已含在窗口标志中，
        # raise_ 只调整置顶层内 Z 序，不抢键盘焦点）
        if self.tb_win is not None:
            self.tb_win.raise_()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._confirm()

    def _set_preview(self, x0, y0, x1, y1):
        """设置标注预览（绝对坐标），paintEvent 负责绘制。"""
        l, t, r, b = self._region
        px0 = max(l, min(x0, x1))
        py0 = max(t, min(y0, y1))
        px1 = min(r, max(x0, x1))
        py1 = min(b, max(y0, y1))
        if px1 - px0 < 1 or py1 - py0 < 1:
            self._preview = None
            self.update()
            return
        if self.tool == "arrow":
            # 端点 clamp 到选区（与最终渲染的 _region_point 一致），
            # 预览不画出选区外；分别 clamp 两端，方向保持正确
            ax0, ay0 = max(l, min(x0, r)), max(t, min(y0, b))
            ax1, ay1 = max(l, min(x1, r)), max(t, min(y1, b))
            self._preview = {"tool": "arrow", "start": (ax0, ay0), "end": (ax1, ay1)}
        else:
            self._preview = {"tool": self.tool,
                             "box": (px0, py0, px1, py1)}
        self.update()

    def _update_cursor(self, x=None, y=None):
        if x is not None:
            if self._mode == "select" or self._region is None:
                cursor = Qt.CrossCursor
            else:
                hit = self._hit_test(x, y)
                if hit:
                    cursor = self._CURSORS[hit]
                else:
                    l, t, r, b = self._region
                    inside = l <= x <= r and t <= y <= b
                    cursor = (
                        self._CURSORS["move"]
                        if inside and self.tool == "move"
                        else Qt.CrossCursor
                    )
        else:
            cursor = Qt.CrossCursor
        self._set_cursor_shape(cursor)

    def _set_cursor_shape(self, shape):
        self._cursor = shape
        try:
            self.setCursor(shape)
        except Exception:
            pass

    def _constrain_ellipse(self, x0, y0, x1, y1):
        """按住 Alt 时约束椭圆为正圆：取横纵位移较大值作为边长。"""
        dx, dy = x1 - x0, y1 - y0
        d = max(abs(dx), abs(dy))
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        return (x0, y0, x0 + sx * d, y0 + sy * d)

