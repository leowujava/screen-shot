import threading
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFileDialog, QLabel, QHBoxLayout, QMainWindow, QMessageBox, QPushButton, QVBoxLayout, QWidget

from capture import copy_to_clipboard
from drawing import get_ocr_engine, ocr_result_to_text


class EditorWindow(QMainWindow):
    """Simple PySide6-based screenshot editor window."""

    # OCR 线程 → 主线程（跨线程信号自动排队投递到主线程）
    _ocr_text_ready = Signal(str)
    _ocr_failed = Signal(str)

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = image.copy()
        self.setWindowTitle("screen-shot 编辑器")
        self.resize(max(640, self.image.width + 80), max(420, self.image.height + 120))

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        self.save_btn = QPushButton("保存")
        self.copy_btn = QPushButton("复制")
        self.ocr_btn = QPushButton("识别文字")
        self.close_btn = QPushButton("关闭")
        for button in (self.save_btn, self.copy_btn, self.ocr_btn, self.close_btn):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background:#1f2228;padding:20px;border:1px solid #3d4550;")
        layout.addWidget(self.label)

        self.save_btn.clicked.connect(self.save_image)
        self.copy_btn.clicked.connect(self.copy_image)
        self.ocr_btn.clicked.connect(self.ocr)
        self.close_btn.clicked.connect(self.close)

        self._ocr_text_ready.connect(self._show_ocr_result)
        self._ocr_failed.connect(self._on_ocr_failed)
        self._ocr_busy = False  # OCR 进行中：忽略连点，避免叠开多个结果弹窗

        self._refresh_preview()

    def _refresh_preview(self):
        pixmap = QPixmap.fromImage(self._pil_to_qimage(self.image))
        self.label.setPixmap(
            pixmap.scaled(
                self.label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    @staticmethod
    def _pil_to_qimage(img):
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        # QImage 不拷贝传入缓冲：必须 copy() 深拷贝后再返回，
        # 否则 data 被 GC 后 QImage 指向已释放内存（悬垂缓冲）。
        return QImage(data, img.width, img.height,
                      QImage.Format_RGBA8888).copy()

    def save_image(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存截图",
            f"截图_{datetime.now():%Y%m%d_%H%M%S}.png",
            "PNG Files (*.png);;JPEG Files (*.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            if path.lower().endswith((".jpg", ".jpeg")):
                self.image.convert("RGB").save(path)
            else:
                self.image.save(path)
            QMessageBox.information(self, "保存成功", f"已保存到：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def copy_image(self):
        try:
            copy_to_clipboard(self.image)
            QMessageBox.information(self, "复制成功", "已复制到剪贴板")
        except Exception as exc:
            QMessageBox.critical(self, "复制失败", str(exc))

    def ocr(self):
        """识别图中文字（工作线程执行，避免 OCR 期间界面卡死）。

        引擎懒加载（首次会读 OCR 模型）也在工作线程执行。
        结果通过跨线程信号排队回主线程弹窗，与选区工具行为一致。
        进行中禁用按钮并忽略连点。
        """
        if self._ocr_busy:
            return
        self._ocr_busy = True
        self.ocr_btn.setEnabled(False)
        img = self.image.copy()

        def run():
            try:
                engine = get_ocr_engine()
                if engine is None:
                    self._ocr_failed.emit("未安装 OCR 引擎。")
                    return
                import numpy as np
                result = engine(np.array(img.convert("RGB")))
                self._ocr_text_ready.emit(ocr_result_to_text(result))
            except Exception as exc:
                self._ocr_failed.emit(str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _ocr_done(self):
        """OCR 结束（成功/失败共用）：复位进行中状态，恢复按钮。"""
        self._ocr_busy = False
        self.ocr_btn.setEnabled(True)

    def _show_ocr_result(self, text):
        self._ocr_done()
        if not text:
            QMessageBox.information(self, "识别文字", "未识别到文字。")
        else:
            QMessageBox.information(self, "识别文字", text)

    def _on_ocr_failed(self, msg):
        self._ocr_done()
        QMessageBox.critical(self, "识别失败", msg)
