"""搜索面板（像 Claude 的搜索）：盖在窗口上，输入框 + 结果列表，搜解析记录（视频和网盘分享，点一下重新解析）、下载好的视频和分区。
↑↓ 选择，Enter 打开，Esc 关闭；右键结果有完整菜单（重命名、移至分区等）。"""
import os

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from . import icons, ui
from .theme import c
from .widgets import ElidedLabel


class _Row(QWidget):
    activated = Signal()
    menu = Signal(QPoint)

    def __init__(self, icon, title, meta):
        super().__init__()
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(54)
        self.selected = False
        self.icon_name = icon
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 6, 14, 6)
        h.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        h.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = ElidedLabel(title, 'rowLabel')
        col.addWidget(t)
        if meta:
            col.addWidget(ElidedLabel(meta, 'meta'))
        h.addLayout(col, 1)
        self.refresh_icon()

    def refresh_icon(self):
        self.icon.setPixmap(icons.pixmap(self.icon_name, c('text2'), 20))

    def set_selected(self, on):
        self.selected = on
        self.update()

    def paintEvent(self, e):
        if self.selected or self.underMouse():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(c('selected' if self.selected else 'hover'))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(4, 1, -4, -1), 10, 10)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.activated.emit()
        elif e.button() == Qt.RightButton:
            self.menu.emit(e.globalPosition().toPoint())


class SearchPanel(ui.Overlay):
    def __init__(self, host, win):
        super().__init__(host, 680, 560)
        self.win = win
        self.rows = []
        self.sel = -1
        v = QVBoxLayout(self.panel)
        v.setContentsMargins(6, 6, 6, 8)
        v.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(14, 6, 6, 6)
        top.setSpacing(10)
        self.search_icon = QLabel()
        top.addWidget(self.search_icon)
        self.edit = QLineEdit()
        self.edit.setObjectName('searchBig')
        self.edit.setPlaceholderText("搜索解析记录、下载好的视频和分区…")
        self.edit.installEventFilter(self)
        top.addWidget(self.edit, 1)
        x = ui.IconButton('x', '关闭（Esc）', size=34, icon_size=18)
        x.clicked.connect(self.close_panel)
        top.addWidget(x)
        v.addLayout(top)
        v.addWidget(ui.HLine())
        body = QWidget()
        body.setObjectName('clear')
        self.list = QVBoxLayout(body)
        self.list.setContentsMargins(0, 6, 0, 6)
        self.list.setSpacing(0)
        from .gui import scroll_area
        self.scroll = scroll_area(body)
        v.addWidget(self.scroll, 1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.refresh)
        self.edit.textChanged.connect(lambda _: self.timer.start())

    def _layout(self):
        self.setGeometry(self.host.rect())
        w = min(self.pref[0], self.width() - 48)
        h = min(self.pref[1], self.height() - 96)
        self.panel.setGeometry((self.width() - w) // 2, max(24, int(self.height() * 0.1)), w, h)

    def open_search(self):
        self.edit.clear()
        self.search_icon.setPixmap(icons.pixmap('search', c('text3'), 20))
        self.refresh()
        self.open()
        self.edit.setFocus()

    def _header(self, text):
        lab = QLabel(text)
        lab.setObjectName('sideSection')
        lab.setContentsMargins(8, 8, 0, 4)
        self.list.addWidget(lab)

    def refresh(self):
        while self.list.count():
            item = self.list.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.rows, self.sel = [], -1
        q = self.edit.text().strip()
        hist = self.win.history
        if not hist:
            return
        sections = {s['id']: s for s in hist.sections()}
        if q:
            hits = [s for s in sections.values() if q.lower() in s['name'].lower()]
            if hits:
                self._header('分区')
                for s in hits:
                    unit = '个视频' if s['kind'] == 'video' else '个网盘分享'
                    self._add(s['icon'], s['name'], f'{s["count"]} {unit}', lambda sid=s['id']: self._open_section(sid))
        # 解析记录：点一下重新解析
        parses = hist.parses(q, limit=40 if q else 20)
        if parses:
            self._header('解析记录' if q else '最近解析')
            for rec in parses:
                sec = sections.get(rec['section_id'])
                meta = '  ·  '.join(x for x in ('网盘分享' if rec['kind'] == 'pan' else rec['platform'], rec['info'] or '',
                                                 sec['name'] if sec and rec['kind'] == 'pan' else '', self.win.ago(rec['ts'])) if x)
                icon = sec['icon'] if sec else ('folder' if rec['kind'] == 'pan' else 'film')
                row = self._add(icon, rec['title'] or rec['url'], meta + '  ·  点击重新解析', lambda r=rec: self._reparse(r))
                row.menu.connect(lambda pos, r=rec: self.win._parse_menu(r, pos, after=self.refresh))
        # 下载好的视频：点一下打开
        recs = hist.recent(q, limit=30, kind='video') if q else []
        if recs:
            self._header('已下载的视频')
            for h in recs:
                sec = sections.get(h['section_id'])
                exists = bool(h['filepath']) and os.path.exists(h['filepath'])
                meta = '  ·  '.join(x for x in ((h['quality'] or '').replace('] [', ' · '), sec['name'] if sec else h['platform'],
                                                 self.win.ago(h['ts']), '' if exists else '文件已不在原位置') if x)
                row = self._add(sec['icon'] if sec else 'film', h['title'] or h['url'], meta,
                                lambda rec=h: self._open_record(rec))
                row.menu.connect(lambda pos, rec=h: self.win._item_menu(rec, pos, after=self.refresh))
        if not parses and not recs:
            empty = QLabel('没有找到相关的记录' if q else '还没有解析记录')
            empty.setObjectName('hint')
            empty.setAlignment(Qt.AlignCenter)
            empty.setContentsMargins(0, 30, 0, 30)
            self.list.addWidget(empty)
        self.list.addStretch(1)
        self._select(0 if self.rows else -1)

    def _add(self, icon, title, meta, slot):
        row = _Row(icon, title, meta)
        row.activated.connect(slot)
        self.list.addWidget(row)
        self.rows.append((row, slot))
        return row

    def _select(self, i):
        if 0 <= self.sel < len(self.rows):
            self.rows[self.sel][0].set_selected(False)
        self.sel = i
        if 0 <= i < len(self.rows):
            self.rows[i][0].set_selected(True)
            self.scroll.ensureWidgetVisible(self.rows[i][0], 0, 30)

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Down, Qt.Key_Up) and self.rows:
                self._select((self.sel + (1 if k == Qt.Key_Down else -1)) % len(self.rows))
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter) and 0 <= self.sel < len(self.rows):
                self.rows[self.sel][1]()
                return True
            if k == Qt.Key_Escape:
                self.close_panel()
                return True
        return super().eventFilter(obj, ev)

    def _open_record(self, h):
        self.close_panel()
        self.win._open_recent(h)

    def _reparse(self, rec):
        self.close_panel()
        self.win._reparse_record(rec)

    def _open_section(self, sid):
        self.close_panel()
        self.win._show_section(sid)
