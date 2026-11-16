"""分区相关的小窗口：新建 / 编辑分区（名称 + 从图标库选图标）、重命名。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from .icons import SECTION_ICONS
from .ui import IconButton


def _label(text, name):
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(True)
    return lab


def _buttons(dialog, ok_text):
    h = QHBoxLayout()
    h.addStretch(1)
    cancel = QPushButton('取消')
    cancel.setCursor(Qt.PointingHandCursor)
    cancel.clicked.connect(dialog.reject)
    ok = QPushButton(ok_text)
    ok.setObjectName('primary')
    ok.setCursor(Qt.PointingHandCursor)
    ok.setDefault(True)
    ok.clicked.connect(dialog.accept)
    h.addWidget(cancel)
    h.addWidget(ok)
    return h, ok


class FolderDialog(QDialog):
    """新建 / 编辑分区。exec() 返回真时，结果在 self.name、self.icon。"""

    def __init__(self, parent=None, title='新建分区', name='', icon='folder', icon_editable=True,
                 hidden=None, on_restore=None):
        """hidden：从侧边栏移除过的分区（新建时在标题右边显示「已移除 N」，点开可以放回）；on_restore(s) 放回一个"""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.name, self.icon = name, icon if icon in SECTION_ICONS else 'folder'
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(_label(title, 'sectionTitle'), 1)
        self._hidden, self._on_restore = list(hidden or []), on_restore
        self.restore_btn = QPushButton()
        self.restore_btn.setObjectName('link')
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.clicked.connect(self._restore_menu)
        head.addWidget(self.restore_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._update_restore()
        v.addLayout(head)
        v.addWidget(_label('分区名称', 'rowDesc'))
        self.edit = QLineEdit(name)
        self.edit.setPlaceholderText('例如：收藏、学习、音乐、动漫')
        self.edit.setMaxLength(20)
        v.addWidget(self.edit)
        if icon_editable:
            v.addSpacing(6)
            v.addWidget(_label('图标', 'rowDesc'))
            grid = QGridLayout()
            grid.setSpacing(6)
            self.group = QButtonGroup(self)
            for i, key in enumerate(SECTION_ICONS):
                b = IconButton(key, '', size=40, icon_size=20, radius=10)
                b.setCheckable(True)
                b.setChecked(key == self.icon)
                b.clicked.connect(lambda _=False, k=key: setattr(self, 'icon', k))
                self.group.addButton(b)
                grid.addWidget(b, i // 10, i % 10)
            v.addLayout(grid)
        v.addSpacing(8)
        row, self.ok = _buttons(self, '保存' if name else '创建')
        v.addLayout(row)
        self.edit.textChanged.connect(lambda t: self.ok.setEnabled(bool(t.strip())))
        self.ok.setEnabled(bool(name.strip()))
        self.edit.setFocus()

    def _update_restore(self):
        self.restore_btn.setText(f'已移除 {len(self._hidden)}')
        self.restore_btn.setToolTip('从侧边栏移除过的分区，点开可以放回')
        self.restore_btn.setVisible(bool(self._hidden) and self._on_restore is not None)

    def _restore_menu(self):
        from . import icons, ui
        from .theme import c
        m = ui.popup_menu(self)
        head = m.addAction('点一下放回侧边栏')
        head.setEnabled(False)
        for s in self._hidden:
            m.addAction(icons.icon(s['icon'], c('text2')), s['name'], lambda s=s: self._restore(s))
        m.exec(self.restore_btn.mapToGlobal(self.restore_btn.rect().bottomLeft()))

    def _restore(self, s):
        self._hidden.remove(s)
        self._on_restore(s)
        self._update_restore()

    def accept(self):
        self.name = self.edit.text().strip()
        if self.name:
            super().accept()


class TextDialog(QDialog):
    """输入一行文字（重命名用）。exec() 返回真时，结果在 self.value。"""

    def __init__(self, parent, title, text='', hint='', placeholder=''):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.value = text
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(10)
        v.addWidget(_label(title, 'sectionTitle'))
        if hint:
            v.addWidget(_label(hint, 'rowDesc'))
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.selectAll()
        v.addWidget(self.edit)
        v.addSpacing(6)
        row, self.ok = _buttons(self, '保存')
        v.addLayout(row)

    def accept(self):
        self.value = self.edit.text().strip()
        super().accept()
