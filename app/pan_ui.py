"""网盘部分的界面：解析页里的分享卡片、下载页里的批次卡片、扫码登录、验证码，以及主窗口里和网盘有关的逻辑（PanMixin）。

一个分享 = 解析页里一张卡片（打开分享 → 需要时输入提取码 → 勾选文件 → 下载）；打开成功就记一条解析记录
（历史记录页 + 网盘分区，点一下重新解析）——下载下来的网盘文件常被移走、解压，所以不记下载后的文件。
每点一次「下载」= 一个批次（网盘引擎里的一批任务），在卡片下方和「下载」页显示进度。
"""
import base64
import logging
import os
import subprocess

import shiboken6
from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSystemTrayIcon, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import APP_NAME, icons, theme, ui
from .folder_dialog import TextDialog
from .history import PAN
from .pan import (
    COMPLETED, FAILED, LOGIN_PROVIDERS, PAUSED, PROVIDERS, QUEUED, RUNNING, Batch, Engine, fmt_bytes, human_eta, now,
)
from .theme import c
from .ui import Card, HLine, IconButton, PlatformTag, ProgressLine, SelectButton, Spark
from .widgets import ElidedLabel

log = logging.getLogger(__name__)

LOGOS = {'baidu': 'brand_baidupan', 'quark': 'brand_quark', 'pan123': 'brand_123pan', 'mega': 'brand_mega'}

_EXT_ICONS = {}
for _icon, _exts in (('film', 'mp4 mkv avi mov wmv flv webm m4v ts rmvb rm mpg mpeg 3gp'),
                     ('music', 'mp3 flac wav aac m4a ogg wma ape opus'),
                     ('image', 'jpg jpeg png gif bmp webp heic tif tiff svg psd raw'),
                     ('cube', 'zip rar 7z tar gz bz2 xz iso exe msi apk dmg'),
                     ('book', 'pdf epub mobi azw3 djvu'),
                     ('code', 'py js ts go java c cpp h cs json xml html css sh bat')):
    for _e in _exts.split():
        _EXT_ICONS[_e] = _icon

TOKEN_HELP = {
    'pan123': ('在浏览器里登录 123云盘网页版，按 F12 打开开发者工具 → 应用（Application）→ 本地存储（Local Storage）→ '
               '选择 123pan 的网址 → 复制 authorToken 的值，粘贴到下面。', '粘贴 authorToken'),
    'baidu': ('在浏览器里登录百度网盘网页版，按 F12 打开开发者工具 → 应用（Application）→ Cookie → https://pan.baidu.com → '
              '复制 BDUSS 和 STOKEN 的值，按「BDUSS=…; STOKEN=…」的格式粘贴（只粘贴 BDUSS 的值也可以）。', 'BDUSS=…; STOKEN=…'),
    'quark': ('在浏览器里登录夸克网盘网页版，按 F12 打开开发者工具 → 网络（Network）→ 刷新页面后点任意一个发往 '
              'drive-pc.quark.cn 的请求 → 在请求头（Request Headers）里复制整段 Cookie，粘贴到下面。', '__pus=…; __puus=…; …'),
}


def file_icon(name, is_dir=False):
    if is_dir:
        return 'folder'
    ext = os.path.splitext(name)[1].lower().lstrip('.')
    return _EXT_ICONS.get(ext, 'file_text')


def reveal(path):
    """在资源管理器里显示文件（文件夹就直接打开）。"""
    if not path:
        return
    if os.path.isfile(path):
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
    elif os.path.isdir(path):
        os.startfile(path)
    else:
        parent = os.path.dirname(path)
        while parent and not os.path.isdir(parent) and os.path.dirname(parent) != parent:
            parent = os.path.dirname(parent)
        if parent and os.path.isdir(parent):
            os.startfile(parent)


def pixmap_from_data_uri(uri):
    pm = QPixmap()
    try:
        pm.loadFromData(base64.b64decode(uri.split(',', 1)[1]))
    except (IndexError, ValueError):
        pass
    return pm


def _label(text='', name='', wrap=False):
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def _button(text, slot=None, name='', tip='', icon=None):
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    if tip:
        b.setToolTip(tip)
    if icon:
        role = 'on_primary' if name == 'primary' else 'text2'
        b.setIcon(icons.icon(icon, c(role), 16))
        b._icon_name, b._icon_role = icon, role
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def _clear():
    w = QWidget()
    w.setObjectName('clear')
    return w


# ---------------------------------------------------------------- 小部件

class ProviderTile(QWidget):
    """卡片左边的网盘「封面」：浅灰圆角底 + 网盘 logo（单色，跟着主题变）。打开分享时显示转动的星芒。"""

    def __init__(self, provider, w=176, h=99, radius=10, parent=None):
        super().__init__(parent)
        self.provider, self.radius = provider, radius
        self.setFixedSize(w, h)
        self.spark = None

    def set_busy(self, on):
        if on and self.spark is None:
            self.spark = Spark(36, self)
            self.spark.move((self.width() - 36) // 2, (self.height() - 36) // 2)
        if self.spark is not None:
            self.spark.set_busy(on)
            self.spark.setVisible(on)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(c('border'), 1))
        p.setBrush(c('selected' if theme.is_dark() else 'subtle'))
        p.drawRoundedRect(r, self.radius, self.radius)
        if self.spark is not None and self.spark.isVisible():
            return
        s = min(self.height() * 0.42, 44)
        icons.paint(p, LOGOS.get(self.provider, 'folder'), QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s),
                    c('text3'))


