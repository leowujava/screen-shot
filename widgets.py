"""选区辅助窗口：工具条窗口与 OCR 结果弹窗。

从 selector.py 拆出。两者都不依赖 Selector 的具体实现：
- _Toolbar 只把按键转发给传入的 selector（Esc / Enter / Ctrl+Z）；
- _OcrResultDialog 自包含（文本展示 + 剪贴板复制）。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class _Toolbar(QWidget):
    """选区工具条：独立置顶无边框窗口。

    键盘（Esc / Enter / Ctrl+Z）与 overlay 保持一致；
    closeEvent 被忽略，工具条的显示/隐藏完全由 Selector 管理。
    """

    def __init__(self, selector):
        super().__init__()
        self._selector = selector

    def keyPressEvent(self, e):
        sel = self._selector
        if sel is None:  # selector 已清理：按键直接忽略，避免事件晚到时崩溃
            return
        k = e.key()
        if k == Qt.Key_Escape:
            sel._cancel()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            sel._confirm()
        elif k == Qt.Key_Z and (e.modifiers() & Qt.ControlModifier):
            sel._undo()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        e.ignore()


class _OcrResultDialog(QDialog):
    """OCR 结果弹窗：只读文本可选中，支持复制全部 / 复制选中 / Ctrl+C。"""

    def __init__(self, text, parent):
        super().__init__(parent)
        self.setWindowTitle("识别文字")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_edit.setText(text)
        layout.addWidget(self.text_edit)

        bar = QHBoxLayout()

        def copy_selected():
            sel = self.text_edit.selectedText()
            if sel:
                from capture import copy_text_to_clipboard
                if copy_text_to_clipboard(sel):
                    self.accept()

        def copy_all():
            from capture import copy_text_to_clipboard
            if copy_text_to_clipboard(text):
                self.accept()

        btn_sel = QPushButton("复制选中")
        btn_sel.clicked.connect(copy_selected)
        btn_all = QPushButton("复制全部")
        btn_all.clicked.connect(copy_all)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        bar.addStretch(1)
        bar.addWidget(btn_sel)
        bar.addWidget(btn_all)
        bar.addWidget(btn_close)
        layout.addLayout(bar)

    def keyPressEvent(self, e):
        # Ctrl+C 复制选中（capture.copy_text_to_clipboard，与剪贴板行为一致）
        if e.key() == Qt.Key_C and (e.modifiers() & Qt.ControlModifier):
            sel = self.text_edit.selectedText()
            if sel:
                from capture import copy_text_to_clipboard
                copy_text_to_clipboard(sel)
            return
        super().keyPressEvent(e)
