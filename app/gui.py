import datetime
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import weakref
from logging.handlers import RotatingFileHandler

import shiboken6
from PySide6.QtCore import QByteArray, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal, QObject
from PySide6.QtGui import (
    QAction, QActionGroup, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QPainter,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from . import APP_NAME, APP_VERSION, avatar, bilibili, core, greetings, icons, pan, task_store, theme, ui
from .bili_dialog import BiliLoginDialog
from .config import DATA, ROOT, Settings, ffmpeg_dir
from .episode_dialog import EpisodeDialog
from .folder_dialog import FolderDialog, TextDialog
from .pan_ui import PanMixin
from .search_panel import SearchPanel
from .history import PAN, VIDEO, History, video_key
from .hotkey import GlobalHotkey
from .section_dialog import SectionDialog
from .settings_dialog import SettingsPanel
from .theme import c
from .ui import Card, Chip, Combo, Composer, HLine, IconButton, NavItem, PlatformTag, ProgressLine, SelectButton, Spark
from .widgets import CoverDialog, CoverView, ElidedLabel, ThumbLoader, cover_url, save_cover_bytes

log = logging.getLogger('app')

GITHUB_URL = 'https://github.com/MoyerZhang-Claude/video-downloader'

# 输入框下方的清晰度预设：(键, 名称, 取值, 说明)
PRESETS = [('pick', '逐个选择', None, '每个视频默认最高画质，可在卡片里单独改'),
           ('best', '全部最高画质', 'best', ''), ('1080', '全部 1080p', 1080, '没有时取更低一档'),
           ('720', '全部 720p', 720, ''), ('480', '全部 480p', 480, ''), ('360', '全部 360p', 360, ''),
           ('mp3', '全部仅音频 MP3', 'mp3', '')]
PRESET_BY_KEY = {k: (name, val, tip) for k, name, val, tip in PRESETS}

PROGRESS_STATE = {core.DONE: 'done', core.EXISTS: 'done', core.FAILED: 'failed', core.PAUSED: 'paused',
                  core.CANCELED: 'paused', core.MERGING: 'merging'}

COLUMN = 860        # 中间内容列的最大宽度


def fmt_duration(sec):
    if not sec:
        return ''
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def fmt_speed(bps):
    return f'{core.fmt_size(bps)}/s' if bps else ''


def fmt_ago(ts):
    d = time.time() - ts
    if d < 60:
        return '刚刚'
    if d < 3600:
        return f'{int(d // 60)} 分钟前'
    then = datetime.datetime.fromtimestamp(ts)
    days = (datetime.date.today() - then.date()).days
    if days == 0:
        return f'{int(d // 3600)} 小时前'
    if days == 1:
        return f'昨天 {then:%H:%M}'
    if days < 7:
        return f'{days} 天前'
    return f'{then:%Y-%m-%d}' if then.year != datetime.date.today().year else f'{then:%m-%d}'


greeting = greetings.greeting       # 首页问候语：按时间、星期、节日随机（见 greetings.py）


def reveal_in_explorer(path):
    if path and os.path.exists(path):
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
    elif path and os.path.isdir(os.path.dirname(path)):
        os.startfile(os.path.dirname(path))


def app_icon_path():
    for base in (ROOT, getattr(sys, '_MEIPASS', ROOT)):
        p = os.path.join(base, 'app', 'icon.ico')
        if os.path.isfile(p):
            return p
    return ''


# ---------------------------------------------------------------- 小工具

_themed_buttons = weakref.WeakKeyDictionary()     # 带图标的按钮 → (图标名, 颜色令牌)，换主题时重新着色


def set_btn_icon(btn, name, role):
    _themed_buttons[btn] = (name, role)
    btn.setIcon(icons.icon(name, c(role), 16))
    btn.setIconSize(QSize(16, 16))


def _refresh_btn_icons():
    for btn, (name, role) in list(_themed_buttons.items()):
        if shiboken6.isValid(btn):
            btn.setIcon(icons.icon(name, c(role), 16))


def button(text, slot=None, name='', tip='', icon=None, icon_role='text2'):
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    if icon:
        set_btn_icon(b, icon, {'primary': 'on_primary', 'accent': 'on_accent'}.get(name, icon_role))
    return b


def label(text='', name='', wrap=False):
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def restyle(w, name=None):
    if name is not None:
        w.setObjectName(name)
    w.style().unpolish(w)
    w.style().polish(w)


def clear_widget(name='clear'):
    w = QWidget()
    w.setObjectName(name)
    return w


def centered(inner, max_w=COLUMN, margins=(24, 0, 24, 0)):
    """把 inner 放在居中、有最大宽度的一列里。"""
    outer = clear_widget()
    h = QHBoxLayout(outer)
    h.setContentsMargins(*margins)
    h.setSpacing(0)
    inner.setMaximumWidth(max_w)
    h.addStretch(1)
    h.addWidget(inner, 100)
    h.addStretch(1)
    return outer


def scroll_area(content):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.viewport().setAutoFillBackground(False)
    sa.setWidget(content)
    sa.verticalScrollBar().setSingleStep(24)
    return sa


class Bridge(QObject):
    """后台线程 → 界面线程。"""
    parsed = Signal(int, object)
    parse_failed = Signal(int, str, str)
    expanded = Signal(int, object)
    task_updated = Signal(object)
    log = Signal(str, str)
    bili_status = Signal(object)
    collection_loaded = Signal(int, object, str)
    core_update = Signal(str, str)
    app_update = Signal(object)


class ResultRow:
    def __init__(self, token, url):
        self.token = token
        self.url = url
        self.state = 'parsing'
        self.video = None
        self.error = ''
        self.error_raw = ''
        self.card = None
        self.combo = None
        self.cover = None
        self.badge = None
        self.dl_btn = None
        self.spark = None
        self.auto_download = False   # 从选集加入：解析完自动下载
        self.from_collection = False

    @property
    def cover_url(self):
        return cover_url(self.video.info) if self.video else ''


# ---------------------------------------------------------------- 卡片

class ResultCard(Card):
    """一条解析结果：上半部分随解析状态重建，下半部分是这个视频的下载进度（一直保留）。"""

    def __init__(self):
        super().__init__(radius=16)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(14, 14, 16, 14)
        self.v.setSpacing(12)
        self.body = None
        self.tasks = QVBoxLayout()
        self.tasks.setSpacing(10)
        self.v.addLayout(self.tasks)

    def set_body(self, w):
        if self.body is not None:
            self.v.removeWidget(self.body)
            for sp in self.body.findChildren(Spark):
                sp.set_busy(False)
            self.body.deleteLater()
        self.body = w
        self.v.insertWidget(0, w)


class TaskView(Card):
    """一个下载任务。compact=True：嵌在解析结果卡片里的进度条；否则是「下载」页里的一张卡片。"""

    def __init__(self, win, task, compact):
        super().__init__(radius=14, fill=None if compact else 'surface', border=None if compact else 'border')
        self.win, self.task, self.compact = win, task, compact
        self.state = None
        self.b1 = IconButton('pause', '暂停', size=30, icon_size=17)
        self.b2 = IconButton('x', '取消', size=30, icon_size=17)
        self.b3 = IconButton('folder', '打开所在文件夹', size=30, icon_size=17)
        self.b1.clicked.connect(lambda: win._task_primary(self.task))
        self.b2.clicked.connect(lambda: win._task_secondary(self.task))
        self.b3.clicked.connect(lambda: reveal_in_explorer(self.task.filepath or self.task._expected_name
                                                           or self.task.out_dir))
        self.bar = ProgressLine(height=5 if compact else 6)
        self.status = label('', 'metaStrong')
        self.detail = ElidedLabel('', 'meta')
        self.name = None
        self.cover = None
        btns = QHBoxLayout()
        btns.setSpacing(2)
        for b in (self.b1, self.b2, self.b3):
            btns.addWidget(b)
        if compact:
            v = QVBoxLayout(self)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            v.addWidget(HLine())
            top = QHBoxLayout()
            top.setSpacing(8)
            icon = QLabel()
            icon.setPixmap(icons.pixmap('download', c('text3'), 15))
            self._icon = icon
            self.tag = label(self._tag_text(), 'metaStrong')
            top.addWidget(icon)
            top.addWidget(self.tag)
            top.addWidget(self.status)
            top.addWidget(self.detail, 1)
            top.addLayout(btns)
            v.addLayout(top)
            v.addWidget(self.bar)
        else:
            h = QHBoxLayout(self)
            h.setContentsMargins(12, 12, 12, 12)
            h.setSpacing(14)
            url = cover_url(task.video.info)
            self.cover = CoverView(112, 63, radius=8)
            self.cover.set_placeholder(task.video.platform)
            self.cover_url = url
            if win.thumbs.get(url):
                self.cover.set_pixmap(win.thumbs.get(url))
            elif url:
                win.thumbs.request(url, task.video.info.get('webpage_url') or task.video.url)
            h.addWidget(self.cover, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(6)
            self.name = ElidedLabel(task.video.title, 'cardTitle')
            col.addWidget(self.name)
            line = QHBoxLayout()
            line.setSpacing(8)
            line.addWidget(PlatformTag(task.video.platform))
            self.tag = ElidedLabel(self._tag_text(), 'meta')
            line.addWidget(self.tag, 1)
            col.addLayout(line)
            col.addWidget(self.bar)
            bottom = QHBoxLayout()
            bottom.setSpacing(8)
            bottom.addWidget(self.status)
            bottom.addWidget(self.detail, 1)
            col.addLayout(bottom)
            h.addLayout(col, 1)
            h.addLayout(btns)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: win._task_menu(self.task, self.mapToGlobal(pos)))
        self.refresh()

    def _tag_text(self):
        task = self.task
        s = self.win.settings
        extras = ['封面'] if task.save_cover else []
        if task.save_danmaku and s.save_danmaku and core.has_danmaku(task.video.info):
            extras.append('弹幕')
        if task.save_subs and s.save_subs and any(core.pick_subtitles(task.video.info, s.sub_langs)):
            extras.append('字幕')
        return task.tag.replace('] [', ' · ') + ''.join(f' + {x}' for x in extras)

    def mouseDoubleClickEvent(self, e):
        if self.task.status in (core.DONE, core.EXISTS):
            self.win._open_file(self.task)

    def refresh(self):
        task = self.task
        s = task.status
        if self.name is not None and task.filepath:
            self.name.set_full_text(os.path.basename(task.filepath), task.filepath)
        if self.compact:
            self._icon.setPixmap(icons.pixmap('download', c('text3'), 15))
        state = PROGRESS_STATE.get(s, 'running')
        role = {'done': 'ok', 'failed': 'err', 'paused': 'text4', 'merging': 'info'}.get(state, 'brand')
        self.bar.set_state(task.progress, role, indeterminate=(s == core.MERGING))

        parts = []
        if task.total:
            parts.append(f'{core.fmt_size(task.downloaded) if task.downloaded else "0 MB"} / {core.fmt_size(task.total)}'
                         if s in (core.RUNNING, core.PAUSED) and task.downloaded < task.total
                         else core.fmt_size(task.total))
        elif task.downloaded:
            parts.append(core.fmt_size(task.downloaded))
        if s in (core.DONE, core.EXISTS) and task.extras:
            subs = [core.sub_name(x) for x in task.extras if x not in ('danmaku', 'burned')]
            bits = (['字幕 ' + '/'.join(subs)] if subs else []) + (['弹幕'] if 'danmaku' in task.extras else []) \
                + (['弹幕版视频'] if 'burned' in task.extras else [])
            parts.append('已存 ' + ' + '.join(bits))
        if s == core.RUNNING:
            if task.speed:
                parts.append(fmt_speed(task.speed))
            if task.eta:
                parts.append(f'剩余 {fmt_duration(task.eta)}')
        self.detail.set_full_text('  ·  '.join(parts), task.error_raw or None)

        status = core.STATUS_TEXT.get(s, s)
        if s == core.RUNNING:
            status = f'{task.progress * 100:.0f}%' + (f' · {task.stage}' if task.stage else '')
        elif s == core.MERGING and task.stage:
            status = f'{task.stage}…'
        if s == core.FAILED and task.error:
            status = f'失败：{task.error}'
        self.status.setText(status)
        self.status.setToolTip(task.error_raw or task.error or status)
        self.status.setStyleSheet(f'color: {c(theme.STATUS_ROLE.get(s, "text2")).name()};')
        self.status.setMaximumWidth(460 if s == core.FAILED else 16777215)

        b1 = {core.QUEUED: ('pause', '暂停'), core.RUNNING: ('pause', '暂停'), core.PAUSED: ('play', '继续'),
              core.FAILED: ('refresh', '重试'), core.CANCELED: ('refresh', '重试'),
              core.DONE: ('play_circle', '打开文件'), core.EXISTS: ('play_circle', '打开文件')}.get(s, ('pause', '暂停'))
        self.b1.set_icon(*b1)
        self.b1.color_role = 'ok' if s in (core.DONE, core.EXISTS) else None
        self.b1.setEnabled(s != core.MERGING)
        finished = s in (core.DONE, core.EXISTS, core.CANCELED, core.FAILED)
        self.b2.set_icon('trash' if finished else 'x', '从列表移除' if finished else '取消')
        self.b2.setEnabled(s != core.MERGING or task.stage.startswith('弹幕版'))   # 生成弹幕版时可以取消


class HistoryRow(QWidget):
    """历史记录里的一行：平台、标题、清晰度 · 大小 · 时间；悬停时出现操作按钮。"""

    def __init__(self, win, h):
        super().__init__()
        self.win, self.h = win, h
        self.setAttribute(Qt.WA_Hover)
        self.setFixedHeight(62)
        exists = bool(h['filepath']) and os.path.exists(h['filepath'])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(12)
        tag_box = clear_widget()
        tag_box.setFixedWidth(92)
        tl = QHBoxLayout(tag_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(PlatformTag(h['platform']))
        tl.addStretch(1)
        lay.addWidget(tag_box, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = ElidedLabel(h['title'] or h['url'], 'rowLabel')
        title.set_full_text(h['title'] or h['url'], f'{h["title"]}\n{h["filepath"]}' +
                            ('' if exists else '\n（文件已被移动或删除）'))
        if not exists:
            title.setStyleSheet(f'color: {c("text3").name()};')
        bits = [(h['quality'] or '').replace('] [', ' · '), core.fmt_size(h['size']) if h['size'] else '',
                fmt_ago(h['ts'])] + ([] if exists else ['文件已不在原位置'])
        col.addWidget(title)
        col.addWidget(ElidedLabel('  ·  '.join(b for b in bits if b), 'meta'))
        lay.addLayout(col, 1)
        self.ops = clear_widget()
        oh = QHBoxLayout(self.ops)
        oh.setContentsMargins(0, 0, 0, 0)
        oh.setSpacing(2)
        for icon, tip, slot, ok in (
                ('play_circle', '打开', lambda: os.startfile(h['filepath']), exists),
                ('folder', '打开所在文件夹', lambda: reveal_in_explorer(h['filepath']), exists),
                ('refresh', '重新解析', lambda: win._reparse_url(h['url']), True),
                ('trash', '删除这条记录（不删文件）', lambda: win._delete_history(h['id']), True),
                ('more', '重命名、移至分区…', None, True)):
            b = IconButton(icon, tip, size=30, icon_size=17)
            if slot:
                b.clicked.connect(slot)
            else:
                b.clicked.connect(lambda _=False, bb=b: win._item_menu(h, bb.mapToGlobal(QPoint(0, bb.height() + 4))))
            b.setEnabled(ok)
            oh.addWidget(b)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: win._item_menu(h, self.mapToGlobal(pos)))
        self.ops.setVisible(False)
        lay.addWidget(self.ops)
        self.exists = exists

    def enterEvent(self, e):
        self.ops.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.ops.setVisible(False)
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.exists:
            os.startfile(self.h['filepath'])

    def paintEvent(self, e):
        if self.underMouse():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(c('hover'))
            p.drawRoundedRect(QRectF(self.rect()), 10, 10)


class ParseRow(QWidget):
    """历史记录（解析记录）里的一行：平台、标题、信息 · 时间。点一下重新解析，悬停时出现操作按钮。"""

    def __init__(self, win, rec):
        super().__init__()
        self.win, self.rec = win, rec
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(62)
        pan_rec = rec['kind'] == PAN
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(12)
        tag_box = clear_widget()
        tag_box.setFixedWidth(92)
        tl = QHBoxLayout(tag_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(PlatformTag(rec['platform']))
        tl.addStretch(1)
        lay.addWidget(tag_box, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = ElidedLabel(rec['title'] or rec['url'], 'rowLabel')
        title.set_full_text(rec['title'] or rec['url'], f'{rec["title"]}\n{rec["url"]}\n点击重新解析')
        sec = win.sections.get(rec['section_id']) if rec.get('section_id') else None
        bits = ['网盘分享' if pan_rec else '视频', rec['info'] or '', f'「{sec["name"]}」分区' if sec and not sec['builtin'] else '',
                fmt_ago(rec['ts'])]
        col.addWidget(title)
        col.addWidget(ElidedLabel('  ·  '.join(b for b in bits if b), 'meta'))
        lay.addLayout(col, 1)
        self.ops = clear_widget()
        oh = QHBoxLayout(self.ops)
        oh.setContentsMargins(0, 0, 0, 0)
        oh.setSpacing(2)
        for icon, tip, slot in (
                ('refresh', '重新解析', lambda: win._reparse_record(rec)),
                ('copy', '复制链接', lambda: (QGuiApplication.clipboard().setText(rec['url']), win.notify('已复制链接'))),
                ('trash', '删除这条记录', lambda: win._delete_parse(rec)),
                ('more', '重命名、移至分区…', None)):
            b = IconButton(icon, tip, size=30, icon_size=17)
            if slot:
                b.clicked.connect(slot)
            else:
                b.clicked.connect(lambda _=False, bb=b: win._parse_menu(rec, bb.mapToGlobal(QPoint(0, bb.height() + 4))))
            oh.addWidget(b)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: win._parse_menu(rec, self.mapToGlobal(pos)))
        self.ops.setVisible(False)
        lay.addWidget(self.ops)

    def enterEvent(self, e):
        self.ops.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.ops.setVisible(False)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.win._reparse_record(self.rec)

    def paintEvent(self, e):
        if self.underMouse():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(c('hover'))
            p.drawRoundedRect(QRectF(self.rect()), 10, 10)


class AccountButton(ui._HoverButton):
    """侧边栏左下角：头像 + 名字 + B站状态，点击弹出菜单（像 Claude 的账号菜单）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.avatar = ui.Avatar(32, self)
        self.avatar.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.name, self.sub = '', ''
        self.collapsed = False

    def set_info(self, name, sub, initial):
        self.name, self.sub = name, sub
        self.avatar.set_text(initial)
        self.setToolTip(f'{name}\n{sub}')
        self.update()

    def set_collapsed(self, on):
        self.collapsed = on
        self.resizeEvent(None)
        self.update()

    def resizeEvent(self, e):
        self.avatar.move((self.width() - 32) // 2 if self.collapsed else 8, 10)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hov() or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(c('press' if self.isDown() else 'hover'))
            p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        if self.collapsed:
            return
        x, w = 50, self.width() - 50 - 30
        f = ui.px_font(13, 600)
        p.setFont(f)
        p.setPen(c('text'))
        p.drawText(QRectF(x, 7, w, 20), int(Qt.AlignLeft | Qt.AlignVCenter),
                   QFontMetrics(f).elidedText(self.name, Qt.ElideRight, w))
        f2 = ui.px_font(12)
        p.setFont(f2)
        p.setPen(c('text3'))
        p.drawText(QRectF(x, 26, w, 18), int(Qt.AlignLeft | Qt.AlignVCenter),
                   QFontMetrics(f2).elidedText(self.sub, Qt.ElideRight, w))
        icons.paint(p, 'chevron_down', QRectF(self.width() - 26, 18, 16, 16), c('text3'))


# ---------------------------------------------------------------- 主窗口

class MainWindow(PanMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.bridge = Bridge()
        self.parser = core.Parser(lambda: self.settings, self.bridge.log.emit)
        self.manager = core.DownloadManager(lambda: self.settings, self.bridge.task_updated.emit,
                                            self.bridge.log.emit)
        self.thumbs = ThumbLoader(lambda: self.settings.proxy, self)
        self.thumbs.loaded.connect(self._on_thumb_loaded)
        self.rows = {}              # token -> ResultRow
        self._next_token = 1
        self.task_views = {}        # task.id -> TaskView（下载页）
        self.task_lines = {}        # task.id -> TaskView（解析结果卡片里）
        self.task_row = {}          # task.id -> 解析结果 token
        # 启动时剪贴板里已有的内容一律不理会，只提示之后新复制的链接
        self._last_clip = QGuiApplication.clipboard().text()
        self._clip_urls = []
        self._pending_cover_saves = {}   # url -> [(title, dir)]
        self.bili_account = None
        self._prev_status = {}           # task.id -> 上一次的状态（判断「刚完成」）
        self._batch_done = 0             # 本轮完成数，全部完成时提示
        self._hist_limit = 150
        self.page = 'parse'
        self.fresh = False               # 点了「解析新链接」：显示首页，之前的结果暂时收起
        self.settings_panel = None
        self.search_panel = None
        self.hist_section = None         # 历史记录页只看某个分区（None = 全部）
        self.hist_kind = None            # 历史记录页只看视频 / 网盘（None = 两类都看）
        self.hist_mode = 'parses'        # 历史记录页：parses 解析记录 / downloads 下载好的视频
        self.sections = {}
        try:
            self.history = History()
        except Exception as e:  # noqa: BLE001 - 历史库打不开时不影响下载
            self.history = None
            log.warning('打开下载历史失败: %s', e)

        self.bridge.parsed.connect(self._on_parsed)
        self.bridge.parse_failed.connect(self._on_parse_failed)
        self.bridge.expanded.connect(self._on_expanded)
        self.bridge.task_updated.connect(self._on_task_updated)
        self.bridge.log.connect(self._append_log)
        self.bridge.bili_status.connect(self._on_bili_status)
        self.bridge.collection_loaded.connect(self._on_collection_loaded)
        self.bridge.core_update.connect(self._on_core_update)
        self.bridge.app_update.connect(self._on_app_update)

        self.setWindowTitle(f'{APP_NAME} {APP_VERSION}')
        self.setMinimumSize(980, 640)
        self._pan_init()
        self._parse_timer = QTimer(self, singleShot=True, interval=400)     # 一次解析很多链接时合并刷新
        self._parse_timer.timeout.connect(self._after_history_change)
        self._build_ui()
        theme.signals.changed.connect(self._on_theme_changed)
        if self.settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
        else:
            avail = QGuiApplication.primaryScreen().availableGeometry()
            self.resize(min(1280, int(avail.width() * 0.9)), min(860, int(avail.height() * 0.9)))
            self.move(avail.center() - self.rect().center())

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(600)
        QGuiApplication.instance().applicationStateChanged.connect(self._on_app_state)
        self._really_quit = False
        self.hotkey = GlobalHotkey(QApplication.instance(), self)
        self.hotkey.activated.connect(self._hotkey_parse)
        self._apply_hotkey(silent=True)
        if self.settings.check_core_update:
            QTimer.singleShot(8000, self._check_core_update)
        if self.settings.check_app_update:
            QTimer.singleShot(5000, self._check_app_update)
        self._check_bili()
        self._bili_timer = QTimer(self)            # 软件一直开着时，每 4 小时检查一次是否需要续期
        self._bili_timer.setInterval(4 * 3600 * 1000)
        self._bili_timer.timeout.connect(lambda: bilibili.is_logged_in() and self._check_bili())
        self._bili_timer.start()
        self._setup_tray()
        self._tasks_save_timer = QTimer(self)
        self._tasks_save_timer.setSingleShot(True)
        self._tasks_save_timer.setInterval(1500)
        self._tasks_save_timer.timeout.connect(self._save_tasks)
        QTimer.singleShot(0, self._restore_tasks)
        if not ffmpeg_dir():
            QTimer.singleShot(300, lambda: QMessageBox.warning(
                self, APP_NAME, '没有找到 ffmpeg（程序目录下的 ffmpeg 文件夹），YouTube/B站等需要合并音视频的高清格式将无法下载。'))

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        central = clear_widget('central')
        self.central = central
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.pages = {'parse': self._build_parse_page(), 'tasks': self._build_tasks_page(),
                      'history': self._build_history_page(), 'log': self._build_log_page()}
        for w in self.pages.values():
            self.stack.addWidget(w)
        self.setCentralWidget(central)
        self.toast = ui.Toast(central)
        QShortcut(QKeySequence('Ctrl+,'), self, activated=self._open_settings)
        QShortcut(QKeySequence('Ctrl+L'), self, activated=self._new_parse)
        QShortcut(QKeySequence('Ctrl+K'), self, activated=self._open_search)
        self._set_collapsed(self.settings.sidebar_collapsed)
        self._update_parse_mode()
        self._refresh_sidebar()
        self._go('parse')

    # 侧边栏 ----------------------------------------------------------
    def _build_sidebar(self):
        sb = QFrame()
        sb.setObjectName('sidebar')
        self.sidebar = sb
        v = QVBoxLayout(sb)
        v.setContentsMargins(10, 12, 10, 10)
        v.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(6, 0, 0, 0)
        top.setSpacing(8)
        self.side_spark = Spark(24)
        self.brand = label(APP_NAME, 'brandName')
        self.collapse_btn = IconButton('sidebar', '收起侧边栏', size=32, icon_size=18)
        self.collapse_btn.clicked.connect(lambda: self._set_collapsed(not self.settings.sidebar_collapsed, save=True))
        top.addWidget(self.side_spark)
        top.addWidget(self.brand)
        top.addStretch(1)
        top.addWidget(self.collapse_btn)
        self.side_top = top
        v.addLayout(top)
        v.addSpacing(16)
        self.nav_new = NavItem('解析新链接', special=True)
        self.nav_new.setCheckable(True)
        self.nav_new.clicked.connect(self._new_parse)
        self.nav_search = NavItem('搜索', 'search')       # 和 Claude 一样放在「新建」下面
        self.nav_search.setCheckable(False)
        self.nav_search.setToolTip('搜索下载记录和分区（Ctrl+K）')
        self.nav_search.clicked.connect(self._open_search)
        self.nav_tasks = NavItem('下载', 'download')
        self.nav_tasks.clicked.connect(lambda: self._go('tasks'))
        self.nav_hist = NavItem('历史记录', 'clock')
        self.nav_hist.clicked.connect(lambda: self._show_section(None))
        self.navs = {'parse': self.nav_new, 'tasks': self.nav_tasks, 'history': self.nav_hist}
        for n in (self.nav_new, self.nav_search, self.nav_tasks, self.nav_hist):
            v.addWidget(n)
        v.addSpacing(14)
        # 分区 + 最近下载（一起滚动）
        side = clear_widget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(1)
        extra = self.settings.extra
        self.sec_headers, self.sec_boxes = {}, {}
        for kind, title, key in ((VIDEO, '视频分区', 'sections_open'), (PAN, '网盘分区', 'pan_sections_open')):
            head = ui.SideHeader(title, extra.get(key, True), '新建' + title)
            head.toggled_open.connect(lambda on, k=key: self._toggle_side(k, on))
            head.plus.clicked.connect(lambda _=False, k=kind: self._new_section(kind=k))
            sl.addWidget(head)
            sec = clear_widget()
            box = QVBoxLayout(sec)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(1)
            sl.addWidget(sec)
            sl.addSpacing(10)
            self.sec_headers[kind], self.sec_boxes[kind] = head, box
        self.recent_header = ui.SideHeader('最近下载', extra.get('recents_open', True))
        self.recent_header.toggled_open.connect(lambda on: self._toggle_side('recents_open', on))
        sl.addWidget(self.recent_header)
        rc = clear_widget()
        self.recent_box = QVBoxLayout(rc)
        self.recent_box.setContentsMargins(0, 0, 0, 0)
        self.recent_box.setSpacing(1)
        sl.addWidget(rc)
        sl.addStretch(1)
        self.expanded_sections = set()
        self.recent_scroll = scroll_area(side)
        v.addWidget(self.recent_scroll, 1)
        self.side_fill = QWidget()        # 收起时代替最近列表占位
        self.side_fill.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        v.addWidget(self.side_fill, 1)
        v.addSpacing(6)
        self.account = AccountButton()
        self.account.clicked.connect(self._account_menu)
        v.addWidget(self.account)
        self._update_account()
        return sb

    def _set_collapsed(self, on, save=False):
        self.settings.sidebar_collapsed = on
        self.sidebar.setFixedWidth(60 if on else 260)
        for w in (self.brand, self.side_spark, self.recent_scroll):
            w.setVisible(not on)
        self.side_fill.setVisible(on)
        self.side_top.setContentsMargins(0 if on else 6, 0, 0, 0)
        for n in (*self.navs.values(), self.nav_search):
            n.set_collapsed(on)
        self.account.set_collapsed(on)
        self.collapse_btn.setToolTip('展开侧边栏' if on else '收起侧边栏')
        if save:
            self._save_settings()

    def _go(self, page):
        if self.settings_panel is not None and self.settings_panel.isVisible():
            self.settings_panel.close_panel()
        self.page = page
        self.stack.setCurrentWidget(self.pages[page])
        for key, n in self.navs.items():
            n.setChecked(key == page)
        if page == 'history':
            self._refresh_history()
        self._place_toast()

    def _new_parse(self):
        """像 Claude 的「新建」：回到首页（问候语 + 居中输入框）。
        之前的解析结果先留着（首页有「回到刚才的解析结果」），在首页解析新链接时才清掉；下载不受影响。"""
        if (self.rows or self.pan_cards) and not self.fresh:
            self.fresh = True
            self._update_parse_mode()
        self._go('parse')
        self.composer.edit.setFocus()

    def _back_to_results(self):
        self.fresh = False
        self._update_parse_mode()
        self._go('parse')

    ago = staticmethod(fmt_ago)

    @staticmethod
    def _clear_layout(lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.hide()           # 立刻从界面上拿掉（deleteLater 要等事件循环，期间会和新内容叠在一起）
                w.setParent(None)
                w.deleteLater()

    def _toggle_side(self, key, on):
        self.settings.extra[key] = on
        self._save_settings()
        self._refresh_sidebar()

    def _record_row(self, h, height=32):
        n = NavItem(h['title'] or h['url'], height=height, font_px=13)
        n.setCheckable(False)
        n.setToolTip(f'{h["title"]}\n{(h["quality"] or "").replace("] [", " · ")} · {fmt_ago(h["ts"])}')
        n.clicked.connect(lambda _=False, rec=h: self._open_recent(rec))
        n.setContextMenuPolicy(Qt.CustomContextMenu)
        n.customContextMenuRequested.connect(lambda pos, rec=h, w=n: self._item_menu(rec, w.mapToGlobal(pos)))
        return n

    def _refresh_sidebar(self):
        """侧边栏的「分区」和「最近下载」（像 Claude 侧边栏的项目和最近对话）。"""
        for box in self.sec_boxes.values():
            self._clear_layout(box)
        self._clear_layout(self.recent_box)
        if not self.history:
            return
        secs = self.history.sections()
        self.sections = {s['id']: s for s in secs}
        for s in secs:
            if not self.sec_headers[s['kind']].open:
                continue
            unit = '个下载好的视频' if s['kind'] == VIDEO else '个网盘分享（点开后点一下就重新解析）'
            n = NavItem(s['name'], s['icon'], height=34, font_px=14)
            n.setCheckable(False)
            n.set_trailing(str(s['count']) if s['count'] else '')
            n.setToolTip(f'{s["name"]}：{s["count"]} {unit}\n点击展开 / 收起，右键可以' +
                         ('改名' if s['builtin'] else '编辑或删除'))
            n.clicked.connect(lambda _=False, sid=s['id']: self._toggle_section(sid))
            n.setContextMenuPolicy(Qt.CustomContextMenu)
            n.customContextMenuRequested.connect(lambda pos, sec=s, w=n: self._section_menu(sec, w.mapToGlobal(pos)))
            self.sec_boxes[s['kind']].addWidget(n)
            if s['id'] in self.expanded_sections:
                tree = ui.TreeBox()
                tv = QVBoxLayout(tree)
                tv.setContentsMargins(30, 0, 0, 4)
                tv.setSpacing(0)
                if s['kind'] == VIDEO:
                    for h in self.history.recent('', limit=5, section=s['id']):
                        tv.addWidget(self._record_row(h, 30))
                else:
                    for rec in self.history.parses('', limit=8, section=s['id']):
                        tv.addWidget(self._parse_side_row(rec, 30))
                more = NavItem(f'查看全部（{s["count"]}）' if s['count'] else
                               ('还没有视频，右键视频可以移进来' if s['kind'] == VIDEO else '解析过的网盘分享会放进来'),
                               height=30, font_px=13)
                more.setCheckable(False)
                more.muted = True
                if not s['count']:
                    more.setToolTip('在历史记录、最近下载或解析结果里右键视频，选「移至分区」，就会放进这个分区'
                                    if s['kind'] == VIDEO else
                                    '网盘分享解析后会自动放进对应网盘的分区；也可以在历史记录里右键一条记录，选「移至分区」')
                more.clicked.connect(lambda _=False, sid=s['id']: self._show_section(sid))
                tv.addWidget(more)
                self.sec_boxes[s['kind']].addWidget(tree)
        if self.recent_header.open:
            recs = self.history.recent('', limit=16, kind=VIDEO)
            for h in recs:
                self.recent_box.addWidget(self._record_row(h))
            if not recs:
                empty = label('下载好的视频会出现在这里', 'meta')
                empty.setContentsMargins(10, 4, 0, 0)
                self.recent_box.addWidget(empty)
            elif self.history.count(kind=VIDEO) > len(recs):
                more = NavItem('查看全部下载好的视频', height=30, font_px=13)
                more.setCheckable(False)
                more.muted = True
                more.clicked.connect(self._show_downloads)
                self.recent_box.addWidget(more)

    def _parse_side_row(self, rec, height=32):
        """侧边栏里的一条解析记录（网盘分区）：点一下重新解析。"""
        n = NavItem(rec['title'] or rec['url'], height=height, font_px=13)
        n.setCheckable(False)
        n.setToolTip(f'{rec["title"]}\n{rec["url"]}\n{rec["info"] or ""} · {fmt_ago(rec["ts"])}\n点击重新解析')
        n.clicked.connect(lambda _=False, r=rec: self._reparse_record(r))
        n.setContextMenuPolicy(Qt.CustomContextMenu)
        n.customContextMenuRequested.connect(lambda pos, r=rec, w=n: self._parse_menu(r, w.mapToGlobal(pos)))
        return n

    def _toggle_section(self, sid):
        self.expanded_sections ^= {sid}
        self._refresh_sidebar()

    def _open_recent(self, h):
        if h['filepath'] and os.path.exists(h['filepath']):
            os.startfile(h['filepath'])
        else:
            self.notify('文件已被移动或删除，可以右键「重新解析」再下载一次', 'error')

    def _item_menu(self, h, pos, after=None):
        """一条下载记录的菜单（侧边栏、历史记录、搜索里通用）：打开、重命名、移至分区……"""
        m = ui.popup_menu(self)
        ic = lambda name: icons.icon(name, c('text2'))   # noqa: E731
        exists = bool(h['filepath']) and os.path.exists(h['filepath'])
        a = m.addAction(ic('play_circle'), '打开', lambda: os.startfile(h['filepath']))
        a.setEnabled(exists)
        a = m.addAction(ic('folder'), '打开所在文件夹', lambda: reveal_in_explorer(h['filepath']))
        a.setEnabled(exists)
        m.addSeparator()
        m.addAction(ic('edit'), '重命名…', lambda: self._rename_record(h))
        sub = m.addMenu(ic('folder_move'), '移至分区')
        kind = h.get('kind') or VIDEO
        for s in (self.history.sections(kind) if self.history else []):
            here = h.get('section_id') == s['id']
            a = sub.addAction(ic(s['icon']), s['name'] + ('（当前）' if here else ''),
                              lambda sid=s['id']: self._move_record(h, sid))
            a.setEnabled(not here)
        sub.addSeparator()
        sub.addAction(ic('folder_plus'), '新建分区…', lambda: self._new_section(move=h, kind=kind))
        if h.get('section_id'):
            sub.addAction(ic('x'), '移出分区', lambda: self._move_record(h, None))
        m.addSeparator()
        m.addAction(ic('refresh'), '重新解析', lambda: self._reparse_url(h['url']))
        m.addAction(ic('copy'), '复制原链接', lambda: QGuiApplication.clipboard().setText(h['url']))
        m.addSeparator()
        m.addAction(ic('trash'), '从历史中删除', lambda: self._delete_history(h['id']))
        m.exec(pos)
        if after:
            after()

    def _parse_menu(self, rec, pos, after=None):
        """一条解析记录的菜单（历史记录、网盘分区、搜索里通用）：重新解析、重命名、移至分区……"""
        m = ui.popup_menu(self)
        ic = lambda name: icons.icon(name, c('text2'))   # noqa: E731
        m.addAction(ic('refresh'), '重新解析', lambda: self._reparse_record(rec))
        m.addAction(ic('copy'), '复制链接', lambda: QGuiApplication.clipboard().setText(rec['url']))
        m.addSeparator()
        m.addAction(ic('edit'), '重命名…', lambda: self._rename_parse(rec))
        if rec['kind'] == PAN:
            sub = m.addMenu(ic('folder_move'), '移至分区')
            for s in (self.history.sections(PAN) if self.history else []):
                here = rec.get('section_id') == s['id']
                a = sub.addAction(ic(s['icon']), s['name'] + ('（当前）' if here else ''),
                                  lambda sid=s['id']: self._move_parse(rec, sid))
                a.setEnabled(not here)
            sub.addSeparator()
            sub.addAction(ic('folder_plus'), '新建分区…', lambda: self._new_section(kind=PAN, move_parse=rec))
            if rec.get('section_id'):
                sub.addAction(ic('x'), '移出分区', lambda: self._move_parse(rec, None))
        m.addSeparator()
        m.addAction(ic('trash'), '删除这条记录', lambda: self._delete_parse(rec))
        m.exec(pos)
        if after:
            after()

    def _reparse_record(self, rec):
        if self.search_panel is not None and self.search_panel.isVisible():
            self.search_panel.close_panel()
        self._reparse_url(rec['url'])

    def _rename_parse(self, rec):
        what = '网盘分享' if rec['kind'] == PAN else '视频'
        dlg = TextDialog(self, '重命名', rec['title'] or '', placeholder=rec.get('orig_title') or '',
                         hint=f'只改软件里显示的名字。留空 = 恢复成{what}原来的标题。')
        if dlg.exec():
            self.history.rename_parse(rec['id'], dlg.value)
            self._after_history_change()
            self.notify('已重命名' if dlg.value else '已恢复原标题', 'ok')

    def _move_parse(self, rec, sid):
        self.history.move_parses([rec['id']], sid)
        self._after_history_change()
        name = self.sections.get(sid, {}).get('name') if sid else None
        self.notify(f'已移至「{name}」' if name else '已移出分区', 'ok')

    def _delete_parse(self, rec):
        self.history.delete_parse(rec['id'])
        self._after_history_change()

    def _record_parse(self, kind, pkey, title, platform, uploader, url, info='', count=0, size=0):
        """解析成功后记一条解析记录（历史记录页；网盘分享还会放进网盘分区）。"""
        if not self.history or not pkey:
            return
        try:
            self.history.add_parse(kind, pkey, title, platform, uploader, url, info, count, size)
        except Exception as e:  # noqa: BLE001
            log.warning('写入解析记录失败: %s', e)
            return
        self._parse_timer.start()

    def _rename_record(self, h):
        dlg = TextDialog(self, '重命名', h['title'] or '', placeholder=h.get('orig_title') or '',
                         hint='只改软件里显示的名字，不会改动电脑上的视频文件。留空 = 恢复成视频原标题。')
        if dlg.exec():
            self.history.rename(h['id'], dlg.value)
            self._after_history_change()
            self.notify('已重命名' if dlg.value else '已恢复原标题', 'ok')

    def _move_record(self, h, sid):
        self.history.move([h['id']], sid)
        self._after_history_change()
        name = self.sections.get(sid, {}).get('name') if sid else None
        self.notify(f'已移至「{name}」' if name else '已移出分区', 'ok')

    def _new_section(self, move=None, kind=VIDEO, move_parse=None):
        what = '视频' if kind == VIDEO else '网盘'
        dlg = FolderDialog(self, f'新建{what}分区')
        if not dlg.exec():
            return
        sid = self.history.create_section(dlg.name, dlg.icon, kind)
        if move:
            self.history.move([move['id']], sid)
        if move_parse:
            self.history.move_parses([move_parse['id']], sid)
            move = move_parse
        self.expanded_sections.add(sid)
        head = self.sec_headers[kind]
        if not head.open:
            head.open = True
            self.settings.extra['sections_open' if kind == VIDEO else 'pan_sections_open'] = True
        self._after_history_change()
        self.notify(f'已新建{what}分区「{dlg.name}」' + ('，已移进去' if move else '，右键记录可以「移至分区」'), 'ok')

    def _section_menu(self, s, pos):
        m = ui.popup_menu(self)
        ic = lambda name: icons.icon(name, c('text2'))   # noqa: E731
        m.addAction(ic('list'), f'查看全部（{s["count"]}）', lambda: self._show_section(s['id']))
        m.addSeparator()
        if s['builtin']:
            m.addAction(ic('edit'), '重命名分区…', lambda: self._edit_section(s))
            hint = m.addAction(f'{s["name"]} 的视频下载后会自动放进来' if s['kind'] == VIDEO
                               else f'解析过的{s["name"]}分享会自动放进来')
            hint.setEnabled(False)
        else:
            m.addAction(ic('edit'), '编辑分区…', lambda: self._edit_section(s))
            m.addAction(ic('trash'), '删除分区', lambda: self._delete_section(s))
        m.exec(pos)

    def _edit_section(self, s):
        dlg = FolderDialog(self, '编辑分区', s['name'], s['icon'], icon_editable=not s['builtin'])
        if dlg.exec():
            self.history.update_section(s['id'], dlg.name, None if s['builtin'] else dlg.icon)
            self._after_history_change()

    def _delete_section(self, s):
        if QMessageBox.question(self, APP_NAME, f'删除分区「{s["name"]}」？\n里面的记录和文件不会被删除，会回到所属平台的分区。'
                                ) != QMessageBox.Yes:
            return
        self.history.delete_section(s['id'])
        if self.hist_section == s['id']:
            self.hist_section = None
        self._after_history_change()
        self.notify(f'已删除分区「{s["name"]}」')

    def _show_section(self, sid):
        """「查看全部」：打开历史记录页，只看这个分区（sid 为 None = 全部解析记录）。
        视频分区里是下载好的视频，网盘分区里是网盘分享的解析记录。"""
        kind = self.sections.get(sid, {}).get('kind') if sid else None
        self.hist_section = sid
        self.hist_mode = 'downloads' if kind == VIDEO else 'parses'
        self.hist_kind = kind if kind == PAN else None
        self._go('history')

    def _show_downloads(self):
        """「查看全部下载好的视频」"""
        self.hist_mode, self.hist_section, self.hist_kind = 'downloads', None, None
        self._go('history')

    def _open_search(self):
        if self.search_panel is None:
            self.search_panel = SearchPanel(self.central, self)
        self.search_panel.open_search()

    def _update_account(self):
        info = self.bili_account
        nick = (self.settings.nickname or '').strip()
        self.account.avatar.set_image(avatar.image())
        if info and not info.get('expired') and info.get('name'):
            vip = info.get('vip_label') or ('大会员' if info.get('vip') else '非大会员')
            self.account.set_info(info['name'], f'B站 · {vip}', info['name'])
        elif info and info.get('expired'):
            self.account.set_info(nick or 'B站登录已失效', '点这里重新扫码登录', nick)
        elif info and info.get('offline'):
            self.account.set_info(nick or 'B站 · 已登录', '网络不通，稍后自动重试', nick)
        else:
            self.account.set_info(nick or APP_NAME, '未登录 B站 · 设置与更多', nick)
        if hasattr(self, 'bili_chip'):
            logged = info and not info.get('expired')
            self.bili_chip.setText(f'B站 · {info.get("name") or "已登录"}' if logged else 'B站扫码登录')

    def _account_menu(self):
        # 菜单开着时再点一下头像框：那次点击先把菜单关掉了，这里就不要再打开（再点一次 = 收起）
        if time.monotonic() - getattr(self, '_account_menu_closed', 0) < 0.3:
            return
        m = ui.popup_menu(self)
        m.aboutToHide.connect(lambda: setattr(self, '_account_menu_closed', time.monotonic()))
        info = self.bili_account
        logged = info and not info.get('expired')
        head = m.addAction(f'B站 · {info.get("name") or "已登录"}' if logged else '未登录 B站')
        head.setEnabled(False)
        m.addSeparator()
        m.addAction(icons.icon('user', c('text2')), '更换头像…' if avatar.exists() else '设置头像…',
                    self._change_avatar)
        a = m.addAction(icons.icon('settings', c('text2')), '设置', self._open_settings)
        a.setShortcut(QKeySequence('Ctrl+,'))
        a.setShortcutVisibleInContextMenu(True)
        if logged:
            m.addAction(icons.icon('tv', c('text2')), '重新扫码登录 B站', lambda: self._bili_login(True))
            m.addAction(icons.icon('log_out', c('text2')), '退出 B站登录', self._bili_logout)
        else:
            m.addAction(icons.icon('tv', c('text2')), 'B站长效登录（电视端扫码，约 180 天）', lambda: self._bili_login(True))
            m.addAction(icons.icon('tv', c('text2')), 'B站网页扫码登录（约 7 天）', lambda: self._bili_login(False))
        m.addAction(icons.icon('user', c('text2')), '网盘账号…', lambda: self._open_settings('network'))
        m.addAction(icons.icon('cube', c('text2')), '检查解析内核更新', lambda: self._open_settings('core'))
        m.addAction(icons.icon('file_text', c('text2')), '查看日志', lambda: self._go('log'))
        m.addAction(icons.icon('folder', c('text2')), '打开下载文件夹', self._open_dir)
        m.addSeparator()
        m.addAction(icons.icon('help', c('text2')), '获取帮助', lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))
        m.addAction(icons.icon('info', c('text2')), '关于', lambda: self._open_settings('about'))
        m.addSeparator()
        m.addAction(icons.icon('log_out', c('text2')), '退出程序', self._quit)
        hint = m.sizeHint()
        m.exec(self.account.mapToGlobal(QPoint(0, -hint.height() - 6)))

    def _change_avatar(self):
        if avatar.change(self):
            self.setting_changed('avatar')

    # 解析页 ----------------------------------------------------------
    def _build_parse_page(self):
        page = clear_widget('page')
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 顶栏（有结果时显示）
        head = clear_widget()
        hh = QHBoxLayout(head)
        hh.setContentsMargins(24, 14, 20, 8)
        hh.setSpacing(10)
        hh.addWidget(label('解析结果', 'headTitle'))
        self.res_label = label('', 'meta')
        hh.addWidget(self.res_label)
        hh.addStretch(1)
        self.parse_status = label('', 'meta')
        hh.addWidget(self.parse_status)
        hh.addSpacing(6)
        hh.addWidget(button('全部下载', self._download_all, 'primary', '按每张卡片的选择全部下载（视频按选好的清晰度，网盘按勾选的文件）', 'download'))
        clear_btn = IconButton('trash', '清空解析结果', size=34, icon_size=18)
        clear_btn.clicked.connect(self._clear_results)
        hh.addWidget(clear_btn)
        self.parse_head = head
        v.addWidget(head)

        self.parse_stack = QStackedWidget()
        v.addWidget(self.parse_stack, 1)

        # 空状态：星芒 + 衬线问候语 + 居中的输入框
        empty = clear_widget('page')
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(24, 0, 24, 0)
        ev.setSpacing(0)
        ev.addStretch(10)
        hero = clear_widget()
        hv = QVBoxLayout(hero)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(0)
        greet = QHBoxLayout()
        greet.setSpacing(16)
        greet.addStretch(1)
        self.hero_spark = Spark(42)
        greet.addWidget(self.hero_spark)
        self.greeting = label('', 'greeting')
        self._new_greeting()
        greet.addWidget(self.greeting)
        greet.addStretch(1)
        hv.addLayout(greet)
        hv.addSpacing(24)
        self.hero_slot = QVBoxLayout()
        self.hero_slot.setSpacing(0)
        hv.addLayout(self.hero_slot)
        hv.addSpacing(10)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.addStretch(1)
        self.back_chip = Chip('回到刚才的解析结果', 'list', height=34,
                              tip='点了「解析新链接」后，之前的解析结果还留着；在这里解析新链接时才会清掉')
        self.back_chip.clicked.connect(self._back_to_results)
        self.back_chip.hide()
        chips.addWidget(self.back_chip)
        for text, icon, slot, tip in (
                ('粘贴并解析', 'clipboard', self._paste_and_parse, '解析剪贴板里的链接'),
                ('打开下载文件夹', 'folder', self._open_dir, ''),
                ('B站扫码登录', 'tv', self._bili_clicked, '登录后解锁 1080P 高码率 / 4K 等大会员画质'),
                ('历史记录', 'clock', lambda: self._go('history'), '')):
            chip = Chip(text, icon, tip=tip, height=34)
            chip.clicked.connect(slot)
            chips.addWidget(chip)
            if icon == 'tv':
                self.bili_chip = chip
        chips.addStretch(1)
        hv.addLayout(chips)
        ev.addWidget(centered(hero, 720, (0, 0, 0, 0)))
        ev.addStretch(13)
        self.parse_stack.addWidget(empty)

        # 有结果时：卡片列表（最新的在最上面）+ 底部输入框
        res = clear_widget('page')
        rv = QVBoxLayout(res)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        col = clear_widget()
        self.res_list = QVBoxLayout(col)
        self.res_list.setContentsMargins(0, 6, 0, 18)
        self.res_list.setSpacing(12)
        self.res_list.addStretch(1)
        self.res_scroll = scroll_area(centered(col, COLUMN, (24, 0, 24, 0)))
        rv.addWidget(self.res_scroll, 1)
        dock = clear_widget()
        self.dock_slot = QVBoxLayout(dock)
        self.dock_slot.setContentsMargins(0, 0, 0, 10)
        rv.addWidget(centered(dock, COLUMN + 20, (14, 0, 14, 0)))
        self.parse_stack.addWidget(res)

        # 输入框（只有一个，在两种状态之间移动）
        box = clear_widget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(4)
        self.clip_bar = self._build_clip_bar()
        bv.addWidget(self.clip_bar)
        self.composer = Composer('粘贴视频或网盘分享链接…')
        self.composer.submitted.connect(self._parse_input)
        self.composer.plus.setToolTip('粘贴、从文件导入链接…')
        self.composer.plus.clicked.connect(self._plus_menu)
        self.chip_cover = Chip('封面', 'image', checkable=True, tip='下载视频时，把封面另存为与视频同名的 JPG')
        self.chip_dm = Chip('弹幕', 'danmaku', checkable=True,
                            tip='B站视频同时下载弹幕（.danmaku.ass；可在设置里生成「弹幕版」视频）')
        self.chip_sub = Chip('字幕', 'subtitles', checkable=True,
                             tip='同时下载字幕（B站 CC 字幕、YouTube 字幕等，SRT 格式；语言可在设置里选）')
        for chip, key in ((self.chip_cover, 'save_cover'), (self.chip_dm, 'save_danmaku'), (self.chip_sub, 'save_subs')):
            chip.setChecked(getattr(self.settings, key))
            chip.toggled.connect(lambda on, k=key: self._toggle_setting(k, on))
            self.composer.left.addWidget(chip)
        bv.addWidget(self.composer)
        under = QHBoxLayout()
        under.setContentsMargins(Composer.SHADOW + 2, 0, Composer.SHADOW + 2, 0)
        self.folder_btn = SelectButton('', 'folder')
        self.folder_btn.clicked.connect(self._folder_menu)
        self.preset_btn = SelectButton('')
        self.preset_btn.clicked.connect(self._preset_menu)
        under.addWidget(self.folder_btn)
        under.addStretch(1)
        under.addWidget(self.preset_btn)
        bv.addLayout(under)
        self.composer_box = box
        self.hero_slot.addWidget(box)
        self._update_folder_btn()
        self._update_preset_btn()
        return page

    def _build_clip_bar(self):
        bar = Card(radius=12)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 6, 6)
        h.setSpacing(8)
        self.clip_icon = QLabel()
        h.addWidget(self.clip_icon)
        self.clip_label = ElidedLabel('', 'metaStrong')
        h.addWidget(self.clip_label, 1)
        h.addWidget(button('解析', self._parse_clip, 'primary'))
        x = IconButton('x', '忽略', size=30, icon_size=16)
        x.clicked.connect(self._hide_clip_bar)
        h.addWidget(x)
        bar.setContentsMargins(Composer.SHADOW, 0, Composer.SHADOW, 0)
        wrap = clear_widget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(Composer.SHADOW, 0, Composer.SHADOW, 2)
        wl.addWidget(bar)
        wrap.hide()
        return wrap

    def _update_parse_mode(self):
        n = len(self.rows) + len(self.pan_cards)
        if not n:
            self.fresh = False
        has = bool(n) and not self.fresh       # 有结果、且没点「解析新链接」时显示结果列表
        if hasattr(self, 'back_chip'):
            self.back_chip.setText(f'回到刚才的 {n} 个解析结果')
            self.back_chip.setVisible(self.fresh)
        want = 1 if has else 0
        slot = self.dock_slot if has else self.hero_slot
        if self.composer_box.parentWidget() is not None and slot.indexOf(self.composer_box) < 0:
            other = self.hero_slot if has else self.dock_slot
            other.removeWidget(self.composer_box)
            slot.addWidget(self.composer_box)
        self.composer.set_min_lines(1 if has else 2)
        self.parse_head.setVisible(has)
        if self.parse_stack.currentIndex() != want:
            self.parse_stack.setCurrentIndex(want)
            if not has:
                self._new_greeting()
        self._place_toast()

    def _new_greeting(self):
        self._greet_slot = greetings.slot()
        self.greeting.setText(greeting(self.settings.nickname))

    def _refresh_greeting(self):
        """软件一直开着时，时段变了（比如下午 → 晚上、过了零点）就换一句问候语。"""
        if self.parse_stack.currentIndex() == 0 and getattr(self, '_greet_slot', None) != greetings.slot():
            self._new_greeting()

    def _place_toast(self):
        if not hasattr(self, 'toast'):
            return
        if self.page == 'parse' and self.parse_stack.currentIndex() == 1:
            self.toast.bottom = self.composer_box.height() + 24
        else:
            self.toast.bottom = 40
        if self.toast.isVisible():
            self.toast.place()

    def _update_folder_btn(self):
        d = self.settings.download_dir
        self.folder_btn.setText(os.path.basename(d.rstrip('\\/')) or d)
        self.folder_btn.setToolTip(f'保存到：{d}')

    def _update_preset_btn(self):
        name, _, tip = PRESET_BY_KEY.get(self.settings.preset, PRESET_BY_KEY['pick'])
        self.preset_btn.setText(f'清晰度：{name}')
        self.preset_btn.setToolTip('一键给所有解析结果选清晰度' + (f'\n{tip}' if tip else ''))

    def _preset_value(self):
        return PRESET_BY_KEY.get(self.settings.preset, PRESET_BY_KEY['pick'])[1]

    def _preset_menu(self):
        m = ui.popup_menu(self)
        group = QActionGroup(m)
        for key, name, _, tip in PRESETS:
            a = m.addAction(name + (f'　{tip}' if tip else ''))
            a.setCheckable(True)
            a.setChecked(key == self.settings.preset)
            a.triggered.connect(lambda _=False, k=key: self._set_preset(k))
            group.addAction(a)
        m.exec(self.preset_btn.mapToGlobal(QPoint(0, self.preset_btn.height() + 4)))

    def _set_preset(self, key):
        self.settings.preset = key
        self._save_settings()
        self._update_preset_btn()
        self._apply_preset_all()

    def _folder_menu(self):
        m = ui.popup_menu(self)
        head = m.addAction(self.settings.download_dir)
        head.setEnabled(False)
        m.addSeparator()
        m.addAction(icons.icon('folder', c('text2')), '打开文件夹', self._open_dir)
        m.addAction(icons.icon('settings', c('text2')), '更改保存位置…', self._choose_dir)
        m.exec(self.folder_btn.mapToGlobal(QPoint(0, self.folder_btn.height() + 4)))

    def _plus_menu(self):
        m = ui.popup_menu(self)
        m.addAction(icons.icon('clipboard', c('text2')), '粘贴剪贴板里的链接并解析', self._paste_and_parse)
        m.addAction(icons.icon('file_text', c('text2')), '从文本文件导入链接…', self._import_links)
        m.addSeparator()
        m.addAction(icons.icon('settings', c('text2')), '下载选项…', lambda: self._open_settings('download'))
        m.addAction(icons.icon('user', c('text2')), '网盘账号…', lambda: self._open_settings('network'))
        m.exec(self.composer.plus.mapToGlobal(QPoint(0, self.composer.plus.height() + 4)))

    # 下载页 ----------------------------------------------------------
    def _page_header(self, title, right_widgets=(), sub=None):
        w = clear_widget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 26, 0, 12)
        v.setSpacing(6)
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(label(title, 'pageTitle'))
        h.addStretch(1)
        for x in right_widgets:
            h.addWidget(x)
        v.addLayout(h)
        if sub is not None:
            v.addWidget(sub)
        return w

    def _build_tasks_page(self):
        page = clear_widget('page')
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.tasks_status = ElidedLabel('', 'meta')
        head = self._page_header('下载', [
            button('全部暂停', self._pause_all, icon='pause'), button('全部继续', self._resume_all, icon='play'),
            button('清除已结束', self._clear_finished, icon='trash')], self.tasks_status)
        v.addWidget(centered(head, COLUMN, (32, 0, 32, 0)))
        col = clear_widget()
        self.task_list = QVBoxLayout(col)
        self.task_list.setContentsMargins(0, 4, 0, 24)
        self.task_list.setSpacing(10)
        self.tasks_empty = self._empty_state('还没有下载任务', '在「解析新链接」里粘贴视频或网盘分享链接')
        self.task_list.addWidget(self.tasks_empty)
        self.task_list.addStretch(1)
        v.addWidget(scroll_area(centered(col, COLUMN, (32, 0, 32, 0))), 1)
        return page

    def _empty_state(self, title, sub):
        w = clear_widget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 70, 0, 0)
        lay.setSpacing(10)
        sp = Spark(34, color_role='text4')
        lay.addWidget(sp, 0, Qt.AlignHCenter)
        t = label(title, 'serifSmall')
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = label(sub, 'hint')
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        return w

    # 历史页 ----------------------------------------------------------
    def _build_history_page(self):
        page = clear_widget('page')
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.hist_count = label('', 'meta')
        self.hist_clear_btn = button('清空历史', self._clear_history, 'danger', icon='trash', icon_role='err')
        head = self._page_header('历史记录', [self.hist_clear_btn], self.hist_count)
        hv = head.layout()
        self.hist_search = QLineEdit()
        self.hist_search.setObjectName('search')
        self.hist_search.setPlaceholderText('搜索标题、作者或平台')
        self.hist_search.setClearButtonEnabled(True)
        self._search_action = self.hist_search.addAction(icons.icon('search', c('text3'), 18),
                                                         QLineEdit.LeadingPosition)
        self.hist_search.textChanged.connect(lambda _: self._hist_timer.start())
        self._hist_timer = QTimer(self)
        self._hist_timer.setSingleShot(True)
        self._hist_timer.setInterval(200)
        self._hist_timer.timeout.connect(lambda: self._refresh_history(reset=True))
        hv.addSpacing(8)
        hv.addWidget(self.hist_search)
        # 按分区筛选
        chips = clear_widget()
        self.hist_filters = QHBoxLayout(chips)
        self.hist_filters.setContentsMargins(0, 4, 0, 4)
        self.hist_filters.setSpacing(6)
        fsa = scroll_area(chips)
        fsa.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fsa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        fsa.setFixedHeight(48)
        hv.addSpacing(4)
        hv.addWidget(fsa)
        v.addWidget(centered(head, COLUMN, (32, 0, 32, 0)))
        col = clear_widget()
        self.hist_list = QVBoxLayout(col)
        self.hist_list.setContentsMargins(0, 0, 0, 24)
        self.hist_list.setSpacing(0)
        self.hist_list.addStretch(1)
        self.hist_scroll = scroll_area(centered(col, COLUMN, (32, 0, 32, 0)))
        v.addWidget(self.hist_scroll, 1)
        return page

    # 日志页 ----------------------------------------------------------
    def _build_log_page(self):
        page = clear_widget('page')
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName('log')
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        head = self._page_header('日志', [
            button('复制全部', lambda: (QGuiApplication.clipboard().setText(self.log_view.toPlainText()),
                                        self.notify('日志已复制', 'ok')), icon='copy'),
            button('清空', self.log_view.clear, icon='trash')],
            label('解析、下载的详细过程和报错。反馈问题时可以把这里的内容发过去。', 'meta'))
        v.addWidget(centered(head, COLUMN, (32, 0, 32, 0)))
        card = Card(radius=14)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 8, 6, 8)
        cl.addWidget(self.log_view)
        holder = clear_widget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 24)
        hl.addWidget(card)
        v.addWidget(centered(holder, COLUMN, (32, 0, 32, 0)), 1)
        return page

    # ------------------------------------------------------------ 外观
    def notify(self, text, kind='info'):
        self._place_toast()
        self.toast.show_message(text, kind)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_toast()

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, '_shown_once', False):
            self._shown_once = True
            ui.style_window(self, 'bg')
            self.composer.edit.setFocus()
            # 窗口出来后再在后台加载解析内核，第一次解析时就已经准备好了
            QTimer.singleShot(30, lambda: threading.Thread(target=core.preload, daemon=True).start())

    def apply_theme(self, mode):
        self.settings.theme = mode
        self._save_settings()
        theme.apply(QApplication.instance(), mode)

    def _on_theme_changed(self):
        ui.style_window(self, 'bg')
        _refresh_btn_icons()
        self._search_action.setIcon(icons.icon('search', c('text3'), 18))
        self.clip_icon.setPixmap(icons.pixmap('clipboard', c('text3'), 16))
        for tv in list(self.task_views.values()) + list(self.task_lines.values()):
            if shiboken6.isValid(tv):
                tv.refresh()
        if self.page == 'history':
            self._refresh_history()
        for w in QApplication.allWidgets():
            w.update()

    # ------------------------------------------------------------ 解析
    def _submit_text(self, text):
        """一段文字里的链接：网盘分享交给网盘引擎，其余的交给视频解析。
        返回 (新加入的个数, 识别到的个数)；一个链接都没有时返回 None。"""
        shares = self.pan.detect(text) if pan.has_pan_link(text) else []
        urls = [u for u in core.extract_urls(text) if not pan.is_pan_url(u)]
        if not urls and not shares:
            if pan.has_pan_link(text) and not self.pan.alive:
                self.notify(f'没法解析网盘链接：{self.pan.error or "网盘解析组件还没启动好，请稍后再试"}', 'error')
                return 0, 0
            return None
        if self.fresh:
            # 点过「解析新链接」：这是新的一批，清掉之前的解析结果（正在进行的下载不受影响）
            self._clear_results()
            self.fresh = False
        added = self._submit_urls(urls) if urls else 0
        n_pan = self._pan_submit_links(shares) if shares else 0
        if n_pan:
            self._go('parse')
            QTimer.singleShot(0, lambda: self.res_scroll.verticalScrollBar().setValue(0))
        return added + n_pan, len(urls) + len(shares)

    def _notify_submitted(self, res):
        added, total = res
        if not total:
            return
        skipped = total - added
        self.notify(f'开始解析 {added} 个链接' + (f'（{skipped} 个已在列表中）' if skipped else '') if added
                    else '这些链接已经在列表里了')

    def _paste_and_parse(self):
        text = QGuiApplication.clipboard().text()
        if text.strip():
            self._go('parse')
            self.composer.edit.setPlainText(text)
            self._parse_input()
        else:
            self.notify('剪贴板里没有文字', 'error')

    def _import_links(self):
        path, _ = QFileDialog.getOpenFileName(self, '从文本文件导入链接', '', '文本文件 (*.txt *.csv *.md);;所有文件 (*)')
        if not path:
            return
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError as e:
            self.notify(f'读取失败：{e}', 'error')
            return
        res = self._submit_text(text)
        if res is None:
            self.notify('文件里没有找到链接', 'error')
            return
        self._notify_submitted(res)

    def _parse_input(self):
        res = self._submit_text(self.composer.text())
        if res is None:
            self.notify('没有识别到链接', 'error')
            return
        if res[1]:
            self.composer.clear()
        self._notify_submitted(res)

    def _submit_urls(self, urls, force=False, auto_download=False, from_collection=False):
        if self.fresh and urls:
            # 点过「解析新链接」：这是新的一批，清掉之前的解析结果（正在进行的下载不受影响）
            self._clear_results()
            self.fresh = False
        existing = {r.url for r in self.rows.values() if r.state != 'error'}
        added = 0
        for url in urls:
            if not force and url in existing:
                continue
            existing.add(url)
            # 新解析的放在最上面；一次加入多个时保持原来的先后顺序
            token = self._new_row(url, at=added)
            row = self.rows[token]
            row.auto_download, row.from_collection = auto_download, from_collection
            self._start_parse(token)
            added += 1
        if added:
            self._go('parse')
            QTimer.singleShot(0, lambda: self.res_scroll.verticalScrollBar().setValue(0))
        return added

    def _new_row(self, url, after_token=None, at=None):
        token = self._next_token
        self._next_token += 1
        row = ResultRow(token, url)
        row.card = ResultCard()
        row.card.setContextMenuPolicy(Qt.CustomContextMenu)
        row.card.customContextMenuRequested.connect(
            lambda pos, t=token: self._result_menu(t, self.rows[t].card.mapToGlobal(pos)) if t in self.rows else None)
        self.rows[token] = row
        n = self.res_list.count() - 1          # 最后一项是弹簧
        idx = n if at is None else min(at, n)
        if after_token is not None and after_token in self.rows:
            i = self.res_list.indexOf(self.rows[after_token].card)
            if i >= 0:
                idx = i + 1
        self.res_list.insertWidget(idx, row.card)
        self._render_result(row)
        self._update_res_label()
        return token

    def _start_parse(self, token):
        row = self.rows[token]
        row.state, row.error = 'parsing', ''
        self._render_result(row)
        self._update_res_label()
        self.parser.submit(
            row.url,
            lambda url, video, t=token: self.bridge.parsed.emit(t, video),
            lambda url, msg, raw, t=token: self.bridge.parse_failed.emit(t, msg, raw),
            lambda url, col, t=token: self.bridge.expanded.emit(t, col),
            probe=not row.from_collection,
        )

    def _on_parsed(self, token, video):
        row = self.rows.get(token)
        if row is None:
            return
        if row.state == 'ok':
            # 同一链接解析出多个视频（如带多个视频的推文）：追加新行
            token = self._new_row(row.url, after_token=token)
            row = self.rows[token]
        row.state, row.video = 'ok', video
        self._render_result(row)
        self._update_res_label()
        best = next((o.tag for o in video.options if o.kind == 'video'), '')
        self._record_parse(VIDEO, video_key(video.info), video.title, video.platform, video.uploader,
                           video.info.get('webpage_url') or row.url,
                           ' · '.join(x for x in (fmt_duration(video.duration), f'最高 {best}' if best else '') if x))
        if row.cover_url:
            self.thumbs.request(row.cover_url, video.info.get('webpage_url') or row.url)
        if row.auto_download:
            row.auto_download = False
            self._download_row(token, quiet=True)

    def _on_parse_failed(self, token, msg, raw):
        row = self.rows.get(token)
        if row is None:
            return
        row.state, row.error, row.error_raw = 'error', msg, raw
        self._render_result(row)
        self._update_res_label()
        self._append_log('error', f'解析失败 {row.url}\n    {raw}')

    def _on_expanded(self, token, col):
        """粘贴的是播放列表 / 收藏夹等：先让用户勾选要哪些视频。"""
        row = self.rows.get(token)
        if row is None:
            return
        if row.state == 'parsing':
            self._remove_result(token)
        self._choose_episodes(col)

    def _choose_episodes(self, col):
        dlg = EpisodeDialog(col, {r.url for r in self.rows.values()}, self)
        if not dlg.exec() or not dlg.selected:
            return
        n = self._submit_urls([e['url'] for e in dlg.selected], auto_download=dlg.auto_download, from_collection=True)
        tip = '，解析完自动下载' if dlg.auto_download else ''
        self.notify(f'已加入 {n} 个视频{tip}' if n else '选中的视频都已经在列表里了', 'ok' if n else 'info')

    def _open_collection(self, token):
        row = self.rows.get(token)
        if not (row and row.video and row.video.collection):
            return
        col = row.video.collection
        if col.get('entries') is not None:
            self._choose_episodes(col)
            return
        if col.get('loading'):
            return
        col['loading'] = True
        self.notify(f'正在获取「{col.get("title") or "频道"}」的视频列表…')

        def work():
            from . import collection
            try:
                collection.load_lazy(col, lambda u: core.Parser._flat(u, self.settings))
                self.bridge.collection_loaded.emit(token, col, '')
            except Exception as e:  # noqa: BLE001
                self.bridge.collection_loaded.emit(token, col, core.friendly_error(e)[0])
        threading.Thread(target=work, daemon=True).start()

    def _on_collection_loaded(self, token, col, error):
        col['loading'] = False
        if error:
            self.notify(f'获取视频列表失败：{error}', 'error')
            return
        row = self.rows.get(token)
        if row and row.state == 'ok':
            self._render_result(row)
        self._choose_episodes(col)

    def _render_result(self, row):
        token = row.token
        body = clear_widget()
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)
        cover = CoverView(176, 99, radius=10)
        cover.clicked.connect(lambda: self._preview_cover(token))
        row.cover, row.combo, row.badge, row.dl_btn, row.spark = cover, None, None, None, None
        h.addWidget(cover, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(7)
        h.addLayout(col, 1)

        if row.state == 'ok':
            v = row.video
            cover.set_placeholder(v.platform)
            pm = self.thumbs.get(row.cover_url)
            if pm:
                cover.set_pixmap(pm)
            title = ElidedLabel(v.title, 'cardTitle')
            title.set_full_text(v.title, f'{v.title}\n{row.url}')
            col.addWidget(title)
            line = QHBoxLayout()
            line.setSpacing(6)
            line.addWidget(PlatformTag(v.platform))
            badge = label('', 'tagOk')
            badge.hide()           # 放进卡片后再由 _update_badge 决定是否显示
            row.badge = badge
            line.addWidget(badge)
            if core.has_danmaku(v.info):
                t = label('弹幕', 'tagAccent')
                t.setToolTip('B站弹幕，可转成 ASS 字幕或生成「弹幕版」视频')
                line.addWidget(t)
            human, autos = core.pick_subtitles(v.info, self.settings.sub_langs)
            if human or autos:
                names = [core.sub_name(x) for x in human] + [core.sub_name(x, True) for x in autos]
                all_langs = [x for x in (v.info.get('subtitles') or {}) if x not in ('danmaku', 'live_chat')]
                t = label('字幕 ' + '/'.join(names), 'tagInfo')
                t.setToolTip(f'将下载：{"、".join(names)}\n该视频共有 {len(all_langs)} 种人工字幕')
                line.addWidget(t)
            colinfo = v.collection
            if colinfo and colinfo.get('entries') is None:
                cb = Chip('频道视频 · 选集', 'list', height=24, font_px=12,
                          tip=f'列出频道「{colinfo.get("title") or ""}」的全部视频（最多 1000 个），勾选要下载的\n'
                              '（YouTube 的分享按钮会去掉列表信息；想下载某个播放列表，请复制浏览器地址栏里带 list= 的链接）')
                cb.clicked.connect(lambda: self._open_collection(token))
                line.addWidget(cb)
            elif colinfo:
                pos = f'{colinfo["current"] + 1}/{len(colinfo["entries"])}' if colinfo.get('current', -1) >= 0 \
                    else f'{len(colinfo["entries"])} 个'
                cb = Chip(f'{colinfo["kind"]} {pos} · 选集', 'list', height=24, font_px=12,
                          tip=f'{colinfo["kind"]}「{colinfo.get("title") or ""}」共 {len(colinfo["entries"])} 个视频，'
                              '点击选择要下载哪几个')
                cb.clicked.connect(lambda: self._open_collection(token))
                line.addWidget(cb)
            best = next((o.tag for o in v.options if o.kind == 'video'), '')
            meta_txt = ' · '.join(x for x in (v.uploader, fmt_duration(v.duration), f'最高 {best}' if best else '') if x)
            line.addWidget(ElidedLabel(meta_txt, 'meta'), 1)
            col.addLayout(line)
            col.addStretch(1)

            act = QHBoxLayout()
            act.setSpacing(4)
            combo = Combo()
            for opt in v.options:
                combo.addItem(opt.label, opt)
            if v.locked:
                need = '需要大会员账号' if bilibili.is_logged_in() else '左下角头像菜单里扫码登录 B站'
                combo.addItem(f'🔒 {v.locked}（{need}）')
                combo.model().item(combo.count() - 1).setEnabled(False)
                combo.setToolTip(f'未解锁的清晰度：{v.locked}')
            combo.setMinimumWidth(200)
            combo.setMaximumWidth(380)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row.combo = combo
            self._apply_preset(row)
            act.addWidget(combo, 3)
            act.addStretch(1)
            for icon, tip, slot in (('scissors', '只下载其中一段（输入起止时间）', lambda: self._download_section(token)),
                                    ('image', '保存封面到下载目录', lambda: self._save_cover(token)),
                                    ('more', '更多', None)):
                b = IconButton(icon, tip, size=34, icon_size=18)
                if slot:
                    b.clicked.connect(slot)
                else:
                    b.clicked.connect(lambda _=False, bb=b: self._result_menu(token, bb.mapToGlobal(
                        QPoint(0, bb.height() + 4))))
                if icon == 'image' and not row.cover_url:
                    b.setEnabled(False)
                    b.setToolTip('这个视频没有封面')
                act.addWidget(b)
            act.addSpacing(4)
            dl = button('下载', lambda: self._download_row(token), 'primary', icon='download')
            row.dl_btn = dl
            act.addWidget(dl)
            col.addLayout(act)
        else:
            title = ElidedLabel(row.url, 'cardTitleDim')
            title.set_full_text(row.url)
            col.addWidget(title)
            status = QHBoxLayout()
            status.setSpacing(8)
            if row.state == 'parsing':
                cover.set_placeholder('')
                spark = Spark(36, cover)
                spark.move((cover.width() - 36) // 2, (cover.height() - 36) // 2)
                spark.set_busy(True)
                row.spark = spark
                status.addWidget(label('正在解析…', 'meta'))
                status.addStretch(1)
                col.addLayout(status)
                col.addStretch(1)
                act = QHBoxLayout()
                act.addStretch(1)
                act.addWidget(button('移除', lambda: self._remove_result(token), 'ghost'))
            else:
                cover.set_placeholder('解析失败')
                icon = QLabel()
                icon.setPixmap(icons.pixmap('alert', c('err'), 15))
                status.addWidget(icon)
                err = ElidedLabel(row.error, 'metaError')
                err.set_full_text(row.error, row.error_raw or row.error)
                status.addWidget(err, 1)
                col.addLayout(status)
                col.addStretch(1)
                act = QHBoxLayout()
                act.setSpacing(6)
                act.addStretch(1)
                act.addWidget(button('移除', lambda: self._remove_result(token), 'ghost'))
                act.addWidget(button('重试', lambda: self._start_parse(token), icon='refresh'))
            col.addLayout(act)
        row.card.set_body(body)
        self._update_badge(row)
        # 卡片里的下载进度
        for tid, t in self.task_row.items():
            if t == token and tid not in self.task_lines:
                task = next((x for x in self.manager.tasks if x.id == tid), None)
                if task:
                    self._add_task_line(task, row)

    def _remove_result(self, token):
        row = self.rows.pop(token, None)
        if row and row.card:
            self.res_list.removeWidget(row.card)
            for sp in row.card.findChildren(Spark):
                sp.set_busy(False)
            row.card.deleteLater()
        for tid in [tid for tid, t in self.task_row.items() if t == token]:
            self.task_lines.pop(tid, None)
            self.task_row.pop(tid, None)
        self._update_res_label()

    def _clear_results(self):
        for token in list(self.rows):
            self._remove_result(token)
        self._pan_clear_cards()
        self._update_res_label()

    def _update_res_label(self):
        total = len(self.rows)
        ok = sum(1 for r in self.rows.values() if r.state == 'ok')
        busy = sum(1 for r in self.rows.values() if r.state == 'parsing')
        busy += sum(1 for card in self.pan_cards.values() if shiboken6.isValid(card) and card.state == 'opening')
        bits = [f'{ok} / {total} 个视频' if total else '', f'{len(self.pan_cards)} 个网盘分享' if self.pan_cards else '']
        txt = ' · '.join(b for b in bits if b)
        if busy:
            txt += f' · 正在解析 {busy} 个'
        self.res_label.setText(txt)
        self.side_spark.set_busy(busy > 0)
        self._update_parse_mode()

    # ------------------------------------------------------------ 封面
    def _on_thumb_loaded(self, url):
        pm = self.thumbs.get(url)
        for row in self.rows.values():
            if row.state == 'ok' and row.cover_url == url and row.cover is not None and shiboken6.isValid(row.cover):
                if pm:
                    row.cover.set_pixmap(pm)
                else:
                    row.cover.set_placeholder(row.video.platform)
        for tv in self.task_views.values():
            if shiboken6.isValid(tv) and tv.cover is not None and tv.cover_url == url and pm:
                tv.cover.set_pixmap(pm)
        for title, out_dir in self._pending_cover_saves.pop(url, []):
            self._write_cover(url, title, out_dir)

    def _preview_cover(self, token):
        row = self.rows.get(token)
        if not row or not row.video:
            return
        data = self.thumbs.raw.get(row.cover_url)
        if not data:
            return
        dlg = CoverDialog(data, row.video.title, self.settings.download_dir, self)
        if dlg.exec() and dlg.saved_path:
            self._cover_saved(dlg.saved_path)

    def _save_cover(self, token):
        row = self.rows.get(token)
        if not row or not row.cover_url:
            return
        url = row.cover_url
        if url in self.thumbs.raw:
            self._write_cover(url, row.video.title, self.settings.download_dir)
        elif url in self.thumbs.failed:
            self.notify('封面加载失败，无法保存', 'error')
        else:
            self._pending_cover_saves.setdefault(url, []).append((row.video.title, self.settings.download_dir))
            self.thumbs.request(url, row.video.info.get('webpage_url') or row.url)
            self.notify('正在获取封面…')

    def _write_cover(self, url, title, out_dir):
        data = self.thumbs.raw.get(url)
        if not data:
            self.notify('封面加载失败，无法保存', 'error')
            return
        try:
            self._cover_saved(save_cover_bytes(data, out_dir, title))
        except OSError as e:
            self.notify(f'保存封面失败：{e}', 'error')

    def _cover_saved(self, path):
        self.notify(f'封面已保存：{os.path.basename(path)}', 'ok')
        self._append_log('info', f'封面已保存 {path}')

    # ------------------------------------------------------------ 清晰度预设
    def _apply_preset(self, row):
        preset = self._preset_value()
        combo = row.combo
        if combo is None or combo.count() == 0:
            return
        opts = [combo.itemData(i) for i in range(combo.count())]
        opts = [o for o in opts if o is not None]
        idx = 0
        if preset == 'mp3':
            idx = next((i for i, o in enumerate(opts) if o.convert == 'mp3'), 0)
        elif isinstance(preset, int):
            videos = [(i, o) for i, o in enumerate(opts) if o.kind == 'video']
            fit = [(i, o) for i, o in videos if o.res and o.res <= preset]
            if fit:
                idx = fit[0][0]
            elif videos:
                idx = videos[-1][0]
        combo.setCurrentIndex(idx)

    def _apply_preset_all(self):
        for row in self.rows.values():
            if row.state == 'ok':
                self._apply_preset(row)

    # ------------------------------------------------------------ 下载
    def _download_row(self, token, quiet=False):
        row = self.rows.get(token)
        if not row or row.state != 'ok' or row.combo is None:
            return False
        option = row.combo.currentData()
        if option is None:
            return False
        vid = row.video.info.get('id')
        for t in self.manager.tasks:
            if t.video.info.get('id') == vid and t.option.format == option.format and t.status in (
                    core.QUEUED, core.RUNNING, core.MERGING, core.PAUSED):
                if not quiet:
                    self.notify('这个视频的该清晰度已经在下载列表里了')
                return False
        overwrite = False
        done_before = [h for h in self._history_of(row.video) if h['quality'] == option.tag and h['exists']]
        if done_before:
            if quiet:
                return False
            when = datetime.datetime.fromtimestamp(done_before[0]['ts']).strftime('%m-%d %H:%M')
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setText(f'这个视频的「{option.tag}」已经在 {when} 下载过了。')
            box.setInformativeText(os.path.basename(done_before[0]['filepath']))
            open_btn = box.addButton('打开已有文件', QMessageBox.AcceptRole)
            again_btn = box.addButton('重新下载', QMessageBox.DestructiveRole)
            box.addButton('取消', QMessageBox.RejectRole)
            restyle(open_btn, 'primary')
            box.exec()
            if box.clickedButton() is open_btn:
                os.startfile(done_before[0]['filepath'])
                return False
            if box.clickedButton() is not again_btn:
                return False
            overwrite = True
        task = core.DownloadTask(row.video, option, self.settings.download_dir, save_cover=self.settings.save_cover)
        task.overwrite = overwrite
        task.save_subs = self.settings.save_subs
        task.save_danmaku = self.settings.save_danmaku
        self.task_row[task.id] = token
        self.manager.add(task)
        if not quiet:
            self._flash_download_button(token)
        return True

    def _download_section(self, token):
        row = self.rows.get(token)
        if not row or row.state != 'ok':
            return
        dlg = SectionDialog(row.video, self)
        if row.combo is not None and row.combo.currentData() is not None:
            dlg.set_quality(row.combo.currentIndex())
        if not dlg.exec():
            return
        task = core.DownloadTask(row.video, dlg.option, self.settings.download_dir, save_cover=self.settings.save_cover)
        task.section, task.precise = dlg.section, dlg.precise
        task.save_subs = False
        task.save_danmaku = self.settings.save_danmaku
        self.task_row[task.id] = token
        self.manager.add(task)
        self.notify('已加入片段下载', 'ok')

    def _flash_download_button(self, token):
        """点击「下载」后按钮立即变成绿色「已加入」，1.2 秒后恢复。"""
        row = self.rows.get(token)
        btn = row.dl_btn if row else None
        if btn is None or not shiboken6.isValid(btn):
            return
        btn.setText('已加入')
        set_btn_icon(btn, 'check', 'on_primary')
        restyle(btn, 'done')

        def revert():
            if shiboken6.isValid(btn):
                btn.setText('下载')
                set_btn_icon(btn, 'download', 'on_primary')
                restyle(btn, 'primary')
        QTimer.singleShot(1200, revert)

    def _download_all(self):
        n = skipped = 0
        for token, row in list(self.rows.items()):
            if self._download_row(token, quiet=True):
                n += 1
            elif row.state == 'ok' and row.combo is not None and row.combo.currentData() is not None and any(
                    h['quality'] == row.combo.currentData().tag and h['exists'] for h in self._history_of(row.video)):
                skipped += 1
        # 网盘分享：还没下载过、不需要先登录的，按卡片里勾选的文件下载
        for card in list(self.pan_cards.values()):
            if shiboken6.isValid(card) and card.state == 'list' and card.tree is not None and not card.adding                     and not (card.result or {}).get('downloadNote') and card.lines.count() == 0 and card.tree.picked():
                card.download()
                n += 1
        if n:
            msg = f'已添加 {n} 个下载任务' + (f'，跳过 {skipped} 个已下载过的' if skipped else '')
        else:
            msg = f'这 {skipped} 个视频都已经下载过了（单独点「下载」可以重新下载）' if skipped else '没有可下载的结果'
        self.notify(msg, 'ok' if n else 'info')

    def _add_task_line(self, task, row):
        if row.card is None or not shiboken6.isValid(row.card):
            return
        line = TaskView(self, task, compact=True)
        row.card.tasks.addWidget(line)
        self.task_lines[task.id] = line

    def _on_task_updated(self, task):
        if task.id not in self.task_views:
            if task not in self.manager.tasks:
                return
            view = TaskView(self, task, compact=False)
            self.task_list.insertWidget(0, view)          # 新任务放最上面
            self.task_views[task.id] = view
            self.tasks_empty.hide()
            token = self.task_row.get(task.id)
            if token in self.rows and task.id not in self.task_lines:
                self._add_task_line(task, self.rows[token])
        s = task.status
        prev = self._prev_status.get(task.id)
        if prev != s:
            self._prev_status[task.id] = s
            self._on_status_changed(task, prev)
        for views in (self.task_views, self.task_lines):
            v = views.get(task.id)
            if v is not None:
                if shiboken6.isValid(v):
                    v.refresh()
                else:
                    views.pop(task.id, None)

    def _task_primary(self, task):
        s = task.status
        if s in (core.QUEUED, core.RUNNING):
            self.manager.pause(task)
        elif s in (core.PAUSED, core.FAILED, core.CANCELED):
            self.manager.resume(task)
        elif s in (core.DONE, core.EXISTS):
            self._open_file(task)

    def _task_secondary(self, task):
        if task.status in (core.DONE, core.EXISTS, core.CANCELED, core.FAILED):
            self._remove_task(task)
        else:
            self.manager.cancel(task)

    def _save_tasks(self):
        try:
            task_store.save(self.manager.tasks)
        except Exception as e:  # noqa: BLE001
            log.warning('保存未完成任务失败: %s', e)

    def _restore_tasks(self):
        tasks = task_store.load()
        for t in tasks:
            self.manager.restore(t)
        if tasks:
            self.notify(f'已恢复上次未完成的 {len(tasks)} 个任务（已暂停），在「下载」页点「全部继续」')

    def _toggle_setting(self, name, value):
        setattr(self.settings, name, value)
        self._save_settings()

    def _remove_task(self, task):
        if self.manager.remove(task):
            self._tasks_save_timer.start()
            for views in (self.task_views, self.task_lines):
                v = views.pop(task.id, None)
                if v is not None and shiboken6.isValid(v):
                    v.setParent(None)
                    v.deleteLater()
            self.task_row.pop(task.id, None)
            self.tasks_empty.setVisible(not (self.task_views or self.pan_views))

    def _open_file(self, task):
        # 有弹幕版时优先打开弹幕版（弹幕流畅，任何播放器都能看）
        path = task.burned_path if task.burned_path and os.path.exists(task.burned_path) else task.filepath
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            self.notify('文件不存在（可能已被移动或删除）', 'error')

    def _pause_all(self):
        n = 0
        for t in list(self.manager.tasks):
            if t.status in (core.QUEUED, core.RUNNING):
                self.manager.pause(t)
                n += 1
        pc = self._pan_counts()
        if pc['running'] + pc['queued']:
            self.pan.call('pauseAll')
            n += pc['running'] + pc['queued']
        self.notify(f'已暂停 {n} 个任务' if n else '没有进行中的任务')

    def _resume_all(self):
        n = 0
        for t in list(self.manager.tasks):
            if t.status == core.PAUSED:
                self.manager.resume(t)
                n += 1
        pc = self._pan_counts()
        if pc['paused']:
            self.pan.call('resumeAll')
            n += pc['paused']
        self.notify(f'已继续 {n} 个任务' if n else '没有暂停的任务')

    def _clear_finished(self):
        for t in list(self.manager.tasks):
            if t.status in (core.DONE, core.EXISTS, core.CANCELED):
                self._remove_task(t)
        self._pan_clear_finished()

    # ------------------------------------------------------------ 下载历史
    def _history_of(self, video):
        if not self.history or not video:
            return []
        try:
            return self.history.lookup(video.info)
        except Exception:  # noqa: BLE001
            return []

    def _update_badge(self, row):
        badge = row.badge
        if badge is None or not shiboken6.isValid(badge):
            return
        recs = [h for h in self._history_of(row.video) if h['exists']]
        if not recs:
            badge.hide()
            return
        qualities = '、'.join(dict.fromkeys(h['quality'].replace('] [', ' · ') for h in recs))
        badge.setText(f'✓ 已下载 {qualities}')
        badge.setToolTip('\n'.join(
            f'{datetime.datetime.fromtimestamp(h["ts"]).strftime("%Y-%m-%d %H:%M")}  {h["filepath"]}' for h in recs))
        badge.show()

    def _on_status_changed(self, task, prev):
        self._tasks_save_timer.start()
        s = task.status
        if s in (core.DONE, core.EXISTS) and prev not in (core.DONE, core.EXISTS):
            if self.history and task.filepath:
                try:
                    self.history.add(task)
                except Exception as e:  # noqa: BLE001
                    log.warning('写入下载历史失败: %s', e)
                key_rows = [r for r in self.rows.values() if r.video and r.video.info.get('id') == task.video.info.get('id')]
                for r in key_rows:
                    self._update_badge(r)
                self._refresh_sidebar()
                if self.page == 'history':
                    self._refresh_history()
            if s == core.DONE:
                self._batch_done += 1
                if self.settings.notify_done and not self.isActiveWindow():
                    self._tray_message('下载完成', task.video.title)
        elif s == core.FAILED and prev != core.FAILED:
            if self.settings.notify_done and not self.isActiveWindow():
                self._tray_message('下载失败', f'{task.video.title}\n{task.error}', QSystemTrayIcon.Warning)
        if s in (core.DONE, core.EXISTS, core.FAILED, core.CANCELED, core.PAUSED):
            self._check_all_done()

    def _check_all_done(self):
        if any(t.status in (core.QUEUED, core.RUNNING, core.MERGING) for t in self.manager.tasks):
            return
        if not self._batch_done:
            return
        n, self._batch_done = self._batch_done, 0
        failed = sum(1 for t in self.manager.tasks if t.status == core.FAILED)
        msg = f'全部下载完成，共 {n} 个' + (f'，{failed} 个失败' if failed else '')
        self.notify(msg, 'ok')
        if self.settings.notify_done and not self.isActiveWindow() and n > 1:
            self._tray_message('全部下载完成', msg)
        if self.settings.notify_sound:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:  # noqa: BLE001
                QApplication.beep()
        if self.settings.open_dir_when_done:
            self._open_dir()

    def _refresh_history(self, reset=False):
        """历史记录页：解析记录（全部 / 视频 / 网盘 / 某个网盘分区），或者下载好的视频（全部 / 某个视频分区）。"""
        if reset:
            self._hist_limit = 150
        lay = self.hist_list
        while lay.count() > 1:
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not self.history:
            self.hist_count.setText('历史记录不可用')
            return
        self._refresh_hist_filters()
        search = self.hist_search.text().strip()
        sec = self.sections.get(self.hist_section) if self.hist_section else None
        downloads = self.hist_mode == 'downloads'
        if downloads:
            recs = self.history.recent(search, limit=self._hist_limit + 1, section=self.hist_section, kind=VIDEO)
            total = self.history.count(self.hist_section, VIDEO)
            head = f'「{sec["name"]}」分区 {total} 个下载好的视频' if sec else f'下载好的视频 {total} 个'
        else:
            kind = None if sec else self.hist_kind
            recs = self.history.parses(search, limit=self._hist_limit + 1, kind=kind, section=self.hist_section)
            total = self.history.parse_count(kind, self.hist_section)
            what = {VIDEO: '视频', PAN: '网盘分享'}.get(kind, '')
            head = (f'「{sec["name"]}」分区 {total} 个网盘分享，点一下重新解析' if sec else
                    f'共 {total} 条{what}解析记录，点一下重新解析')
        more = len(recs) > self._hist_limit
        recs = recs[:self._hist_limit]
        self.hist_count.setText(head + (f'；找到 {len(recs)}{"+" if more else ""} 条' if search else ''))
        self.hist_clear_btn.setText('清空下载记录' if downloads else '清空历史')
        i = 0
        for h in recs:
            if i:
                lay.insertWidget(i, HLine(margin=12))
                i += 1
            lay.insertWidget(i, HistoryRow(self, h) if downloads else ParseRow(self, h))
            i += 1
        if more:
            b = button('显示更多', self._more_history, 'ghost')
            lay.insertWidget(i, b, 0, Qt.AlignHCenter)
            i += 1
        if not recs:
            if search:
                t, sub = '没有找到记录', '换个关键词试试'
            elif downloads:
                t, sub = '还没有下载好的视频', '视频下载完成后会记录在这里，方便再次打开'
            else:
                t, sub = '还没有解析记录', '解析过的视频和网盘分享会记录在这里，点一下就能重新解析'
            lay.insertWidget(0, self._empty_state(t, sub))

    def _refresh_hist_filters(self):
        """历史记录页顶部的筛选：全部 / 视频 / 网盘 是解析记录，「已下载的视频」是下载好的视频；
        选了网盘或已下载的视频后，再列出对应的分区。"""
        self._clear_layout(self.hist_filters)
        if self.history:
            self.sections = {s['id']: s for s in self.history.sections()}
        if self.hist_section not in self.sections:
            self.hist_section = None
        sec = self.sections.get(self.hist_section)
        downloads = self.hist_mode == 'downloads'
        kind = None if downloads else (sec['kind'] if sec else self.hist_kind)
        h = self.history
        n = (lambda **kw: h.parse_count(**kw)) if h else (lambda **kw: 0)
        chips = [(f'全部 {n()}', 'list', not downloads and kind is None, lambda: self._filter_kind(None)),
                 (f'视频 {n(kind=VIDEO)}', 'film', not downloads and kind == VIDEO, lambda: self._filter_kind(VIDEO)),
                 (f'网盘 {n(kind=PAN)}', 'folder', not downloads and kind == PAN and sec is None,
                  lambda: self._filter_kind(PAN)),
                 (f'已下载的视频 {h.count(kind=VIDEO) if h else 0}', 'download', downloads and sec is None,
                  self._show_downloads_here)]
        group = VIDEO if downloads else (PAN if kind == PAN else None)
        if group:
            chips.append(None)           # 分隔
            chips += [(f'{s["name"]} {s["count"]}', s['icon'], s['id'] == self.hist_section,
                       lambda sid=s['id']: self._filter_history(sid))
                      for s in self.sections.values() if s['kind'] == group]
        for item in chips:
            if item is None:
                bar = QFrame()
                bar.setFixedSize(1, 20)
                col = c('border_strong')
                bar.setStyleSheet(f'background: rgba({col.red()}, {col.green()}, {col.blue()}, {col.alpha()});')
                self.hist_filters.addSpacing(4)
                self.hist_filters.addWidget(bar, 0, Qt.AlignVCenter)
                self.hist_filters.addSpacing(4)
                continue
            text, icon, on, slot = item
            chip = Chip(text, icon, checkable=True, height=32)
            chip.setChecked(on)
            chip.clicked.connect(lambda _=False, f=slot: f())
            self.hist_filters.addWidget(chip)
        self.hist_filters.addStretch(1)

    def _filter_kind(self, kind):
        self.hist_mode, self.hist_kind, self.hist_section = 'parses', kind, None
        self._refresh_history(reset=True)

    def _show_downloads_here(self):
        self.hist_mode, self.hist_kind, self.hist_section = 'downloads', None, None
        self._refresh_history(reset=True)

    def _filter_history(self, sid):
        kind = self.sections.get(sid, {}).get('kind') if sid else None
        self.hist_section = sid
        self.hist_mode = 'downloads' if kind == VIDEO else 'parses'
        if kind == PAN:
            self.hist_kind = PAN
        self._refresh_history(reset=True)

    def _more_history(self):
        self._hist_limit += 300
        self._refresh_history()

    def _reparse_url(self, url):
        """重新解析一条记录的链接；已经在解析列表里的，直接跳到那张卡片。"""
        self._pan_last_hit = None
        res = self._submit_text(url) if url else None
        if res and res[0]:
            self.notify('已加入解析列表')
        elif res and res[1]:
            if self.fresh:
                self._back_to_results()
            else:
                self._go('parse')
            card = self._pan_last_hit or next((r.card for r in self.rows.values() if r.url == url), None)
            if card is not None and shiboken6.isValid(card):
                QTimer.singleShot(50, lambda: shiboken6.isValid(card) and self.res_scroll.ensureWidgetVisible(card, 0, 40))
            self.notify('已经在解析列表里了')

    def _delete_history(self, row_id):
        self.history.delete(row_id)
        self._after_history_change()

    def _clear_history(self):
        if not self.history:
            return
        if self.hist_mode == 'downloads':
            if QMessageBox.question(self, APP_NAME, '确定清空全部视频下载记录吗？（只删除记录，不会删除已下载的文件；'
                                                    '侧边栏「最近下载」和视频分区里的记录也会清空）') != QMessageBox.Yes:
                return
            self.history.clear()
        else:
            kind = self.hist_kind
            what = {VIDEO: '视频的', PAN: '网盘分享的'}.get(kind, '全部')
            extra = '（网盘分区里保存的分享链接也会清空）' if kind != VIDEO else ''
            if QMessageBox.question(self, APP_NAME, f'确定清空{what}解析记录吗？{extra}') != QMessageBox.Yes:
                return
            self.history.clear_parses(kind)
        self._after_history_change()

    def _after_history_change(self):
        if self.page == 'history':
            self._refresh_history()
        self._refresh_sidebar()
        for r in self.rows.values():
            self._update_badge(r)

    # ------------------------------------------------------------ 托盘 / 系统通知
    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(app_icon_path()) if app_icon_path() else self.windowIcon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu(self)
        show = QAction('显示主窗口', self)
        show.triggered.connect(self._show_from_tray)
        clip = QAction('解析剪贴板里的链接', self)
        clip.triggered.connect(self._hotkey_parse)
        quit_ = QAction('退出', self)
        quit_.triggered.connect(self._quit)
        menu.addAction(show)
        menu.addAction(clip)
        menu.addSeparator()
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_from_tray() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.messageClicked.connect(self._show_from_tray)
        self.tray.show()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self):
        self._really_quit = True
        self.close()

    def _hotkey_parse(self):
        """全局快捷键 / 托盘菜单：弹出窗口并解析剪贴板里的链接。"""
        self._show_from_tray()
        text = QGuiApplication.clipboard().text()
        self._last_clip = text
        self._hide_clip_bar()
        res = self._submit_text(text)
        if res is None:
            self.notify('剪贴板里没有链接', 'error')
            return
        self._notify_submitted(res)

    def _apply_hotkey(self, silent=False):
        seq = (self.settings.hotkey or '').strip()
        if not seq:
            self.hotkey.unregister()
            return
        if not self.hotkey.register(seq):
            msg = f'全局快捷键 {seq} 注册失败（可能被其它程序占用），可在设置里换一个'
            self._append_log('warning', msg)
            if not silent:
                self.notify(msg, 'error')
            else:
                QTimer.singleShot(1500, lambda: self.notify(msg, 'error'))

    def _check_core_update(self):
        from . import core_update

        def work():
            try:
                cur = core_update.current_version()
                new, _ = core_update.latest_version()
                if cur != '未知' and core_update._norm(new) > core_update._norm(cur):
                    self.bridge.log.emit('info', f'解析内核有新版本 {new}（当前 {cur}）')
                    self.bridge.core_update.emit(new, cur)
            except Exception:  # noqa: BLE001 - 检查失败不打扰
                pass
        threading.Thread(target=work, daemon=True).start()

    def _on_core_update(self, new, cur):
        self.notify(f'解析内核有新版本 {new}（当前 {cur}），可在 设置 → 解析内核 里一键更新')

    def _check_app_update(self):
        """启动时在后台看一下 GitHub 上有没有软件新版本；失败不打扰。"""
        from . import app_update

        def work():
            try:
                rel = app_update.check('windows')
            except Exception as e:  # noqa: BLE001
                self.bridge.log.emit('info', f'检查软件新版本失败：{e}')
                return
            if rel and rel['version'] != self.settings.skip_app_version:
                self.bridge.log.emit('info', f'软件有新版本 {rel["version"]}（当前 {APP_VERSION}）')
                self.bridge.app_update.emit(rel)
        threading.Thread(target=work, daemon=True).start()

    def _on_app_update(self, rel):
        box = QMessageBox(self)
        box.setWindowTitle('软件更新')
        box.setIcon(QMessageBox.Information)
        box.setText(f'{APP_NAME} 有新版本 {rel["version"]}（当前 {APP_VERSION}）')
        box.setInformativeText('打开下载页，下载新的安装包装在原来的位置就是升级，设置和历史记录都会保留。')
        go = box.addButton('去下载', QMessageBox.AcceptRole)
        box.addButton('以后再说', QMessageBox.RejectRole)
        skip = box.addButton('跳过这个版本', QMessageBox.DestructiveRole)
        box.setDefaultButton(go)
        box.exec()
        if box.clickedButton() is go:
            QDesktopServices.openUrl(QUrl(rel['page']))
        elif box.clickedButton() is skip:
            self.settings.skip_app_version = rel['version']
            self._save_settings()

    def _tray_message(self, title, text, icon=QSystemTrayIcon.Information):
        if self.tray:
            self.tray.showMessage(title, text, icon, 5000)

    # ------------------------------------------------------------ 右键菜单
    def _result_menu(self, token, pos):
        row = self.rows.get(token)
        if not row:
            return
        m = ui.popup_menu(self)
        ic = lambda name: icons.icon(name, c('text2'))   # noqa: E731
        if row.state == 'ok':
            m.addAction(ic('download'), '下载', lambda: self._download_row(token))
            m.addAction(ic('scissors'), '下载片段…', lambda: self._download_section(token))
            if row.cover_url:
                m.addAction(ic('image'), '保存封面', lambda: self._save_cover(token))
                if row.cover_url in self.thumbs.raw:
                    m.addAction(ic('search'), '预览封面', lambda: self._preview_cover(token))
            m.addSeparator()
        m.addAction(ic('refresh'), '重新解析', lambda: self._start_parse(token))
        m.addAction(ic('copy'), '复制链接', lambda: QGuiApplication.clipboard().setText(row.url))
        m.addAction(ic('external'), '在浏览器中打开', lambda: QDesktopServices.openUrl(QUrl(row.url)))
        if row.state == 'error':
            m.addAction(ic('copy'), '复制错误信息', lambda: QGuiApplication.clipboard().setText(row.error_raw or row.error))
        m.addSeparator()
        m.addAction(ic('x'), '移除', lambda: self._remove_result(token))
        m.exec(pos)

    def _task_menu(self, task, pos):
        m = ui.popup_menu(self)
        ic = lambda name: icons.icon(name, c('text2'))   # noqa: E731
        if task.status in (core.DONE, core.EXISTS):
            m.addAction(ic('play_circle'), '打开文件', lambda: self._open_file(task))
            if task.burned_path and os.path.exists(task.burned_path) and task.filepath and task.filepath != task.burned_path:
                m.addAction(ic('film'), '打开原视频（不带弹幕）', lambda: os.startfile(task.filepath))
        m.addAction(ic('folder'), '打开所在文件夹',
                    lambda: reveal_in_explorer(task.filepath or task._expected_name or task.out_dir))
        if task.status in (core.FAILED, core.CANCELED, core.PAUSED):
            m.addAction(ic('refresh'), '重试 / 继续', lambda: self.manager.resume(task))
        m.addAction(ic('copy'), '复制原链接', lambda: QGuiApplication.clipboard().setText(task.video.url))
        if task.error_raw:
            m.addAction(ic('copy'), '复制错误信息', lambda: QGuiApplication.clipboard().setText(task.error_raw))
        if task.status not in (core.RUNNING, core.MERGING, core.QUEUED, core.PAUSED):
            m.addSeparator()
            m.addAction(ic('trash'), '从列表移除', lambda: self._remove_task(task))
        m.exec(pos)

    # ------------------------------------------------------------ 目录 / 设置
    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选择保存位置', self.settings.download_dir)
        if d:
            self._set_dir(os.path.normpath(d))

    def _set_dir(self, d):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f'无法使用该目录：{e}')
            return False
        self.settings.download_dir = d
        self._save_settings()
        self._update_folder_btn()
        self.notify('新任务将保存到这个目录')
        return True

    def _open_dir(self):
        d = self.settings.download_dir
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def _open_settings(self, page=None):
        if self.settings_panel is None:
            self.settings_panel = SettingsPanel(self.central, self)
        self.settings_panel.open_at(page)

    def setting_changed(self, key, old=None):
        """设置面板里改了某一项（立即生效）。"""
        s = self.settings
        if key == 'codec_preference':
            for row in self.rows.values():
                if row.state == 'ok':
                    row.video.options = core.build_options(row.video.info, s.codec_preference)
                    self._render_result(row)
        elif key == 'sub_langs':
            for row in self.rows.values():
                if row.state == 'ok':
                    self._render_result(row)
        elif key in ('save_cover', 'save_danmaku', 'save_subs'):
            chip = {'save_cover': self.chip_cover, 'save_danmaku': self.chip_dm, 'save_subs': self.chip_sub}[key]
            chip.blockSignals(True)
            chip.setChecked(getattr(s, key))
            chip.blockSignals(False)
            chip.update()
        elif key == 'max_concurrent':
            self.manager._schedule()
        elif key == 'hotkey':
            if (s.hotkey or '') != self.hotkey.current:
                self._apply_hotkey()
        elif key == 'nickname':
            if self.parse_stack.currentIndex() == 0:
                self._new_greeting()
            self._update_account()
        elif key == 'download_dir':
            self._update_folder_btn()
        elif key == 'proxy':
            self._pan_apply_proxy()
        elif key == 'avatar':
            self._update_account()
            if self.settings_panel is not None:
                self.settings_panel.refresh_avatar()
            self.notify('头像已更新' if avatar.exists() else '已移除头像')
            return
        self._save_settings()

    def _save_settings(self):
        try:
            self.settings.save()
        except OSError as e:
            log.warning('保存设置失败: %s', e)

    # ------------------------------------------------------------ B站登录
    def _check_bili(self):
        if not bilibili.is_logged_in():
            self._on_bili_status(None)
            return

        def work():
            try:
                info = bilibili.ensure_fresh()      # 需要时自动续期登录
            except Exception:  # noqa: BLE001 - 网络问题时先当作已登录
                info = {'name': '', 'vip': False, 'vip_label': '', 'offline': True}
            if info and info.get('refresh') == 'ok':
                self.bridge.log.emit('info', 'B站登录已自动续期')
            self.bridge.bili_status.emit(info or {'expired': True})
        threading.Thread(target=work, daemon=True).start()

    def _on_bili_status(self, info):
        self.bili_account = info
        self._update_account()
        if info and not info.get('expired') and not info.get('offline'):
            exp = bilibili.sessdata_expires()
            left = (exp - time.time()) / 86400 if exp else None
            if left is not None and left < 1.5:
                self.notify('B站登录快过期了，点左下角头像重新扫码即可续上', 'error')
        if self.settings_panel is not None:
            self.settings_panel.refresh_account()

    def _bili_clicked(self):
        info = self.bili_account
        logged = info and not info.get('expired')
        m = ui.popup_menu(self)
        m.addAction(icons.icon('tv', c('text2')), '长效登录（电视端扫码，约 180 天，推荐）', lambda: self._bili_login(True))
        m.addAction(icons.icon('tv', c('text2')), '网页扫码登录（约 7 天）', lambda: self._bili_login(False))
        if logged:
            m.addSeparator()
            m.addAction(icons.icon('log_out', c('text2')), '退出登录', self._bili_logout)
        src = self.bili_chip
        m.exec(src.mapToGlobal(QPoint(0, src.height() + 4)))

    def _bili_login(self, long_term=True):
        dlg = BiliLoginDialog(self, long_term=long_term)
        if dlg.exec() and dlg.account is not None:
            self._on_bili_status(dlg.account)
            vip = dlg.account.get('vip')
            n = self._reparse_bilibili()
            msg = 'B站登录成功' + ('（大会员）' if vip else '（非大会员，1080P+ / 4K 仍需开通大会员）')
            if n:
                msg += f'，已重新解析 {n} 个 B站视频'
            self.notify(msg, 'ok')

    def _bili_logout(self):
        if QMessageBox.question(self, APP_NAME, '确定退出 B站登录吗？') != QMessageBox.Yes:
            return
        threading.Thread(target=bilibili.logout, daemon=True).start()
        self._on_bili_status(None)
        self.notify('已退出 B站登录')

    def _reparse_bilibili(self):
        n = 0
        for token, row in list(self.rows.items()):
            platform = row.video.platform if row.video else ''
            if platform.startswith('B站') or 'bilibili.com' in row.url or 'b23.tv' in row.url:
                self._start_parse(token)
                n += 1
        return n

    # ------------------------------------------------------------ 剪贴板
    def _on_app_state(self, state):
        if state == Qt.ApplicationActive:
            self._refresh_greeting()
        if state != Qt.ApplicationActive or not self.settings.auto_parse_clipboard:
            return
        text = QGuiApplication.clipboard().text()
        if not text or text == self._last_clip:
            return
        self._last_clip = text
        known = {r.url for r in self.rows.values()}
        urls = [u for u in core.extract_urls(text) if u not in known]
        if not urls:
            return
        self._clip_urls, self._clip_text = urls, text
        self.clip_label.set_full_text(f'剪贴板里有新链接：{urls[0]}' if len(urls) == 1
                                      else f'剪贴板里有 {len(urls)} 个新链接：{urls[0]} …', '\n'.join(urls))
        self.clip_icon.setPixmap(icons.pixmap('clipboard', c('text3'), 16))
        self.clip_bar.show()
        QTimer.singleShot(0, self._place_toast)

    def _parse_clip(self):
        text, self._clip_urls, self._clip_text = getattr(self, '_clip_text', ''), [], ''
        self._hide_clip_bar()
        res = self._submit_text(text)
        if res is not None:
            self._notify_submitted(res)

    def _hide_clip_bar(self):
        self.clip_bar.hide()
        QTimer.singleShot(0, self._place_toast)

    # ------------------------------------------------------------ 日志 / 状态
    def _append_log(self, level, msg):
        if level == 'info' and msg.startswith('[任务') and '[download]' in msg and '%' in msg:
            return
        prefix = {'warning': '⚠ ', 'error': '✖ '}.get(level, '')
        self.log_view.appendPlainText(prefix + msg)
        getattr(log, 'error' if level == 'error' else 'warning' if level == 'warning' else 'info')(msg)

    def _refresh_status(self):
        tasks = self.manager.tasks
        running = [t for t in tasks if t.status in (core.RUNNING, core.MERGING)]
        queued = sum(1 for t in tasks if t.status == core.QUEUED)
        done = sum(1 for t in tasks if t.status in (core.DONE, core.EXISTS))
        failed = sum(1 for t in tasks if t.status == core.FAILED)
        paused = sum(1 for t in tasks if t.status == core.PAUSED)
        speed = sum(t.speed or 0 for t in running if t.status == core.RUNNING)
        pc = self._pan_counts()
        n_run = len(running) + pc['running']
        queued += pc['queued']
        done += pc['done']
        failed += pc['failed']
        paused += pc['paused']
        speed += self.pan.total_speed or 0
        parts = [f'下载中 {n_run}', f'等待 {queued}', f'完成 {done}']
        if paused:
            parts.append(f'暂停 {paused}')
        if failed:
            parts.append(f'失败 {failed}')
        if speed:
            parts.append(fmt_speed(speed))
        self.tasks_status.set_full_text('  ·  '.join(parts) + f'　　保存到 {self.settings.download_dir}')
        active = n_run + queued
        self.nav_tasks.set_badge(str(active) if active else '')
        self._refresh_greeting()
        self.parse_status.setText(f'下载中 {n_run} 个 · {fmt_speed(speed)}' if speed else
                                  (f'下载中 {n_run} 个' if n_run else ''))

    def closeEvent(self, event):
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        if not self._really_quit and self.settings.close_to_tray and self.tray:
            # 缩小到托盘：下载继续
            event.ignore()
            self.hide()
            self._save_settings()
            self._save_tasks()
            if not self.settings.tray_hint_shown:
                self.settings.tray_hint_shown = True
                self._save_settings()
                self._tray_message(APP_NAME, '软件已缩小到托盘，下载会继续进行。\n'
                                             f'按 {self.settings.hotkey or "托盘菜单"} 可以解析剪贴板里的链接；'
                                             '右键托盘图标选「退出」才会真正退出。')
            return
        self._really_quit = False
        active = [t for t in self.manager.tasks if t.status in (core.RUNNING, core.MERGING, core.QUEUED)]
        pc = self._pan_counts()
        n_pan = pc['running'] + pc['queued']
        if active or n_pan:
            ret = QMessageBox.question(
                self, APP_NAME, f'还有 {len(active) + n_pan} 个任务未完成，确定退出吗？\n'
                                '（已下载的部分会保留：视频下次下载同一清晰度时自动续传，网盘文件下次打开软件自动继续）')
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            for t in active:
                t._stop = 'pause'
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self._save_settings()
        self._save_tasks()
        self.pan.stop()
        self.hotkey.unregister()
        if self.tray:
            self.tray.hide()
        event.accept()
        QApplication.instance().quit()


def _setup_logging():
    log_dir = os.path.join(DATA, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(os.path.join(log_dir, 'app.log'), maxBytes=1_000_000, backupCount=2,
                                      encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    except OSError:
        pass


def setup_app(argv):
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setStyle('Fusion')
    theme.load_fonts()
    f = QFont()
    f.setFamilies(theme.ui_families())      # 英文 VDSans，中文微软雅黑（和 Claude 一样）
    f.setPixelSize(13)
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    theme.apply(app, Settings.load().theme)
    app._chrome = ui.ChromeFilter(app)
    app.installEventFilter(app._chrome)
    try:     # 跟随系统深浅色
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _: theme.mode() == 'system' and theme.apply(app))
    except Exception:  # noqa: BLE001
        pass
    if app_icon_path():
        app.setWindowIcon(QIcon(app_icon_path()))
    return app


def run(instance=None):
    _setup_logging()
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('VideoDownloader.App')
        except Exception:  # noqa: BLE001
            pass
    app = setup_app(sys.argv)

    def excepthook(etype, value, tb):
        msg = ''.join(traceback.format_exception(etype, value, tb))
        log.error('未处理的异常:\n%s', msg)
        sys.__stderr__ and sys.__stderr__.write(msg)
    sys.excepthook = excepthook

    app.setQuitOnLastWindowClosed(False)    # 关窗口只是缩小到托盘，由「退出」来真正退出
    win = MainWindow()
    win.show()
    if instance is not None:
        activation_timer = QTimer(win)
        activation_timer.timeout.connect(
            lambda: win._show_from_tray() if instance.activation_requested() else None)
        activation_timer.start(150)
    sys.exit(app.exec())