class PanTree(QTreeWidget):
    """分享里的文件树：勾选文件 / 文件夹（勾文件夹 = 里面的全部文件），点开文件夹时才去读里面的内容。"""
    selection_changed = Signal()

    ENTRY, LOADED, LOADING = Qt.UserRole, Qt.UserRole + 1, Qt.UserRole + 2

    def __init__(self, card):
        super().__init__()
        self.card = card
        self.setObjectName('panTree')
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setIndentation(18)
        self.setIconSize(QSize(16, 16))
        self.setSelectionMode(QTreeWidget.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        hd = self.header()
        hd.setStretchLastSection(False)
        hd.setSectionResizeMode(0, QHeaderView.Stretch)
        hd.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.itemExpanded.connect(self._expand)
        self.itemChanged.connect(lambda *_: self.selection_changed.emit())

    def add_entries(self, parent, entries, state=Qt.Checked):
        self.blockSignals(True)
        for e in entries:
            it = QTreeWidgetItem([e['name'], '' if e.get('isDir') else fmt_bytes(e.get('size', -1))])
            it.setData(0, self.ENTRY, e)
            it.setToolTip(0, e.get('path') or e['name'])
            flags = Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
            if e.get('isDir'):
                flags |= Qt.ItemIsAutoTristate
                it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            it.setFlags(flags)
            it.setCheckState(0, state)
            it.setIcon(0, icons.icon(file_icon(e['name'], e.get('isDir')), c('text3'), 16))
            it.setTextAlignment(1, int(Qt.AlignRight | Qt.AlignVCenter))
            it.setForeground(1, c('text3'))
            if parent is None:
                self.addTopLevelItem(it)
            else:
                parent.addChild(it)
        self.blockSignals(False)
        self.fit_height()          # 展开的文件夹读完后，列表跟着变高
        self.selection_changed.emit()

    def _expand(self, item):
        e = item.data(0, self.ENTRY)
        if not e or not e.get('isDir') or item.data(0, self.LOADED) or item.data(0, self.LOADING):
            return
        item.setData(0, self.LOADING, True)
        item.setText(1, '读取中…')

        def done(res, err, it=item):
            if not shiboken6.isValid(self) or not shiboken6.isValid(it):
                return
            it.setData(0, self.LOADING, False)
            it.setText(1, '')
            if err:
                it.setExpanded(False)
                self.card.win.notify(f'读取文件夹失败：{err}', 'error')
                return
            it.setData(0, self.LOADED, True)
            state = it.checkState(0)
            if res:
                self.add_entries(it, res, Qt.Checked if state == Qt.Checked else Qt.Unchecked)
            else:
                it.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
                it.setText(1, '空文件夹')

        self.card.engine.call('listDir', {'sessionId': self.card.session, 'dirId': e['id']}, done)

    def picked(self):
        """勾选的条目（文件夹整个勾上时只返回文件夹本身，由引擎展开）。"""
        out = []

        def walk(parent):
            for i in range(parent.childCount()):
                it = parent.child(i)
                st = it.checkState(0)
                if st == Qt.Checked:
                    out.append(it.data(0, self.ENTRY))
                elif st == Qt.PartiallyChecked:
                    walk(it)
        walk(self.invisibleRootItem())
        return [e for e in out if e]

    def set_all(self, on):
        self.blockSignals(True)
        for i in range(self.topLevelItemCount()):
            self.topLevelItem(i).setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.blockSignals(False)
        self.selection_changed.emit()

    def all_checked(self):
        n = self.topLevelItemCount()
        return n > 0 and all(self.topLevelItem(i).checkState(0) == Qt.Checked for i in range(n))

    def fit_height(self):
        rows = 0

        def count(parent):
            nonlocal rows
            for i in range(parent.childCount()):
                rows += 1
                it = parent.child(i)
                if it.isExpanded():
                    count(it)
        count(self.invisibleRootItem())
        self.setFixedHeight(min(max(rows, 1), 10) * 30 + 12)


# ---------------------------------------------------------------- 解析页：一个网盘分享

class PanCard(Card):
    """解析页里的一个网盘分享。状态：opening 打开中 / password 要提取码 / error 出错 / list 文件列表。"""

    def __init__(self, win, link):
        super().__init__(radius=16)
        self.win, self.engine, self.link = win, win.pan, link
        self.provider = link.get('provider', '')
        self.state, self.error = 'opening', ''
        self.result, self.session = None, ''
        self.pwd = link.get('pwd') or ''
        self.pwd_label, self.pwd_wrong = '提取码', False
        self.req = None
        self.adding = False
        self.add_note = ''
        self.save_dir = win.pan_save_dir(self.provider)
        self.tree = None
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(14, 14, 16, 14)
        self.v.setSpacing(12)
        self.body = None
        self.lines = QVBoxLayout()      # 这个分享的下载进度（每点一次下载一行）
        self.lines.setSpacing(10)
        self.v.addLayout(self.lines)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self._menu(self.mapToGlobal(pos)))

    @property
    def key(self):
        return self.provider, self.link.get('id', '')

    @property
    def name(self):
        return PROVIDERS.get(self.provider, self.link.get('providerName') or '网盘')

    def share_url(self):
        """分享链接（有提取码时带上 pwd=，重新解析时不用再输入）。"""
        url = self.link.get('url', '')
        if self.pwd and self.provider != 'mega' and 'pwd=' not in url:
            url += ('&' if '?' in url else '?') + 'pwd=' + self.pwd
        return url

    # ---- 打开分享
    def open(self, pwd=None):
        if pwd is not None:
            self.pwd = pwd
        self.state, self.error = 'opening', ''
        self.render()
        if self.session:
            self.engine.call('closeShare', {'sessionId': self.session})
            self.session = ''
        self.req = self.engine.call('openShare', {'link': self.link, 'pwd': self.pwd}, self._on_open)

    def _on_open(self, res, err):
        self.req = None
        if not shiboken6.isValid(self):
            if res and res.get('sessionId'):
                self.engine.call('closeShare', {'sessionId': res['sessionId']})
            return
        if err:
            self.state, self.error = 'error', err
        elif res.get('needPassword'):
            self.state = 'password'
            self.pwd_label = res.get('passwordLabel') or '提取码'
            self.pwd_wrong = bool(res.get('wrongPassword'))
        else:
            self.state, self.result, self.session = 'list', res, res.get('sessionId', '')
            self.win.pan_record_parse(self)
        self.render()
        self.win._update_res_label()

    def close_share(self):
        if self.req:
            self.engine.cancel(self.req)
            self.req = None
        if self.session:
            self.engine.call('closeShare', {'sessionId': self.session})
            self.session = ''

    # ---- 界面
    def render(self):
        body = _clear()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(16)
        tile = ProviderTile(self.provider)
        top.addWidget(tile, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(7)
        top.addLayout(col, 1)
        v.addLayout(top)

        if self.state == 'list':
            self._render_list(v, col)
        else:
            title = ElidedLabel(self.link.get('url', ''), 'cardTitleDim')
            title.set_full_text(self.link.get('url', ''))
            col.addWidget(title)
            line = QHBoxLayout()
            line.setSpacing(8)
            line.addWidget(PlatformTag(self.name))
            act = QHBoxLayout()
            act.setSpacing(6)
            act.addStretch(1)
            if self.state == 'opening':
                tile.set_busy(True)
                line.addWidget(_label('正在打开分享…', 'meta'))
                line.addStretch(1)
                col.addLayout(line)
                col.addStretch(1)
                act.addWidget(_button('移除', self.remove, 'ghost'))
            elif self.state == 'password':
                if self.pwd_wrong:
                    ic = QLabel()
                    ic.setPixmap(icons.pixmap('alert', c('err'), 15))
                    line.addWidget(ic)
                    line.addWidget(_label(f'{self.pwd_label}不对，请重新输入', 'metaError'))
                else:
                    line.addWidget(_label(f'这个分享需要{self.pwd_label}', 'meta'))
                line.addStretch(1)
                col.addLayout(line)
                col.addStretch(1)
                edit = QLineEdit()
                edit.setPlaceholderText(self.pwd_label)
                edit.setMaxLength(64)
                edit.setFixedWidth(200)
                ok = _button('确定', None, 'primary')
                submit = lambda: edit.text().strip() and self.open(edit.text().strip())   # noqa: E731
                edit.returnPressed.connect(submit)
                ok.clicked.connect(submit)
                act.addWidget(_button('移除', self.remove, 'ghost'))
                act.addWidget(edit)
                act.addWidget(ok)
                QTimer.singleShot(0, lambda e=edit: shiboken6.isValid(e) and e.setFocus())
            else:
                ic = QLabel()
                ic.setPixmap(icons.pixmap('alert', c('err'), 15))
                line.addWidget(ic)
                err = ElidedLabel(self.error, 'metaError')
                err.set_full_text(self.error, self.error)
                line.addWidget(err, 1)
                col.addLayout(line)
                col.addStretch(1)
                act.addWidget(_button('移除', self.remove, 'ghost'))
                act.addWidget(_button('重试', lambda: self.open(), icon='refresh'))
            col.addLayout(act)
        self._set_body(body)

    def _render_list(self, v, col):
        res = self.result
        entries = res.get('entries') or []
        title = res.get('title') or (entries[0]['name'] if len(entries) == 1 else self.link.get('url', ''))
        t = ElidedLabel(title, 'cardTitle')
        t.set_full_text(title, f'{title}\n{self.link.get("url", "")}')
        col.addWidget(t)
        line = QHBoxLayout()
        line.setSpacing(6)
        line.addWidget(PlatformTag(self.name))
        files = [e for e in entries if not e.get('isDir')]
        dirs = len(entries) - len(files)
        size = sum(max(0, e.get('size') or 0) for e in files)
        bits = []
        if dirs:
            bits.append(f'{dirs} 个文件夹')
        if files:
            bits.append(f'{len(files)} 个文件' + (f'（{fmt_bytes(size)}）' if size else ''))
        line.addWidget(ElidedLabel(' · '.join(bits) or '分享是空的', 'meta'), 1)
        col.addLayout(line)
        note = res.get('downloadNote') or ''
        if note:
            nl = QHBoxLayout()
            nl.setSpacing(8)
            ic = QLabel()
            ic.setPixmap(icons.pixmap('alert', c('warn'), 15))
            nl.addWidget(ic, 0, Qt.AlignTop)
            msg = _label(note, 'meta', wrap=True)
            nl.addWidget(msg, 1)
            col.addLayout(nl)
            col.addStretch(1)
            act = QHBoxLayout()
            act.addStretch(1)
            if self.provider in LOGIN_PROVIDERS:
                act.addWidget(_button(f'扫码登录{self.name}', lambda: self.win.pan_login(self.provider, self.recheck),
                                      'primary', icon='user'))
            col.addLayout(act)
        else:
            col.addStretch(1)
        # 文件树（只有一个文件时不显示：标题里已经有文件名和大小）
        single = len(entries) == 1 and not entries[0].get('isDir')
        head_w = _clear()
        head = QHBoxLayout(head_w)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.all_box = QCheckBox('全选')
        head.addWidget(self.all_box)
        head.addStretch(1)
        self.sum_label = _label('', 'meta')
        head.addWidget(self.sum_label)
        v.addWidget(head_w)
        self.tree = PanTree(self)
        self.tree.add_entries(None, entries)
        self.tree.fit_height()
        self.tree.itemExpanded.connect(lambda *_: QTimer.singleShot(0, self._fit_tree))
        self.tree.itemCollapsed.connect(lambda *_: QTimer.singleShot(0, self._fit_tree))
        self.tree.selection_changed.connect(self._update_summary)
        self.all_box.clicked.connect(lambda on: self.tree.set_all(on))
        v.addWidget(self.tree)
        if single:
            head_w.hide()
            self.tree.hide()
        # 保存位置 + 下载
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.folder_btn = SelectButton('', 'folder')
        self.folder_btn.clicked.connect(self._folder_menu)
        foot.addWidget(self.folder_btn)
        foot.addStretch(1)
        self.add_label = _label('', 'metaBusy')
        foot.addWidget(self.add_label)
        more = IconButton('more', '更多', size=34, icon_size=18)
        more.clicked.connect(lambda: self._menu(more.mapToGlobal(QPoint(0, more.height() + 4))))
        foot.addWidget(more)
        self.dl_btn = _button('下载', self.download, 'primary', icon='download')
        foot.addWidget(self.dl_btn)
        v.addLayout(foot)
        self._update_folder_btn()
        self._update_summary()
        if note:
            self.dl_btn.setEnabled(False)
            self.dl_btn.setToolTip(note)

    def _fit_tree(self):
        if self.tree is not None and shiboken6.isValid(self.tree):
            self.tree.fit_height()

    def _set_body(self, w):
        if self.body is not None:
            self.v.removeWidget(self.body)
            for sp in self.body.findChildren(Spark):
                sp.set_busy(False)
            self.body.hide()
            self.body.setParent(None)
            self.body.deleteLater()
        self.body = w
        self.v.insertWidget(0, w)

    def _update_summary(self):
        if self.tree is None:
            return
        picked = self.tree.picked()
        files = [e for e in picked if not e.get('isDir')]
        dirs = len(picked) - len(files)
        size = sum(max(0, e.get('size') or 0) for e in files)
        parts = []
        if files:
            parts.append(f'{len(files)} 个文件' + (f'（{fmt_bytes(size)}）' if size else ''))
        if dirs:
            parts.append(f'{dirs} 个文件夹')
        self.sum_label.setText(('已选 ' + '、'.join(parts)) if parts else '还没有选择')
        self.all_box.setChecked(self.tree.all_checked())
        note = (self.result or {}).get('downloadNote')
        self.dl_btn.setEnabled(bool(picked) and not self.adding and not note)

    def _update_folder_btn(self):
        d = self.save_dir
        self.folder_btn.setText(os.path.basename(d.rstrip('\\/')) or d)
        self.folder_btn.setToolTip(f'保存到：{d}\n（会按分享里的文件夹结构保存）')

    def _folder_menu(self):
        m = ui.popup_menu(self.win)
        head = m.addAction(self.save_dir)
        head.setEnabled(False)
        m.addSeparator()
        m.addAction(icons.icon('folder', c('text2')), '打开文件夹', lambda: reveal(self.save_dir))
        m.addAction(icons.icon('settings', c('text2')), '更改保存位置…', self._choose_dir)
        m.exec(self.folder_btn.mapToGlobal(QPoint(0, self.folder_btn.height() + 4)))

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self.win, '选择保存位置', self.save_dir)
        if d:
            self.save_dir = os.path.normpath(d)
            self._update_folder_btn()

    def _menu(self, pos):
        m = ui.popup_menu(self.win)
        ic = lambda name: icons.icon(name, c('text2'))   # noqa: E731
        url = self.link.get('url', '')
        m.addAction(ic('copy'), '复制分享链接', lambda: QGuiApplication.clipboard().setText(self.share_url()))
        m.addAction(ic('external'), '在浏览器里打开', lambda: os.startfile(url) if url else None)
        if self.state == 'list':
            m.addAction(ic('refresh'), '重新读取', lambda: self.open())
        m.addSeparator()
        m.addAction(ic('trash'), '移除', self.remove)
        m.exec(pos)

    # ---- 登录后重新检查
    def recheck(self):
        if not self.session:
            self.open()
            return

        def done(note, err):
            if not shiboken6.isValid(self) or self.result is None:
                return
            if err:
                self.open()
                return
            self.result['downloadNote'] = note or ''
            self.render()
        self.engine.call('downloadNote', {'sessionId': self.session}, done)

    # ---- 下载
    def download(self):
        if self.tree is None or self.adding:
            return
        picked = self.tree.picked()
        if not picked:
            self.win.notify('还没有勾选要下载的文件', 'error')
            return
        self.adding = True
        self.dl_btn.setEnabled(False)
        self.add_label.setText('正在添加…')
        res = self.result or {}
        if len(picked) == 1:
            title = picked[0]['name']
        else:
            first = res.get('title') or picked[0]['name']
            title = f'{first} 等 {len(picked)} 项'
        save_dir = self.save_dir

        def done(r, err):
            if shiboken6.isValid(self):
                self.adding = False
                self.add_label.setText('')
                self._update_summary()
            if err:
                if '过期' in err and shiboken6.isValid(self):
                    self.win.notify('解析结果过期了，已重新打开分享，请再点一次下载', 'error')
                    self.open()
                else:
                    self.win.notify(f'添加失败：{err}', 'error')
                return
            added = r.get('added') or []
            errors = r.get('errors') or []
            if added:
                self.win.pan_new_batch(self if shiboken6.isValid(self) else None, self.provider, self.link, self.pwd,
                                       title, save_dir, added)
                self.win.notify(f'已开始下载 {len(added)} 个文件' + (f'，{len(errors)} 个没加进去' if errors else ''),
                                'ok' if not errors else 'error')
            else:
                self.win.notify('没有加入任何文件' + (f'：{errors[0]}' if errors else ''), 'error')
            for e in errors[:20]:
                log.warning('网盘文件加入失败：%s', e)

        self.req = self.engine.call('addShareTasks', {'sessionId': self.session, 'entries': picked, 'saveDir': save_dir},
                                    done)

    def on_add_progress(self, data):
        if self.adding and data.get('sessionId') == self.session and shiboken6.isValid(self.add_label):
            self.add_label.setText(f'正在读取文件夹：已读 {data.get("folders", 0)} 个，找到 {data.get("files", 0)} 个文件')

    def remove(self):
        self.win.pan_remove_card(self)


