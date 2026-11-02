"""选集窗口：从合集 / 分P / 播放列表里勾选要下载的视频。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)


def _dur(sec):
    if not sec:
        return ''
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


class EpisodeDialog(QDialog):
    def __init__(self, col, existing_urls=(), parent=None):
        super().__init__(parent)
        self.col = col
        self.auto_download = False
        self.selected = []
        entries = col['entries']
        cur = col.get('current', -1)
        self.setWindowTitle('选集')
        self.resize(640, 620)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        head = QLabel(f'{col["kind"]}：{col.get("title") or "未命名"}　（共 {len(entries)} 个视频）')
        head.setObjectName('sectionTitle')
        head.setWordWrap(True)
        v.addWidget(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索标题…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        for text, slot in (('全选', lambda: self._set_all(True)), ('全不选', lambda: self._set_all(False)),
                           ('反选', self._invert), ('当前之后全部', self._after_current)):
            b = QPushButton(text)
            b.setObjectName('small')
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            if text == '当前之后全部' and cur < 0:
                b.setEnabled(False)
            bar.addWidget(b)
        bar.addStretch(1)
        self.count = QLabel()
        self.count.setObjectName('meta')
        bar.addWidget(self.count)
        v.addLayout(bar)

        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        width = len(str(len(entries)))
        existing = set(existing_urls)
        for i, e in enumerate(entries):
            mark = '　▶ 当前' if i == cur else ''
            have = e['url'] in existing or i == cur
            text = f'{i + 1:0{width}d}　{e["title"]}' + (f'　·　{_dur(e.get("duration"))}' if e.get('duration') else '') + mark
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, i)
            if have:
                # 已在解析列表里（包括当前这个）：不重复添加
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setToolTip('已经在解析结果里了')
            self.list.addItem(item)
        self.list.itemChanged.connect(lambda _: self._update_count())
        self.list.itemDoubleClicked.connect(self._toggle)
        v.addWidget(self.list, 1)

        foot = QHBoxLayout()
        self.auto = QCheckBox('解析完自动下载（按输入框下方「清晰度」的设置）')
        self.auto.setChecked(True)
        foot.addWidget(self.auto)
        foot.addStretch(1)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton('加入解析')
        self.ok.setObjectName('primary')
        self.ok.clicked.connect(self._accept)
        for b in (cancel, self.ok):
            b.setCursor(Qt.PointingHandCursor)
            foot.addWidget(b)
        v.addLayout(foot)
        if 0 <= cur < self.list.count():
            self.list.scrollToItem(self.list.item(cur), QListWidget.PositionAtCenter)
        self._update_count()

    def _items(self, visible_only=True):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not (it.flags() & Qt.ItemIsEnabled):
                continue
            if visible_only and it.isHidden():
                continue
            yield it

    def _set_all(self, on):
        self.list.blockSignals(True)
        for it in self._items():
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.list.blockSignals(False)
        self._update_count()

    def _invert(self):
        self.list.blockSignals(True)
        for it in self._items():
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
        self.list.blockSignals(False)
        self._update_count()

    def _after_current(self):
        cur = self.col.get('current', -1)
        self.list.blockSignals(True)
        for it in self._items(visible_only=False):
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) > cur else Qt.Unchecked)
        self.list.blockSignals(False)
        self._update_count()

    def _toggle(self, it):
        if it.flags() & Qt.ItemIsEnabled:
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _filter(self, text):
        text = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(text) and text not in it.text().lower())

    def _checked(self):
        return [self.col['entries'][it.data(Qt.UserRole)] for it in self._items(visible_only=False)
                if it.checkState() == Qt.Checked]

    def _update_count(self):
        sel = self._checked()
        total = sum(e.get('duration') or 0 for e in sel)
        self.count.setText(f'已选 {len(sel)} 个' + (f'，共 {_dur(total)}' if total else ''))
        self.ok.setText(f'加入解析（{len(sel)}）' if sel else '加入解析')
        self.ok.setEnabled(bool(sel))

    def _accept(self):
        self.selected = self._checked()
        self.auto_download = self.auto.isChecked()
        self.accept()
