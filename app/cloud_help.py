"""谷歌云盘的使用教程与常见问题（设置 → 谷歌云盘 →「使用教程」）。

每个人要用自己的 Google Cloud 项目创建一个「桌面应用」密钥：软件用这个密钥向谷歌申请访问你的云盘。
免费，只需要做一次，约 10 分钟。
"""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .cloud_guide import FAQ, INTRO, STEPS

def _label(text, name, wrap=True):
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


class CloudHelpDialog(QDialog):
    def __init__(self, parent, notify=None):
        super().__init__(parent)
        self.notify = notify or (lambda text, kind='info': None)
        self.setWindowTitle('谷歌云盘 · 使用教程')
        self.resize(720, 760)
        from .gui import scroll_area
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 22, 28, 24)
        v.setSpacing(6)
        v.addWidget(_label('谷歌云盘 · 使用教程', 'pageTitle'))
        v.addWidget(_label(INTRO, 'rowDesc'))
        v.addSpacing(10)
        for i, (title, desc, url) in enumerate(STEPS, 1):
            v.addSpacing(10)
            v.addWidget(_label(f'第 {i} 步  {title}', 'rowLabel'))
            v.addWidget(_label(desc, 'rowDesc'))
            if url:
                v.addLayout(self._link_row(url))
        v.addSpacing(26)
        v.addWidget(_label('常见问题', 'sectionTitle'))
        for q, a in FAQ:
            v.addSpacing(10)
            v.addWidget(_label(q, 'rowLabel'))
            v.addWidget(_label(a, 'rowDesc'))
        v.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area(body))

    def _link_row(self, url):
        h = QHBoxLayout()
        h.setContentsMargins(0, 4, 0, 0)
        h.setSpacing(6)
        link = _label(url, 'meta', wrap=False)
        h.addWidget(link)
        copy = QPushButton('复制')
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(url), self._copied(copy)))
        openb = QPushButton('打开')
        openb.setCursor(Qt.PointingHandCursor)
        openb.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        h.addWidget(copy)
        h.addWidget(openb)
        h.addStretch(1)
        return h

    @staticmethod
    def _copied(btn):
        btn.setText('已复制')
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: btn.setText('复制'))


def show_help(parent, notify=None):
    dlg = CloudHelpDialog(parent, notify)
    dlg.exec()