# ---------------------------------------------------------------- 一批网盘下载的进度

class PanBatchView(Card):
    """一批网盘下载。compact=True：解析页卡片里的一行进度；否则是「下载」页里的一张卡片（可以展开看每个文件）。"""

    MAX_FILES = 200

    def __init__(self, win, batch, compact):
        super().__init__(radius=14, fill=None if compact else 'surface', border=None if compact else 'border')
        self.win, self.batch, self.compact = win, batch, compact
        self.expanded = False
        self.b1 = IconButton('pause', '暂停', size=30, icon_size=17)
        self.b2 = IconButton('x', '删除任务', size=30, icon_size=17)
        self.b3 = IconButton('folder', '打开所在文件夹', size=30, icon_size=17)
        self.b1.clicked.connect(self._primary)
        self.b2.clicked.connect(self._secondary)
        self.b3.clicked.connect(lambda: reveal(self.batch.location(win.pan)))
        self.bar = ProgressLine(height=5 if compact else 6)
        self.status = _label('', 'metaStrong')
        self.detail = ElidedLabel('', 'meta')
        btns = QHBoxLayout()
        btns.setSpacing(2)
        for b in (self.b1, self.b2, self.b3):
            btns.addWidget(b)
        self.files_box = None
        if compact:
            v = QVBoxLayout(self)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            v.addWidget(HLine())
            top = QHBoxLayout()
            top.setSpacing(8)
            icon = QLabel()
            icon.setPixmap(icons.pixmap('download', c('text3'), 15))
            top.addWidget(icon)
            self.tag = _label('', 'metaStrong')
            top.addWidget(self.tag)
            top.addWidget(self.status)
            top.addWidget(self.detail, 1)
            top.addLayout(btns)
            v.addLayout(top)
            v.addWidget(self.bar)
        else:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(12, 12, 12, 12)
            outer.setSpacing(10)
            h = QHBoxLayout()
            h.setSpacing(14)
            h.addWidget(ProviderTile(batch.provider, 112, 63, 8), 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(6)
            self.name = ElidedLabel(batch.title, 'cardTitle')
            col.addWidget(self.name)
            line = QHBoxLayout()
            line.setSpacing(8)
            line.addWidget(PlatformTag(batch.provider_name))
            self.tag = ElidedLabel('', 'meta')
            line.addWidget(self.tag, 1)
            self.more_btn = IconButton('chevron_down', '查看每个文件', size=26, icon_size=15)
            self.more_btn.clicked.connect(self._toggle_files)
            line.addWidget(self.more_btn)
            col.addLayout(line)
            col.addWidget(self.bar)
            bottom = QHBoxLayout()
            bottom.setSpacing(8)
            bottom.addWidget(self.status)
            bottom.addWidget(self.detail, 1)
            col.addLayout(bottom)
            h.addLayout(col, 1)
            h.addLayout(btns)
            outer.addLayout(h)
            self.files_box = QVBoxLayout()
            self.files_box.setContentsMargins(126, 0, 0, 0)
            self.files_box.setSpacing(0)
            outer.addLayout(self.files_box)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: win.pan_batch_menu(self.batch, self.mapToGlobal(pos)))
        self.refresh()

    def mouseDoubleClickEvent(self, e):
        st = self.batch.stats(self.win.pan)
        if st['total'] and st['done'] == st['total']:
            path = self.batch.location(self.win.pan)
            if path and os.path.exists(path):
                os.startfile(path)

    def _primary(self):
        st = self.batch.stats(self.win.pan)
        if st['active']:
            self.win.pan_pause_batch(self.batch)
        elif st['paused'] or st['failed']:
            self.win.pan_resume_batch(self.batch)
        elif st['total'] and st['done'] == st['total']:
            path = self.batch.location(self.win.pan)
            if path and os.path.exists(path):
                os.startfile(path)
            else:
                self.win.notify('文件已被移动或删除', 'error')

    def _secondary(self):
        st = self.batch.stats(self.win.pan)
        if st['total'] and not st['active'] and st['done'] == st['total']:
            self.win.pan_clear_batch(self.batch)
        else:
            self.win.pan_delete_batch(self.batch)

    def _toggle_files(self):
        self.expanded = not self.expanded
        self.more_btn.set_icon('chevron_up' if self.expanded else 'chevron_down',
                               '收起' if self.expanded else '查看每个文件')
        self._render_files()

    def _render_files(self):
        if self.files_box is None:
            return
        while self.files_box.count():
            w = self.files_box.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not self.expanded:
            return
        order = {FAILED: 0, RUNNING: 1, QUEUED: 2, PAUSED: 3, COMPLETED: 4}
        tasks = sorted(self.batch.tasks(self.win.pan), key=lambda t: (order.get(t.get('status'), 5), t.get('name', '')))
        for t in tasks[:self.MAX_FILES]:
            self.files_box.addWidget(_FileRow(self.win, t))
        if len(tasks) > self.MAX_FILES:
            self.files_box.addWidget(_label(f'…还有 {len(tasks) - self.MAX_FILES} 个文件', 'meta'))

    def refresh(self):
        eng = self.win.pan
        b = self.batch
        st = b.stats(eng)
        total = st['total']
        if self.compact:
            self.tag.setText(f'{total} 个文件' if total else '网盘下载')
        else:
            bits = [f'{total} 个文件' if total else '']
            if st['size'] and not st['unknown_size']:
                bits.append(fmt_bytes(st['size']))
            cloud_text, _ = self.win.cloud_status(f'batch:{b.id}')
            if not cloud_text and getattr(self.win, '_cloud_flags', None) and b.id in self.win._cloud_flags:
                cloud_text = '下完后上传云盘'
            bits.append(cloud_text)
            self.tag.set_full_text(' · '.join(x for x in bits if x), b.save_dir)
        if not total:
            role, text = 'text3', '等待中' if not eng.alive else '任务已删除'
            self.bar.set_state(0, 'text4')
            self.status.setText(text)
            self.detail.set_full_text('')
            self.b1.setEnabled(False)
            self._style_status(role)
            return
        self.b1.setEnabled(True)
        if st['active']:
            running = [t for t in b.tasks(eng) if t.get('status') == RUNNING]
            phase = next((t.get('phase') for t in running if t.get('phase')), '')
            status = f'{st["progress"] * 100:.0f}%' + (f' · {phase}' if phase else '')
            role = 'brand'
            parts = [f'已完成 {st["done"]}/{total} 个文件']
            if st['size']:
                parts.append(f'{fmt_bytes(st["downloaded"])} / {fmt_bytes(st["size"])}')
            if st['speed']:
                parts.append(f'{fmt_bytes(st["speed"])}/s')
            if st['eta']:
                parts.append(f'剩余 {human_eta(st["eta"])}')
            self.bar.set_state(st['progress'], 'brand')
            self.b1.set_icon('pause', '暂停')
            self.b1.color_role = None
            self.b2.set_icon('x', '删除任务')
        elif st['failed']:
            status, role = f'{st["failed"]} 个文件失败', 'err'
            first = next((t for t in b.tasks(eng) if t.get('status') == FAILED), {})
            parts = [f'已完成 {st["done"]}/{total} 个文件', first.get('error', '')]
            self.bar.set_state(st['progress'], 'err')
            self.b1.set_icon('refresh', '重试失败的文件')
            self.b1.color_role = None
            self.b2.set_icon('x', '删除任务')
        elif st['paused']:
            status, role = '已暂停', 'text3'
            parts = [f'已完成 {st["done"]}/{total} 个文件', f'{fmt_bytes(st["downloaded"])} / {fmt_bytes(st["size"])}'
                     if st['size'] else '']
            self.bar.set_state(st['progress'], 'text4')
            self.b1.set_icon('play', '继续')
            self.b1.color_role = None
            self.b2.set_icon('x', '删除任务')
        else:
            status, role = '已完成', 'ok'
            loc = b.location(eng)
            parts = ['✓ 校验通过' if st['verified'] == total else '', f'已存到 {loc}' if loc else '']
            self.bar.set_state(1, 'ok')
            self.b1.set_icon('play_circle', '打开')
            self.b1.color_role = 'ok'
            self.b2.set_icon('trash', '从列表移除（不删文件）')
        self.status.setText(status)
        self._style_status(role)
        self.detail.set_full_text('  ·  '.join(p for p in parts if p), b.location(eng))
        if self.expanded:
            self._render_files()

    def _style_status(self, role):
        self.status.setStyleSheet(f'color: {c(role).name()};')


class _FileRow(QWidget):
    """批次展开后的一个文件：名字、状态，失败的可以单独重试。"""

    def __init__(self, win, t):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 3, 0, 3)
        h.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(file_icon(t.get('name', '')), c('text3'), 15))
        h.addWidget(ic)
        name = ElidedLabel(t.get('name', ''), 'meta')
        name.set_full_text(t.get('name', ''), t.get('filePath') or t.get('saveDir', ''))
        h.addWidget(name, 1)
        s = t.get('status')
        size = t.get('size') or 0
        if s == COMPLETED:
            text, role = '完成' + (f' · {fmt_bytes(size)}' if size > 0 else ''), 'ok'
        elif s == FAILED:
            text, role = '失败：' + (t.get('error') or ''), 'err'
        elif s == RUNNING:
            pct = (t.get('downloaded') or 0) / size * 100 if size > 0 else 0
            text, role = f'{pct:.0f}%' + (f' · {t["phase"]}' if t.get('phase') else ''), 'brand'
        elif s == PAUSED:
            text, role = '已暂停', 'text3'
        else:
            text, role = '等待中', 'text3'
        st = ElidedLabel(text, 'meta')
        st.setMaximumWidth(300)
        st.setStyleSheet(f'color: {c(role).name()};')
        st.set_full_text(text, t.get('error') or text)
        h.addWidget(st)
        if s == FAILED:
            b = IconButton('refresh', '重试', size=24, icon_size=14)
            b.clicked.connect(lambda: win.pan.call('resume', {'id': t['id']}))
            h.addWidget(b)
        elif s == COMPLETED and t.get('filePath'):
            b = IconButton('folder', '打开所在文件夹', size=24, icon_size=14)
            b.clicked.connect(lambda: reveal(t['filePath']))
            h.addWidget(b)


# ---------------------------------------------------------------- 扫码登录 / 验证码

class QRLoginDialog(QDialog):
    """网盘扫码登录：显示二维码，每 1.5 秒查一次扫码结果。exec() 返回真表示登录成功（账号信息在 self.account）。"""

    STATES = {'waiting': '等待扫码…', 'scanned': '已扫码，请在手机上确认登录', 'expired': '二维码已过期',
              'rejected': '已在手机上取消', 'confirmed': '登录成功'}

    def __init__(self, parent, engine, provider):
        super().__init__(parent)
        self.engine, self.provider = engine, provider
        self.name = PROVIDERS.get(provider, provider)
        self.qr_id = ''
        self.account = None
        self._req = None
        self.setWindowTitle(f'{self.name}扫码登录')
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 18)
        v.setSpacing(10)
        v.addWidget(_label(f'登录{self.name}', 'sectionTitle'))
        self.hint = _label('', 'rowDesc', wrap=True)
        v.addWidget(self.hint)
        self.img = QLabel()
        self.img.setFixedSize(232, 232)
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setStyleSheet('background: #ffffff; border-radius: 12px;')      # 二维码一直白底，深色模式也扫得出
        v.addWidget(self.img, 0, Qt.AlignHCenter)
        self.status = _label('正在获取二维码…', 'meta')
        self.status.setAlignment(Qt.AlignCenter)
        v.addWidget(self.status)
        note = _label('登录信息加密保存在本机（只有当前 Windows 用户能解开），只用于下载网盘分享。', 'hint', wrap=True)
        v.addWidget(note)
        btns = QHBoxLayout()
        token = _button('粘贴凭证登录…', self._token, 'ghost')
        btns.addWidget(token)
        btns.addStretch(1)
        self.refresh_btn = _button('刷新二维码', self.start, icon='refresh')
        self.refresh_btn.hide()
        btns.addWidget(self.refresh_btn)
        btns.addWidget(_button('取消', self.reject))
        v.addLayout(btns)
        self.setMinimumWidth(400)
        self.timer = QTimer(self, interval=1500)
        self.timer.timeout.connect(self._poll)
        self.start()

    def start(self):
        self.timer.stop()
        self.refresh_btn.hide()
        self.status.setText('正在获取二维码…')
        self.img.clear()

        def done(res, err):
            if not shiboken6.isValid(self):
                return
            if err:
                self.status.setText(f'获取二维码失败：{err}')
                self.refresh_btn.show()
                return
            self.qr_id = res.get('id', '')
            self.hint.setText(res.get('hint') or '请用手机 App 扫码')
            pm = pixmap_from_data_uri(res.get('image', ''))
            if not pm.isNull():
                self.img.setPixmap(pm.scaled(212, 212, Qt.KeepAspectRatio, Qt.FastTransformation))   # 二维码不要平滑，边缘清楚好扫
            self.status.setText(self.STATES['waiting'])
            self.timer.start()
        self.engine.call('startQR', {'provider': self.provider}, done)

    def _poll(self):
        if self._req is not None or not self.qr_id:
            return

        def done(res, err):
            self._req = None
            if not shiboken6.isValid(self) or not self.isVisible():
                return
            if err:
                self.status.setText(f'查询扫码结果失败：{err}')
                return
            state = res.get('state', '')
            self.status.setText(self.STATES.get(state, state))
            if state == 'confirmed':
                self.timer.stop()
                self.account = res.get('account')
                self.accept()
            elif state in ('expired', 'rejected'):
                self.timer.stop()
                self.refresh_btn.show()
        self._req = self.engine.call('pollQR', {'provider': self.provider, 'qrId': self.qr_id}, done)

    def _token(self):
        text, placeholder = TOKEN_HELP.get(self.provider, ('粘贴网页版的登录凭证。', ''))
        dlg = TextDialog(self, f'粘贴{self.name}登录凭证', '', hint='备用方式：' + text, placeholder=placeholder)
        if not dlg.exec() or not dlg.value:
            return
        self.status.setText('正在验证…')

        def done(res, err):
            if not shiboken6.isValid(self):
                return
            if err:
                self.status.setText(f'登录失败：{err}')
                return
            self.account = res
            self.timer.stop()
            self.accept()
        self.engine.call('loginToken', {'provider': self.provider, 'token': dlg.value}, done)

    def done(self, r):
        self.timer.stop()
        if self._req is not None:
            self.engine.cancel(self._req)
            self._req = None
        super().done(r)



class CaptchaDialog(QDialog):
    """网盘要求输入图片验证码（百度偶尔会要）。"""

    def __init__(self, parent, cap):
        super().__init__(parent)
        self.cap = cap
        self.code = ''
        name = PROVIDERS.get(cap.get('provider', ''), '网盘')
        self.setWindowTitle(f'{name}验证码')
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(10)
        v.addWidget(_label(f'{name}需要输入验证码', 'sectionTitle'))
        if cap.get('prompt'):
            v.addWidget(_label(cap['prompt'], 'rowDesc', wrap=True))
        img = QLabel()
        pm = pixmap_from_data_uri(cap.get('image', ''))
        if not pm.isNull():
            img.setPixmap(pm.scaledToHeight(min(120, max(40, pm.height())), Qt.SmoothTransformation))
        img.setAlignment(Qt.AlignCenter)
        v.addWidget(img)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText('图片里的字符')
        self.edit.setMaxLength(32)
        self.edit.returnPressed.connect(self.accept)
        v.addWidget(self.edit)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(_button('取消', self.reject))
        btns.addWidget(_button('提交', self.accept, 'primary'))
        v.addLayout(btns)
        self.setMinimumWidth(360)

    def accept(self):
        self.code = self.edit.text().strip()
        if self.code:
            super().accept()


# ---------------------------------------------------------------- 主窗口里的网盘逻辑

class PanMixin:
    """MainWindow 里和网盘有关的部分。需要主窗口提供：settings、history、notify、res_list、task_list、tasks_empty……"""

    def _pan_init(self):
        self.pan = Engine(self)
        self.pan_cards = {}          # (平台, 分享ID) → PanCard
        self.pan_batches = {}        # 批次 ID → Batch
        self.pan_views = {}          # 批次 ID → 下载页卡片
        self.pan_lines = {}          # 批次 ID → 解析页卡片里的进度行
        self._pan_captchas = set()
        self._pan_captcha_open = False
        self.pan.tasks_changed.connect(self._pan_on_tasks)
        self.pan.progress.connect(lambda _: self._pan_refresh_views())
        self.pan.add_progress.connect(self._pan_on_add_progress)
        self.pan.captcha.connect(self._pan_on_captcha)
        self.pan.failed.connect(lambda msg: self._append_log('error', f'网盘：{msg}'))
        self.pan.ready.connect(self._pan_on_ready)
        if self.history:
            for row in self.history.batches(('active', 'done')):
                self.pan_batches[row['id']] = Batch(row)
        QTimer.singleShot(300, lambda: self.pan.start(self.settings.download_dir))

    def _pan_on_ready(self, data):
        self._pan_apply_proxy()
        for b in sorted(self.pan_batches.values(), key=lambda x: x.ts):
            if b.state == 'done' and not getattr(self, '_pan_started', False):
                # 上次就已经全部下完的批次：和视频一样，重新打开软件后不再列在「下载」页（下载历史里还有）
                self.pan.call('removeMany', {'ids': list(b.task_ids), 'deleteFile': False})
                if self.history and b.id > 0:
                    self.history.update_batch(b.id, state='cleared')
                self.pan_batches.pop(b.id, None)
                continue
            self._pan_add_view(b)
        self._pan_started = True

    def _pan_apply_proxy(self):
        """网盘和视频用同一个代理设置（网盘部分重启软件后生效）。"""
        st = dict(self.pan.settings)
        proxy = (self.settings.proxy or '').strip()
        if st and st.get('proxy', '') != proxy:
            st['proxy'] = proxy
            self.pan.call('saveSettings', {'settings': st}, lambda r, e: r and self.pan.settings.update(r))

    def pan_save_dir(self, provider):
        """网盘文件保存到下载目录（按平台分文件夹时放进「百度网盘」这样的子文件夹）。"""
        d = self.settings.download_dir
        if getattr(self.settings, 'subfolder', 'none') != 'none':
            d = os.path.join(d, PROVIDERS.get(provider, provider))
        return d

    def pan_record_parse(self, card):
        """分享打开成功：记一条解析记录（历史记录页 + 网盘分区）。"""
        res = card.result or {}
        entries = res.get('entries') or []
        files = [e for e in entries if not e.get('isDir')]
        dirs = len(entries) - len(files)
        size = sum(max(0, e.get('size') or 0) for e in files)
        bits = [f'{dirs} 个文件夹' if dirs else '', f'{len(files)} 个文件' if files else '', fmt_bytes(size) if size else '']
        title = res.get('title') or (entries[0]['name'] if entries else card.link.get('url', ''))
        self._record_parse(PAN, f'pan:{card.provider}:{card.link.get("id", "")}', title, card.name, '', card.share_url(),
                           ' · '.join(b for b in bits if b), len(entries), size)

    # ---- 解析页
    def _pan_submit_links(self, links):
        """把识别到的网盘分享放进解析页（已经在列表里的不重复加）。返回新加的个数。"""
        n = 0
        for link in links:
            key = (link.get('provider', ''), link.get('id', ''))
            if key in self.pan_cards and shiboken6.isValid(self.pan_cards[key]):
                old = self.pan_cards[key]
                if old.state in ('error', 'password') and link.get('pwd') and link.get('pwd') != old.pwd:
                    old.open(link['pwd'])
                elif old.state == 'error':
                    old.open()
                self._pan_last_hit = old
                continue
            card = PanCard(self, link)
            self.pan_cards[key] = card
            self.res_list.insertWidget(n, card)
            card.open()
            n += 1
        if n:
            self._update_res_label()
        return n

    def pan_remove_card(self, card):
        card.close_share()
        self.pan_cards.pop(card.key, None)
        for bid, line in list(self.pan_lines.items()):
            if shiboken6.isValid(line) and line.parent() is card:
                self.pan_lines.pop(bid, None)
        self.res_list.removeWidget(card)
        for sp in card.findChildren(Spark):
            sp.set_busy(False)
        card.hide()
        card.setParent(None)
        card.deleteLater()
        self._update_res_label()

    def _pan_clear_cards(self):
        for card in list(self.pan_cards.values()):
            if shiboken6.isValid(card):
                self.pan_remove_card(card)
        self.pan_cards.clear()

    def _pan_on_add_progress(self, data):
        for card in self.pan_cards.values():
            if shiboken6.isValid(card):
                card.on_add_progress(data)

    # ---- 批次
    def pan_new_batch(self, card, provider, link, pwd, title, save_dir, added):
        ids = [t['id'] for t in added]
        for t in added:
            self.pan.tasks.setdefault(t['id'], t)
        url = card.share_url() if card is not None else link.get('url', '')
        row = {'id': None, 'provider': provider, 'share_id': link.get('id', ''), 'title': title, 'url': url,
               'save_dir': save_dir, 'task_ids': ids, 'ts': now(), 'state': 'active', 'record_id': None}
        if self.history:
            row['id'] = self.history.add_batch(provider, row['share_id'], title, url, save_dir, ids)
        else:
            row['id'] = -len(self.pan_batches) - 1
        b = Batch(row)
        self.pan_batches[b.id] = b
        if self.cloud_wanted():
            self.cloud_flag_batch(b.id)
        self._pan_add_view(b)
        if card is not None:
            line = PanBatchView(self, b, compact=True)
            card.lines.addWidget(line)
            self.pan_lines[b.id] = line
        self._refresh_status()

    def _pan_add_view(self, b):
        if b.id in self.pan_views:
            return
        view = PanBatchView(self, b, compact=False)
        self.task_list.insertWidget(0, view)
        self.pan_views[b.id] = view
        self.tasks_empty.hide()

    def _pan_drop_views(self, bid):
        for views in (self.pan_views, self.pan_lines):
            v = views.pop(bid, None)
            if v is not None and shiboken6.isValid(v):
                v.hide()
                v.setParent(None)
                v.deleteLater()
        self.tasks_empty.setVisible(not (self.task_views or self.pan_views))

    def _pan_refresh_views(self):
        for views in (self.pan_views, self.pan_lines):
            for bid, v in list(views.items()):
                if shiboken6.isValid(v):
                    v.refresh()
                else:
                    views.pop(bid, None)
        self._refresh_status()

    def _pan_on_tasks(self, updated, removed):
        self._pan_check_batches()
        self._pan_refresh_views()

    def _pan_check_batches(self):
        """每批的任务都结束（没有在下载或排队的）时，把下好的文件写进下载历史；全部完成时提醒。"""
        if not self.pan.alive or not self.pan.tasks_loaded:
            return
        for b in list(self.pan_batches.values()):
            if b.state != 'active':
                continue
            st = b.stats(self.pan)
            if st['total'] == 0:          # 任务都被删了
                b.state = 'done' if b.record_id else 'cleared'
                if self.history and b.id > 0:
                    self.history.update_batch(b.id, state=b.state)
                if b.state == 'cleared':
                    self._pan_drop_views(b.id)
                continue
            if st['active'] or st['paused']:
                continue
            if st['done'] == st['total']:
                b.state = 'done'
                if self.history and b.id > 0:
                    self.history.update_batch(b.id, state='done')
                self.notify(f'网盘下载完成：{b.title}', 'ok')
                self.cloud_batch_finished(b)
                if self.settings.notify_done and not self.isActiveWindow():
                    self._tray_message('网盘下载完成', f'{b.title}（{st["total"]} 个文件）')
                if self.settings.notify_sound:
                    try:
                        import winsound
                        winsound.MessageBeep(winsound.MB_ICONASTERISK)
                    except Exception:  # noqa: BLE001
                        QApplication.beep()
            elif st['failed'] and getattr(b, '_warned', 0) != st['failed']:
                b._warned = st['failed']
                self.notify(f'{b.title}：{st["failed"]} 个文件下载失败，可以在「下载」页重试', 'error')
                if self.settings.notify_done and not self.isActiveWindow():
                    self._tray_message('网盘下载失败', f'{b.title}\n{st["failed"]} 个文件失败', QSystemTrayIcon.Warning)

    def _pan_active_ids(self, b, statuses):
        return [t['id'] for t in b.tasks(self.pan) if t.get('status') in statuses]

    def pan_pause_batch(self, b):
        self.pan.call('pauseMany', {'ids': self._pan_active_ids(b, (RUNNING, QUEUED))})

    def pan_resume_batch(self, b):
        self.pan.call('resumeMany', {'ids': self._pan_active_ids(b, (PAUSED, FAILED))})

    def pan_retry_failed(self, b):
        self.pan.call('resumeMany', {'ids': self._pan_active_ids(b, (FAILED,))})

    def pan_clear_batch(self, b):
        """从下载列表里移除（文件保留）。"""
        ids = list(b.task_ids)
        self.pan.call('removeMany', {'ids': ids, 'deleteFile': False})
        b.state = 'done' if b.record_id else 'cleared'
        if self.history and b.id > 0:
            self.history.update_batch(b.id, state='cleared')
        self.pan_batches.pop(b.id, None)
        self._pan_drop_views(b.id)

    def pan_delete_batch(self, b, delete_files=None):
        st = b.stats(self.pan)
        if delete_files is None:
            box = QMessageBox(QMessageBox.Question, APP_NAME,
                              f'删除「{b.title}」的 {st["total"]} 个下载任务？\n没下完的会停止。', parent=self)
            keep = box.addButton('删除任务，保留已下载的文件', QMessageBox.AcceptRole)
            drop = box.addButton('连同已下载的文件一起删除', QMessageBox.DestructiveRole)
            box.addButton('取消', QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() not in (keep, drop):
                return
            delete_files = box.clickedButton() is drop
        self.pan.call('removeMany', {'ids': list(b.task_ids), 'deleteFile': bool(delete_files)})
        if self.history and b.id > 0:
            self.history.update_batch(b.id, state='cleared')
        self.pan_batches.pop(b.id, None)
        self._pan_drop_views(b.id)

    def pan_batch_menu(self, b, pos):
        m = ui.popup_menu(self)
        ic = lambda name: icons.icon(name, c('text2'))   # noqa: E731
        st = b.stats(self.pan)
        loc = b.location(self.pan)
        a = m.addAction(ic('folder'), '打开所在文件夹', lambda: reveal(loc))
        a.setEnabled(bool(loc))
        m.addSeparator()
        if st['active']:
            m.addAction(ic('pause'), '暂停', lambda: self.pan_pause_batch(b))
        if st['paused']:
            m.addAction(ic('play'), '继续', lambda: self.pan_resume_batch(b))
        if st['failed']:
            m.addAction(ic('refresh'), f'重试失败的 {st["failed"]} 个文件', lambda: self.pan_retry_failed(b))
        m.addAction(ic('copy'), '复制分享链接', lambda: QGuiApplication.clipboard().setText(b.url))
        m.addAction(ic('refresh'), '重新解析这个分享', lambda: self._submit_text(b.url))
        if st['done']:
            self.cloud_menu(m, b.done_paths(self.pan), lambda: self.cloud_upload_batch(b, manual=True))
            if self.cloud and (self.cloud.group_state(f'batch:{b.id}') or ('',))[0] == 'failed':
                m.addAction(ic('refresh'), '重试上传到云盘', lambda: self.cloud_retry(f'batch:{b.id}'))
        m.addSeparator()
        if st['total'] and not st['active'] and st['done'] == st['total']:
            m.addAction(ic('x'), '从列表移除（不删文件）', lambda: self.pan_clear_batch(b))
        m.addAction(ic('trash'), '删除任务…', lambda: self.pan_delete_batch(b))
        m.exec(pos)

    def _pan_clear_finished(self):
        """「清除已结束」：全部下完的批次从列表移除（文件保留）。"""
        for b in list(self.pan_batches.values()):
            st = b.stats(self.pan)
            if st['total'] and not st['active'] and not st['paused'] and st['done'] == st['total']:
                self.pan_clear_batch(b)

    def _pan_counts(self):
        out = {'running': 0, 'queued': 0, 'done': 0, 'failed': 0, 'paused': 0}
        for t in self.pan.tasks.values():
            s = t.get('status')
            key = {RUNNING: 'running', QUEUED: 'queued', COMPLETED: 'done', FAILED: 'failed', PAUSED: 'paused'}.get(s)
            if key:
                out[key] += 1
        return out

    # ---- 登录
    def pan_login(self, provider, after=None):
        if not self.pan.alive:
            self.notify(self.pan.error or '网盘解析组件没有运行', 'error')
            return False
        dlg = QRLoginDialog(self, self.pan, provider)
        if dlg.exec():
            acc = dlg.account or {}
            who = acc.get('nickname') or ''
            vip = acc.get('vip') or ''
            self.notify(f'已登录{PROVIDERS.get(provider, provider)}' + (f'：{who}' if who else '') +
                        (f'（{vip}）' if vip else ''), 'ok')
            if self.settings_panel is not None and hasattr(self.settings_panel, 'refresh_pan_accounts'):
                self.settings_panel.refresh_pan_accounts()
            for card in self.pan_cards.values():
                if shiboken6.isValid(card) and card.provider == provider and card.state == 'list':
                    card.recheck()
            if after:
                after()
            return True
        return False

    # ---- 验证码
    def _pan_on_captcha(self):
        if self._pan_captcha_open:
            return

        def got(res, err):
            if err or not res:
                return
            for cap in res:
                if cap['id'] in self._pan_captchas:
                    continue
                self._pan_captchas.add(cap['id'])
                self._pan_captcha_open = True
                self._show_from_tray()
                dlg = CaptchaDialog(self, cap)
                ok = dlg.exec()
                self._pan_captcha_open = False
                if ok:
                    self.pan.call('submitCaptcha', {'id': cap['id'], 'code': dlg.code})
                else:
                    self.pan.call('cancelCaptcha', {'id': cap['id']})
                QTimer.singleShot(200, self._pan_on_captcha)      # 期间可能又来了新的
                return
        self.pan.call('pendingCaptchas', None, got)
