import datetime
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import weakref
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler

import shiboken6
from PySide6.QtCore import QByteArray, QEvent, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal, QObject
from PySide6.QtGui import (
    QAction, QActionGroup, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QPainter,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
    QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from . import (
    APP_NAME, APP_VERSION, avatar, bilibili, core, core_update, greetings, icons, pan, proc_job, runtime_update,
    logsafe, task_store, theme, ui,
)
from .bili_dialog import BiliLoginDialog
from .config import DATA, ROOT, Settings, ffmpeg_dir
from .episode_dialog import EpisodeDialog
from .folder_dialog import FolderDialog, TextDialog
from .cloud_ui import CloudMixin, task_cloud_text
from .pan_ui import PanMixin
from .search_panel import SearchPanel
from .history import PAN, VIDEO, History, video_key
from .hotkey import GlobalHotkey
from .section_dialog import SectionDialog
from .settings_dialog import SettingsPanel
from .theme import c
from .titlebar import TITLE_H, Frameless
from .sniff_ui import SniffMixin
from .ui import (
    Card, Chip, Combo, Composer, HLine, IconButton, NavItem, PlatformTag, ProgressLine, SelectButton, Spark, sync_layout,
)
from .widgets import CoverDialog, CoverView, ElidedLabel, ThumbLoader, cover_url, save_cover_bytes

log = logging.getLogger('app')

GITHUB_URL = 'https://github.com/MoyerZhang-Claude/video-downloader'
RETRY_PATH = os.path.join(DATA, 'retry_urls.json')     # 更新内核后重启：要自动重新解析的链接

# 输入框下方的清晰度预设：(键, 名称, 取值, 说明)
PRESETS = [('pick', '逐个选择', None, '每个视频默认最高画质，可在卡片里单独改'),
           ('best', '全部最高画质', 'best', ''), ('1080', '全部 1080p', 1080, '没有时取更低一档'),
           ('720', '全部 720p', 720, ''), ('480', '全部 480p', 480, ''), ('360', '全部 360p', 360, ''),
           ('audio', '全部仅音频', 'audio', '只列出音频：无损、杜比全景声、Opus、AAC…，默认选音质最好的'),
           ('mp3', '全部转为 MP3', 'mp3', '只列出音频，默认选转为 MP3')]
AUDIO_PRESETS = ('audio', 'mp3')
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
    """带平滑滚动和细滚动条的滚动区（侧边栏、解析结果、历史、下载页、设置面板都用它）。"""
    sa = ui.SmoothScrollArea()
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
    core_update = Signal(str, str)       # 有新内核（还没下载）：(新版本, 当前版本)
    core_ready = Signal(str)             # 新内核已经下载好，重启后生效
    core_retry = Signal(str)             # 「更新内核后重试」下载完成（参数是错误信息，空 = 成功）
    parse_status = Signal(int, str)      # 解析前的准备工作（如下载 YouTube 组件）显示在卡片上
    ui_call = Signal(object)             # 后台线程干完活，把一个函数交回界面线程执行
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
        self.status_text = ''        # 解析前的准备工作（如「正在准备 YouTube 组件」）
        self.status_label = None
        self.outdated = False        # 报错像是解析内核过期了
        self.login = False           # 报错是「要登录才能看」一类：卡片上给「去登录」按钮
        self.extra = None            # 网页嗅探到的资源：解析时要带上的请求头、Cookie、标题（见 sniff_ui.py）
        self.hist = []               # 这个视频下载过的记录（后台查好的，「已下载」标记和「全部下载」用）
        self.pending_render = True   # 卡片内容还在排队等着画（一次加入很多链接时分批画）
        self.picked = None           # 用户在下拉框里手动选的清晰度（格式）：卡片重画后选回去
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
        self._seen = {}             # 上次显示的内容：没变的部分刷新时不碰
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

    def refresh(self, force=False):
        """按任务当前的状态更新显示。下载时每个任务每秒要刷新好几次，所以只改真正变了的部分：
        文字、图标、提示没变就不重设；状态颜色用样式类（st_ok、st_err…），只在状态变化时换一次，
        不在每次进度更新时调用 setStyleSheet（那会触发样式重新计算，十来个任务同时下载时界面明显发涩）。
        force：换主题后全部重设一遍。"""
        task = self.task
        s = task.status
        seen = self._seen

        def changed(key, value):
            if force or seen.get(key, seen) != value:
                seen[key] = value
                return True
            return False

        if self.name is not None and task.filepath and changed('path', task.filepath):
            self.name.set_full_text(os.path.basename(task.filepath), task.filepath)
        if self.compact and force:
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
        cloud_text, _ = task_cloud_text(self.win, task)
        if cloud_text:
            parts.append(cloud_text)
        detail = '  ·  '.join(parts)
        if changed('detail', (detail, task.error_raw)):
            self.detail.set_full_text(detail, task.error_raw or None)

        status = core.STATUS_TEXT.get(s, s)
        if s == core.RUNNING:
            status = f'{task.progress * 100:.0f}%' + (f' · {task.stage}' if task.stage else '')
        elif s == core.MERGING and task.stage:
            status = f'{task.stage}…'
        if s == core.FAILED and task.error:
            status = f'失败：{task.error}'
        if changed('status', status):
            self.status.setText(status)
        tip = task.error_raw or task.error or status
        if changed('tip', tip):
            self.status.setToolTip(tip)
        if changed('role', theme.STATUS_ROLE.get(s, 'text2')):
            restyle(self.status, 'st_' + seen['role'])
        if changed('wide', s == core.FAILED):
            self.status.setMaximumWidth(460 if s == core.FAILED else 16777215)

        done = s in (core.DONE, core.EXISTS)
        b1 = {core.QUEUED: ('pause', '暂停'), core.RUNNING: ('pause', '暂停'), core.PAUSED: ('play', '继续'),
              core.FAILED: ('refresh', '重试'), core.CANCELED: ('refresh', '重试'),
              core.DONE: ('play_circle', '打开文件'), core.EXISTS: ('play_circle', '打开文件')}.get(s, ('pause', '暂停'))
        if changed('b1', (b1, done)):
            self.b1.color_role = 'ok' if done else None
            self.b1.set_icon(*b1)
        if changed('b1_on', s != core.MERGING):
            self.b1.setEnabled(s != core.MERGING)
        finished = s in (core.DONE, core.EXISTS, core.CANCELED, core.FAILED)
        if changed('b2', finished):
            self.b2.set_icon('trash' if finished else 'x', '从列表移除' if finished else '取消')
        b2_on = s != core.MERGING or task.stage.startswith('弹幕版')      # 生成弹幕版时可以取消
        if changed('b2_on', b2_on):
            self.b2.setEnabled(b2_on)


class HistoryRow(QWidget):
    """历史记录里的一行：平台、标题、清晰度 · 大小 · 时间；悬停时出现操作按钮。"""

    def __init__(self, win, h):
        super().__init__()
        self.win, self.h = win, h
        self.setAttribute(Qt.WA_Hover)
        self.setFixedHeight(62)
        # 文件还在不在：历史页是后台线程查好放在 h['exists'] 里的（下载目录在机械盘 / 移动硬盘 / 网络位置时查起来慢）
        exists = h['exists'] if 'exists' in h else (bool(h['filepath']) and os.path.exists(h['filepath']))
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
        in_cloud = win.cloud_uploaded(h['filepath'])
        bits = [(h['quality'] or '').replace('] [', ' · '), core.fmt_size(h['size']) if h['size'] else '',
                fmt_ago(h['ts'])] + ([] if exists else ['文件已不在原位置' if not in_cloud else '本地已删除'])
        if in_cloud:
            bits.append('已传到云盘')
        col.addWidget(title)
        meta = ElidedLabel('  ·  '.join(b for b in bits if b), 'meta')
        col.addWidget(meta)
        win.cloud_watch_record(h['id'], meta, lambda t, base=[b for b in bits if b and b != '已传到云盘']: meta.set_full_text(
            '  ·  '.join(base + ([f'云盘 {t}' if t[0].isdigit() else f'云盘{"上传失败" if t == "失败" else "等待上传"}']
                                 if t else ['已传到云盘'] if win.cloud_uploaded(h['filepath']) else []))))
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

class MainWindow(CloudMixin, PanMixin, SniffMixin, QMainWindow):
    def __init__(self, instance=None):
        super().__init__()
        self.instance = instance         # 「只能开一个」的锁：重启软件时要先放开
        self._core_new = ''              # 查到的新内核版本
        self._core_pending = False       # 新内核已经下载好，等重启
        self._core_checked_on_failure = False
        self._core_updating = False
        self._restart_pending = False
        self._force_quit = False         # 退出时不再问「还有任务没下完」（安装新版本前已经问过了）
        self._app_rel = None             # 查到的软件新版本（app_update.parse_release 的结果）
        self.settings = Settings.load()
        ui.set_tips(self.settings.show_tips)
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
        self._hist_gen = 0               # 历史页 / 侧边栏的数据在后台查：只用最后一次查询的结果
        self._side_gen = 0
        self._io = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ui-io')    # 查数据库、看文件在不在
        self._stale_views = set()        # 页面不可见时跳过刷新的任务：切回来时补一次
        self._render_queue = []          # 等着画内容的解析卡片（一次加入很多链接时分批画）
        self._greet_checked = 0.0
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
        self.bridge.core_ready.connect(self._on_core_ready)
        self.bridge.core_retry.connect(self._on_core_retry)
        self.bridge.parse_status.connect(self._on_parse_status)
        self.bridge.ui_call.connect(lambda fn: fn())
        runtime_update.log = self.bridge.log.emit
        self.bridge.app_update.connect(self._on_app_update)

        self.setWindowTitle(APP_NAME)          # 只显示在任务栏；窗口顶部没有系统标题栏（见 titlebar.py）
        self.setMinimumSize(980, 640)
        self._pan_init()
        self._cloud_init()
        self._sniff_init()
        self._parse_timer = QTimer(self, singleShot=True, interval=400)     # 一次解析很多链接时合并刷新
        self._parse_timer.timeout.connect(self._after_history_change)
        self._side_timer = QTimer(self, singleShot=True)                    # 侧边栏刷新：短时间内的多次合并成一次
        self._side_timer.timeout.connect(self._load_sidebar)
        self._render_timer = QTimer(self, singleShot=True)
        self._render_timer.timeout.connect(self._render_batch)
        self._res_label_timer = QTimer(self, singleShot=True)
        self._res_label_timer.timeout.connect(self._do_update_res_label)
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
        self._tasks_save_timer.timeout.connect(lambda: self._save_tasks(wait=False))
        QTimer.singleShot(0, self._restore_tasks)
        QTimer.singleShot(700, self._resume_retry)
        QTimer.singleShot(1500, self._secure_cookies)
        if not ffmpeg_dir():
            QTimer.singleShot(300, lambda: ui.alert(
                self, '没有找到 ffmpeg', '程序目录下的 ffmpeg 文件夹不见了：YouTube、B站 等需要合并音视频的高清格式将无法下载。\n'
                                       '重新安装软件可以恢复。'))

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        central = clear_widget('central')
        self.central = central
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        # 顶部留一点空：右上角是最小化 / 最大化 / 关闭按钮（titlebar.py），页面标题栏右边的按钮不要和它们挤在一起
        right.addSpacing(TITLE_H - 22)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        root.addLayout(right, 1)
        self.pages = {'parse': self._build_parse_page(), 'tasks': self._build_tasks_page(),
                      'history': self._build_history_page(), 'log': self._build_log_page()}
        for w in self.pages.values():
            self.stack.addWidget(w)
        self.setCentralWidget(central)
        self.toast = ui.Toast(central)
        self.notice = ui.NoticeBar(central)
        self.frameless = Frameless(self)          # 去掉系统标题栏，右上角自己画窗口按钮
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
        sl.setContentsMargins(0, 0, 6, 0)      # 右边留一点：滚动条是浮在内容上的细条，别压住条目右边的数字
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
        from .update_ui import UpdateHint
        self.update_hint = UpdateHint()          # 「新版本 x.y.z · 查看」：查到新版本才出现
        self.update_hint.clicked.connect(lambda: self.show_update())
        self.update_hint.hide()
        v.addWidget(self.update_hint)
        v.addSpacing(4)
        self.account = AccountButton()
        self.account.clicked.connect(self._account_menu)
        v.addWidget(self.account)
        self._update_account()
        return sb

    def _bg(self, work, done=None, what=''):
        """work() 放到后台线程里跑（查数据库、看文件还在不在、写历史——这些碰到慢盘会卡住界面），
        跑完把结果交回界面线程：done(结果)。后台只有一条线程，先来的先做。"""
        def run():
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                log.warning('%s失败: %s', what or '后台任务', e)
                return
            if done is not None:
                self.bridge.ui_call.emit(lambda: done(res))
        self._io.submit(run)

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
        self.update_hint.set_collapsed(on)
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
        self._flush_stale()
        self._place_toast()

    def _new_parse(self):
        """像 Claude 的「新建」：回到首页（问候语 + 居中输入框）。
        之前的解析结果先留着（首页有「回到刚才的解析结果」），在首页解析新链接时才清掉；下载不受影响。"""
        if (self.rows or self.pan_cards or self.sniff_card is not None) and not self.fresh:
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
        self.cloud_watch_record(h['id'], n, n.set_badge)       # 上传到谷歌云盘时显示进度
        return n

    def _refresh_sidebar(self, delay=0):
        """刷新侧边栏的「分区」和「最近下载」（像 Claude 侧边栏的项目和最近对话）。
        delay 毫秒后才真正去查，期间再来的刷新合并成一次：一批任务同时完成时只刷新一次，不会闪。"""
        left = self._side_timer.remainingTime() if self._side_timer.isActive() else None
        if left is None or delay < left:
            self._side_timer.start(delay)

    def _load_sidebar(self):
        """数据库在后台线程里查，查完交给 _apply_sidebar 增量更新。"""
        if not self.history:
            return
        self._side_gen += 1
        gen = self._side_gen
        opened = {kind: head.open for kind, head in self.sec_headers.items()}
        expanded = set(self.expanded_sections)
        recents_open = self.recent_header.open
        h = self.history

        def work():
            secs = h.sections()
            trees = {}
            for sec in secs:
                if sec['id'] in expanded and opened.get(sec['kind']):
                    trees[sec['id']] = (h.recent('', limit=5, section=sec['id']) if sec['kind'] == VIDEO
                                        else h.parses('', limit=8, section=sec['id']))
            recents = h.recent('', limit=16, kind=VIDEO) if recents_open else []
            more = recents_open and h.count(kind=VIDEO) > len(recents)
            return secs, trees, recents, more

        def done(res):
            if gen == self._side_gen:          # 后面又触发了新的刷新：这次的结果已经过时
                self._apply_sidebar(*res, opened, recents_open)
        self._bg(work, done, '读取侧边栏')

    def _apply_sidebar(self, secs, trees, recents, more, opened, recents_open):
        """只增删改变化的条目（见 sync_layout），滚动位置保持不动。"""
        self.sections = {s['id']: s for s in secs}
        vbar = self.recent_scroll.verticalScrollBar()
        pos = vbar.value()
        for kind, box in self.sec_boxes.items():
            desired = []
            for sec in secs:
                if sec['kind'] != kind or not opened.get(kind):
                    continue
                desired.append((('sec', sec['id']), (sec['name'], sec['icon'], sec['count'], sec['builtin']),
                                lambda sec=sec: self._section_item(sec), None))
                if sec['id'] in trees:
                    kids = self._tree_items(sec, trees[sec['id']])
                    desired.append((('tree', sec['id']), tuple((k, sig) for k, sig, _make, _upd in kids),
                                    lambda kids=kids: self._tree_box(kids),
                                    lambda w, kids=kids: sync_layout(w.layout(), kids)))
            sync_layout(box, desired)
        desired = [(('rec', h['id']), self._rec_sig(h), lambda h=h: self._record_row(h), None) for h in recents]
        if recents_open and not recents:
            if ui.tips_on():
                desired.append((('empty',), 0, self._recent_empty, None))
        elif more:
            desired.append((('more',), 0, self._recent_more, None))
        sync_layout(self.recent_box, desired)
        if vbar.value() != pos:
            QTimer.singleShot(0, lambda: vbar.setValue(min(pos, vbar.maximum())))

    @staticmethod
    def _rec_sig(h):
        return (h['title'], h['quality'], h['filepath'], h.get('section_id'))

    def _section_item(self, s):
        unit = '个下载好的视频' if s['kind'] == VIDEO else '个网盘分享（点开后点一下就重新解析）'
        n = NavItem(s['name'], s['icon'], height=34, font_px=14)
        n.setCheckable(False)
        n.set_trailing(str(s['count']) if s['count'] else '')
        n.setToolTip(f'{s["name"]}：{s["count"]} {unit}\n点击展开 / 收起，右键可以' +
                     ('改名' if s['builtin'] else '编辑或删除'))
        n.clicked.connect(lambda _=False, sid=s['id']: self._toggle_section(sid))
        n.setContextMenuPolicy(Qt.CustomContextMenu)
        n.customContextMenuRequested.connect(lambda pos, sec=s, w=n: self._section_menu(sec, w.mapToGlobal(pos)))
        return n

    def _tree_items(self, s, recs):
        """展开的分区里的条目（最近几条 + 「查看全部」），格式同 sync_layout 的 desired。"""
        if s['kind'] == VIDEO:
            kids = [(('rec', h['id']), self._rec_sig(h), lambda h=h: self._record_row(h, 30), None) for h in recs]
        else:
            kids = [(('parse', r['id']), (r['title'], r['url']), lambda r=r: self._parse_side_row(r, 30), None)
                    for r in recs]
        if s['count'] or ui.tips_on():      # 空分区里的占位文字是说明：关掉「显示功能说明」后不显示
            kids.append((('more',), (s['count'],), lambda s=s: self._section_more(s), None))
        return kids

    def _tree_box(self, kids):
        tree = ui.TreeBox()
        tv = QVBoxLayout(tree)
        tv.setContentsMargins(30, 0, 0, 4)
        tv.setSpacing(0)
        sync_layout(tv, kids)
        return tree

    def _section_more(self, s):
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
        return more

    def _recent_empty(self):
        empty = label('下载好的视频会出现在这里', 'meta')
        empty.setContentsMargins(10, 4, 0, 0)
        return empty

    def _recent_more(self):
        more = NavItem('查看全部下载好的视频', height=30, font_px=13)
        more.setCheckable(False)
        more.muted = True
        more.clicked.connect(self._show_downloads)
        return more

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
        self.cloud_menu(m, [h['filepath']], lambda: self.cloud_upload_record(h))
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
        self.sections[sid] = {'id': sid, 'name': dlg.name, 'icon': dlg.icon, 'platform': None, 'builtin': 0,
                              'count': 0, 'kind': kind}      # 侧边栏是后台刷新的：先记上，马上就能用
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
        if not ui.confirm(self, f'删除分区「{s["name"]}」？', '里面的记录和文件不会被删除，会回到所属平台的分区。',
                          ok='删除分区', danger=True):
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
        self.chip_hdr = Chip('HDR', 'sun', checkable=True,
                             tip='打开：清晰度列表里多出 HDR / 杜比视界版本（任何电脑都能下载，在 HDR 屏幕上看效果最好）；关闭：只列普通版本')
        self.chip_hdr.setChecked(self.settings.show_hdr)
        self.chip_hdr.toggled.connect(lambda on: (self._toggle_setting('show_hdr', on), self.setting_changed('show_hdr')))
        self.composer.left.addWidget(self.chip_hdr)
        cloud_chip = self._cloud_chip(Chip)
        if cloud_chip is not None:
            self.composer.left.addWidget(cloud_chip)
            self._cloud_sync_chip()
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
        n = len(self.rows) + len(self.pan_cards) + (1 if self.sniff_card is not None else 0)
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
        self.notice.bottom = self.toast.bottom + 50
        self.notice.place()

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
        audio_changed = (key in AUDIO_PRESETS) != (self.settings.preset in AUDIO_PRESETS)
        self.settings.preset = key
        self._save_settings()
        self._update_preset_btn()
        if audio_changed:           # 下拉框里要不要列视频变了：重画解析结果
            for row in self.rows.values():
                if row.state == 'ok':
                    self._render_result(row)
        else:
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
        m.addAction(icons.icon('globe', c('text2')), '打开网页嗅探视频…', lambda: self.sniff_prompt())
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
        ui.mark_tip(s)          # 引导新手的副标题：关掉「显示功能说明」后不显示
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
            ui.mark_tip(label('解析、下载的详细过程和报错。反馈问题时可以把这里的内容发过去。', 'meta')))
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

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange and not self.isMinimized():
            QTimer.singleShot(0, self._on_shown_again)

    def _on_shown_again(self):
        """从托盘 / 最小化回来：补上看不见时跳过的刷新，状态栏的定时器恢复正常频率。"""
        self._flush_stale()
        self._refresh_status()

    def showEvent(self, e):
        super().showEvent(e)
        if getattr(self, '_shown_once', False):
            QTimer.singleShot(0, self._on_shown_again)
        if not getattr(self, '_shown_once', False):
            self._shown_once = True
            ui.style_window(self, 'bg')
            self.frameless.apply()
            self.composer.edit.setFocus()
            # 窗口出来后再在后台加载解析内核，第一次解析时就已经准备好了
            QTimer.singleShot(30, lambda: threading.Thread(target=core.preload, daemon=True).start())

    def nativeEvent(self, event_type, message):
        r = self.frameless.native_event(event_type, message) if getattr(self, 'frameless', None) else None
        if r is not None:
            return True, r
        return super().nativeEvent(event_type, message)

    def apply_theme(self, mode):
        self.settings.theme = mode
        self._save_settings()
        theme.apply(QApplication.instance(), mode)

    def apply_oled(self, on):
        self._save_settings()
        theme.apply(QApplication.instance(), oled=on)

    def _on_theme_changed(self):
        ui.style_window(self, 'bg')
        _refresh_btn_icons()
        self._search_action.setIcon(icons.icon('search', c('text3'), 18))
        self.clip_icon.setPixmap(icons.pixmap('clipboard', c('text3'), 16))
        for tv in list(self.task_views.values()) + list(self.task_lines.values()):
            if shiboken6.isValid(tv):
                tv.refresh(force=True)
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
        row.card.setMinimumHeight(128)          # 内容还没画出来：先按大致的高度占好位置
        self._render_queue.append(token)
        if not self._render_timer.isActive():
            self._render_timer.start(0)
        self._update_res_label()
        return token

    def _render_batch(self):
        """一次加入很多链接（从合集里选几百个视频）时，卡片内容分批画：每轮最多 20 张，界面不会卡住一大下。"""
        n = 0
        while self._render_queue and n < 20:
            row = self.rows.get(self._render_queue.pop(0))
            if row is None or not row.pending_render:
                continue
            row.pending_render = False
            if shiboken6.isValid(row.card):
                row.card.setMinimumHeight(0)
                self._render_result(row)
            n += 1
        if self._render_queue:
            self._render_timer.start(0)

    def _start_parse(self, token):
        row = self.rows[token]
        row.state, row.error, row.status_text, row.outdated, row.login = 'parsing', '', '', False, False
        self._render_result(row)
        self._update_res_label()
        self.parser.submit(
            row.url,
            lambda url, video, t=token: self.bridge.parsed.emit(t, video),
            lambda url, msg, raw, t=token: self.bridge.parse_failed.emit(t, msg, raw),
            lambda url, col, t=token: self.bridge.expanded.emit(t, col),
            probe=not row.from_collection,
            on_status=lambda url, text, t=token: self.bridge.parse_status.emit(t, text),
            extra=row.extra,
        )

    def _on_parse_status(self, token, text):
        row = self.rows.get(token)
        if row is None or row.state != 'parsing':
            return
        row.status_text = text
        if row.status_label is not None and shiboken6.isValid(row.status_label):
            row.status_label.setText(text or '正在解析…')

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
        row.outdated = core.looks_outdated(raw)
        row.login = core.needs_login(raw) or msg.startswith(core.LOGIN_EXPIRED)
        self._render_result(row)
        self._update_res_label()
        self._append_log('error', f'解析失败 {row.url}\n    {raw}')
        if row.outdated and not self._core_new and not self._core_checked_on_failure:
            # 报错像是解析内核过期了：马上查一次有没有新内核（每次启动最多一次）
            self._core_checked_on_failure = True
            self._check_core_update()

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
        if row.pending_render:          # 还在排队：轮到它时按那时候的状态画
            return
        token = row.token
        body = clear_widget()
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)
        cover = CoverView(176, 99, radius=10)
        cover.clicked.connect(lambda: self._preview_cover(token))
        row.cover, row.combo, row.badge, row.dl_btn, row.spark = cover, None, None, None, None
        row.status_label = None
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
            elif row.cover_url:             # 缩略图缓存有上限，被挤掉的重新要一次（原图在磁盘上，不用再下载）
                self.thumbs.request(row.cover_url, v.info.get('webpage_url') or row.url)
            title = ElidedLabel(v.title, 'cardTitle')
            title.set_full_text(v.title, f'{v.title}\n{row.url}')
            col.addWidget(title)
            line = QHBoxLayout()
            line.setSpacing(6)
            line.addWidget(PlatformTag(v.platform))
            badge = ui.IconTag('check', '', 'ok', 'tint_ok')       # 对勾是画出来的图标，不是文字符号
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
            if v.platform == '网页' and row.extra is None and os.name == 'nt':
                sb = Chip('用浏览器打开并嗅探', 'globe', height=24, font_px=12,
                          tip='这个网站没有专门的解析器，这里是从网页里直接找到的视频，可能不全。\n'
                              '可以在内置浏览器里打开网页并播放，让软件把播放时请求的视频地址抓出来')
                sb.clicked.connect(lambda: self.sniff_start(row.url))
                line.addWidget(sb)
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
            shown = v.options
            if self.settings.preset in AUDIO_PRESETS:      # 「全部仅音频」：列表里只放音频
                shown = [o for o in v.options if o.kind == 'audio'] or v.options
            for opt in shown:
                combo.addItem(opt.label, opt)
            if v.locked:
                need = '需要大会员账号' if bilibili.is_logged_in() else '左下角头像菜单里扫码登录 B站'
                combo.addItem(icons.icon('lock', c('text3'), 16), f'{v.locked}（{need}）')
                combo.model().item(combo.count() - 1).setEnabled(False)
                combo.setToolTip(f'未解锁的清晰度：{v.locked}')
            combo.setMinimumWidth(200)
            combo.setMaximumWidth(380)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row.combo = combo
            self._apply_preset(row)
            if row.picked:          # 改了编码偏好 / HDR / 字幕语言后卡片重画：手动选好的清晰度还在列表里就选回去
                keep = next((i for i in range(combo.count())
                             if combo.itemData(i) is not None and combo.itemData(i).format == row.picked), -1)
                if keep >= 0:
                    combo.setCurrentIndex(keep)
                else:
                    row.picked = None
            combo.activated.connect(lambda i, r=row, cb=combo: setattr(
                r, 'picked', cb.itemData(i).format if cb.itemData(i) is not None else None))
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
                row.status_label = label(row.status_text or '正在解析…', 'meta')
                status.addWidget(row.status_label)
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
                if row.error.startswith('暂不支持') and os.name == 'nt':
                    act.addWidget(button('用浏览器打开并嗅探', lambda: self.sniff_start(row.url), 'primary',
                                         '在内置浏览器里打开这个网页并播放视频，软件把视频地址抓出来', 'globe'))
                if row.login:
                    bili = 'bilibili.com' in row.url or 'b23.tv' in row.url
                    again = row.error.startswith(core.LOGIN_EXPIRED)
                    act.addWidget(button('扫码登录 B站' if bili else '重新登录' if again else '去登录',
                                         lambda: self._login_for_row(token), 'primary',
                                         'B站用扫码登录（左下角头像菜单里也有）' if bili else
                                         '在内置浏览器里登录这个网站，登录好以后自动重新解析', 'user'))
                if row.outdated and self._core_new:
                    act.addWidget(button('更新内核后重试', self._update_core_and_retry, 'primary',
                                         f'解析内核有新版本 {self._core_new}：更新后自动重启软件，并重新解析这个链接', 'refresh'))
            col.addLayout(act)
        row.card.set_body(body)
        self._update_badge(row)
        # 卡片里的下载进度
        for tid, t in self.task_row.items():
            if t == token and tid not in self.task_lines:
                task = next((x for x in self.manager.tasks if x.id == tid), None)
                if task:
                    self._add_task_line(task, row)

    def _login_for_row(self, token):
        """失败卡片上的「去登录」：在内置浏览器里登录这个链接所在的网站，登录好以后重新解析。"""
        row = self.rows.get(token)
        if row is None:
            return
        if 'bilibili.com' in row.url or 'b23.tv' in row.url:
            self._bili_login(True)          # B站有自己的扫码登录（成功后会自动重新解析 B站的卡片）
            return
        from . import login_ui, site_login
        site = site_login.guess(row.url)
        if site is None:
            self._open_settings('network')
            return
        if login_ui.login(self, site):
            self._after_site_login(site)

    def _after_site_login(self, site):
        """登录了一个网站：之前因为要登录而失败的、这个网站的卡片自动重新解析。"""
        from . import site_login
        n = 0
        for token, row in list(self.rows.items()):
            if row.state == 'error' and row.login:
                s = site_login.for_url(row.url)
                if s is not None and s.key == site.key:
                    self._start_parse(token)
                    n += 1
        if self.settings_panel is not None:
            self.settings_panel.refresh_sites()
        if n:
            self.notify(f'已登录 {site.name}，正在重新解析 {n} 个链接', 'ok')

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
        self._render_queue.clear()
        self._pan_clear_cards()
        self.sniff_remove(update=False)
        # 不再显示的封面缩略图马上释放（下载页里的任务还在用的留着）
        self.thumbs.release(tv.cover_url for tv in self.task_views.values() if shiboken6.isValid(tv))
        self._update_res_label()

    def _update_res_label(self):
        """更新「x / y 个视频」和首页 / 结果页的切换。一批解析结果一个个回来时合并成一次。"""
        if not self._res_label_timer.isActive():
            self._res_label_timer.start(0)

    def _do_update_res_label(self):
        total = len(self.rows)
        ok = sum(1 for r in self.rows.values() if r.state == 'ok')
        busy = sum(1 for r in self.rows.values() if r.state == 'parsing')
        busy += sum(1 for card in self.pan_cards.values() if shiboken6.isValid(card) and card.state == 'opening')
        bits = [f'{ok} / {total} 个视频' if total else '', f'{len(self.pan_cards)} 个网盘分享' if self.pan_cards else '']
        if self.sniff_card is not None:
            bits.append('网页嗅探')
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
        data = self.thumbs.raw_data(row.cover_url)
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
        if self.thumbs.has_raw(url):
            self._write_cover(url, row.video.title, self.settings.download_dir)
        elif url in self.thumbs.failed:
            self.notify('封面加载失败，无法保存', 'error')
        else:
            self._pending_cover_saves.setdefault(url, []).append((row.video.title, self.settings.download_dir))
            self.thumbs.request(url, row.video.info.get('webpage_url') or row.url)
            self.notify('正在获取封面…')

    def _write_cover(self, url, title, out_dir):
        data = self.thumbs.raw_data(url)
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
        elif preset == 'audio':
            # 音质最好的：无损 > 合并视频用的那条主音轨（音质最好的普通音轨）> 第一条音频
            idx = next((i for i, o in enumerate(opts) if o.kind == 'audio' and o.convert == 'flac'), None)
            if idx is None:
                idx = next((i for i, o in enumerate(opts) if o.kind == 'audio' and o.tag == '音频'), None)
            if idx is None:
                idx = next((i for i, o in enumerate(opts) if o.kind == 'audio'), 0)
        elif isinstance(preset, int):
            videos = [(i, o) for i, o in enumerate(opts) if o.kind == 'video']
            fit = [(i, o) for i, o in videos if o.res and o.res <= preset]
            if fit:
                idx = fit[0][0]
            elif videos:
                idx = videos[-1][0]
        combo.setCurrentIndex(idx)

    def _apply_preset_all(self):
        """换了「清晰度」预设：所有卡片都按新的预设选（之前手动选的不再保留）。"""
        for row in self.rows.values():
            row.picked = None
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
        # 同一个视频：用「网站 + id」认（不同网站的 id 可能一样）；只下载其中一段的任务不算（整段还是可以下）
        key = video_key(row.video.info)
        for t in self.manager.tasks:
            if video_key(t.video.info) == key and t.option.format == option.format and not t.section and t.status in (
                    core.QUEUED, core.RUNNING, core.MERGING, core.PAUSED):
                if not quiet:
                    self.notify('这个视频的该清晰度已经在下载列表里了')
                return False
        overwrite = False
        # 「全部下载」时用后台查好的记录（几百张卡片逐个查数据库、看文件在不在会卡住界面）；单独点「下载」时现查
        hist = row.hist if quiet else self._history_of(row.video)
        done_before = [h for h in hist if h['quality'] == option.tag and h['exists']]
        if done_before:
            if quiet:
                return False
            when = datetime.datetime.fromtimestamp(done_before[0]['ts']).strftime('%m-%d %H:%M')
            choice = ui.ask(self, f'「{option.tag}」已经下载过了',
                            f'{when} 下载的：{os.path.basename(done_before[0]["filepath"])}',
                            (('取消', None, ''), ('重新下载', 'again', ''), ('打开已有文件', 'open', 'primary')), width=520)
            if choice == 'open':
                os.startfile(done_before[0]['filepath'])
                return False
            if choice != 'again':
                return False
            overwrite = True
        task = core.DownloadTask(row.video, option, self.settings.download_dir, save_cover=self.settings.save_cover)
        task.cloud = self.cloud_wanted()
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
        task.cloud = self.cloud_wanted()
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
                    h['quality'] == row.combo.currentData().tag and h['exists'] for h in row.hist):
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
        # 看不见的不刷新（下载页的卡片只在「下载」页可见，卡片里的进度条只在解析结果页可见）：切回来时补一次
        shown = self.isVisible() and not self.isMinimized()
        for views, page in ((self.task_views, 'tasks'), (self.task_lines, 'parse')):
            v = views.get(task.id)
            if v is not None:
                if not shiboken6.isValid(v):
                    views.pop(task.id, None)
                elif shown and self.page == page:
                    v.refresh()
                else:
                    self._stale_views.add((page, task.id))

    def _flush_stale(self):
        """把刚才因为看不见而跳过的刷新补上（切到这一页、窗口重新显示时调用）。"""
        if not self._stale_views or not self.isVisible() or self.isMinimized():
            return
        views = {'tasks': self.task_views, 'parse': self.task_lines}.get(self.page)
        if views is None:
            return
        for key in [k for k in self._stale_views if k[0] == self.page]:
            self._stale_views.discard(key)
            v = views.get(key[1])
            if v is not None and shiboken6.isValid(v):
                v.refresh()

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

    def _save_tasks(self, wait=True):
        """wait=False：只在界面线程里收集内容，写文件放到后台（任务状态变化时）；退出时用 wait=True 等它写完。"""
        try:
            (task_store.save if wait else task_store.save_async)(self.manager.tasks)
        except Exception as e:  # noqa: BLE001
            log.warning('保存未完成任务失败: %s', e)

    def _restore_tasks(self):
        tasks = task_store.load()
        for t in tasks:
            self.manager.restore(t)
        if tasks:
            self._save_tasks(wait=False)       # 旧版本留下的 tasks.json 里可能有明文 Cookie：马上按新格式重写
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
        """「已下载」标记：下载记录和文件还在不在放到后台线程去查（下载目录在慢盘上时不卡界面），查完再显示。"""
        if row.video is None or not self.history:
            return
        video, token = row.video, row.token
        self._bg(lambda: self._history_of(video), lambda recs: self._show_badge(token, recs), '查询下载记录')

    def _show_badge(self, token, recs):
        row = self.rows.get(token)
        if row is None:
            return
        row.hist = recs
        badge = row.badge
        if badge is None or not shiboken6.isValid(badge):
            return
        recs = [h for h in recs if h['exists']]
        if not recs:
            badge.hide()
            return
        qualities = '、'.join(dict.fromkeys(h['quality'].replace('] [', ' · ') for h in recs))
        badge.setText(f'已下载 {qualities}')
        badge.setToolTip('\n'.join(
            f'{datetime.datetime.fromtimestamp(h["ts"]).strftime("%Y-%m-%d %H:%M")}  {h["filepath"]}' for h in recs))
        badge.show()

    def _on_status_changed(self, task, prev):
        self._tasks_save_timer.start()
        s = task.status
        if s in (core.DONE, core.EXISTS) and prev not in (core.DONE, core.EXISTS):
            if self.history and task.filepath:
                # 写历史（要读文件大小、写数据库）放到后台，写完再更新「已下载」标记、侧边栏和历史页
                self._bg(lambda: self.history.add(task), lambda _r: self._download_recorded(task), '写入下载历史')
            if getattr(task, 'cloud', False) and self.cloud_ready:
                self.cloud_upload_task(task)
            if s == core.DONE:
                self._batch_done += 1
                if self.settings.notify_done and not self.isActiveWindow():
                    self._tray_message('下载完成', task.video.title)
        elif s == core.FAILED and prev != core.FAILED:
            if self.settings.notify_done and not self.isActiveWindow():
                self._tray_message('下载失败', f'{task.video.title}\n{task.error}', QSystemTrayIcon.Warning)
        if s in (core.DONE, core.EXISTS, core.FAILED, core.CANCELED, core.PAUSED):
            self._check_all_done()

    def _download_recorded(self, task):
        key = video_key(task.video.info)
        for r in self.rows.values():
            if r.video and video_key(r.video.info) == key:
                self._update_badge(r)
        self._refresh_sidebar(300)          # 一批任务同时完成：合并成一次刷新
        if self.page == 'history':
            self._refresh_history()

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
        """历史记录页：解析记录（全部 / 视频 / 网盘 / 某个网盘分区），或者下载好的视频（全部 / 某个视频分区）。
        数据库和「文件还在不在」在后台线程里查，查完再画出来。"""
        if reset:
            self._hist_limit = 150
        if not self.history:
            self.hist_count.setText('历史记录不可用')
            return
        self._hist_gen += 1
        gen = self._hist_gen
        search = self.hist_search.text().strip()
        section, mode, kind_sel, limit = self.hist_section, self.hist_mode, self.hist_kind, self._hist_limit
        h = self.history

        def work():
            secs = {s['id']: s for s in h.sections()}
            sec_id = section if section in secs else None
            sec = secs.get(sec_id)
            if mode == 'downloads':
                recs = h.recent(search, limit=limit + 1, section=sec_id, kind=VIDEO)
                total = h.count(sec_id, VIDEO)
                for r in recs[:limit]:
                    r['exists'] = bool(r['filepath']) and os.path.exists(r['filepath'])
            else:
                kind = None if sec else kind_sel
                recs = h.parses(search, limit=limit + 1, kind=kind, section=sec_id)
                total = h.parse_count(kind, sec_id)
            counts = {None: h.parse_count(), VIDEO: h.parse_count(kind=VIDEO), PAN: h.parse_count(kind=PAN),
                      'downloads': h.count(kind=VIDEO)}
            return {'secs': secs, 'recs': recs, 'total': total, 'counts': counts, 'search': search, 'limit': limit}

        def done(m):
            if gen == self._hist_gen:          # 又换了筛选 / 搜索词：这次的结果已经过时
                self._apply_history(m)
        self._bg(work, done, '读取历史记录')

    def _apply_history(self, m):
        lay = self.hist_list
        while lay.count() > 1:
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.sections = m['secs']
        if self.hist_section not in self.sections:
            self.hist_section = None
        self._build_hist_filters(m['counts'])
        search, limit = m['search'], m['limit']
        sec = self.sections.get(self.hist_section) if self.hist_section else None
        downloads = self.hist_mode == 'downloads'
        recs, total = m['recs'], m['total']
        if downloads:
            head = f'「{sec["name"]}」分区 {total} 个下载好的视频' if sec else f'下载好的视频 {total} 个'
        else:
            kind = None if sec else self.hist_kind
            what = {VIDEO: '视频', PAN: '网盘分享'}.get(kind, '')
            head = (f'「{sec["name"]}」分区 {total} 个网盘分享，点一下重新解析' if sec else
                    f'共 {total} 条{what}解析记录，点一下重新解析')
        more = len(recs) > limit
        recs = recs[:limit]
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

    def _build_hist_filters(self, counts):
        """历史记录页顶部的筛选：全部 / 视频 / 网盘 是解析记录，「已下载的视频」是下载好的视频；
        选了网盘或已下载的视频后，再列出对应的分区。counts：各类记录的条数（后台查好的）。"""
        self._clear_layout(self.hist_filters)
        sec = self.sections.get(self.hist_section)
        downloads = self.hist_mode == 'downloads'
        kind = None if downloads else (sec['kind'] if sec else self.hist_kind)
        chips = [(f'全部 {counts[None]}', 'list', not downloads and kind is None, lambda: self._filter_kind(None)),
                 (f'视频 {counts[VIDEO]}', 'film', not downloads and kind == VIDEO, lambda: self._filter_kind(VIDEO)),
                 (f'网盘 {counts[PAN]}', 'folder', not downloads and kind == PAN and sec is None,
                  lambda: self._filter_kind(PAN)),
                 (f'已下载的视频 {counts["downloads"]}', 'download', downloads and sec is None,
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
            if not ui.confirm(self, '清空全部视频下载记录？', '只删除记录，不会删除已下载的文件。'
                              '侧边栏「最近下载」和视频分区里的记录也会清空。', ok='清空', danger=True):
                return
            self.history.clear()
        else:
            kind = self.hist_kind
            what = {VIDEO: '视频的', PAN: '网盘分享的'}.get(kind, '全部')
            extra = '（网盘分区里保存的分享链接也会清空）' if kind != VIDEO else ''
            if not ui.confirm(self, f'清空{what}解析记录？', extra.strip('（）'), ok='清空', danger=True):
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

    # ------------------------------------------------------------ 解析内核更新
    def _check_core_update(self):
        """后台看解析内核（yt-dlp）有没有新版本。打包版默认直接下载好（重启后生效），源码版只提示。
        启动后查一次；解析失败、报错像是内核过期时再马上查一次。顺便每周检查一次 YouTube 组件（Deno）。"""
        s = self.settings
        auto = s.auto_core_update and getattr(sys, 'frozen', False)
        channel, proxy = s.core_channel, (s.proxy or '').strip()

        def work():
            try:
                cur = core_update.current_version()
                if auto:
                    _old, new, updated = core_update.update_frozen(channel=channel)
                    if updated:
                        self.bridge.log.emit('info', f'解析内核新版本 {new} 已下载（当前 {cur}），重启软件后生效')
                        self.bridge.core_ready.emit(new)
                else:
                    new, _ = core_update.latest_version(channel=channel)
                    if cur != '未知' and core_update._norm(new) > core_update._norm(cur):
                        self.bridge.log.emit('info', f'解析内核有新版本 {new}（当前 {cur}）')
                        self.bridge.core_update.emit(new, cur)
            except core_update.NeedsNewApp as e:
                self.bridge.log.emit('warning', str(e))
            except Exception as e:  # noqa: BLE001 - 检查失败不打扰，只记日志
                self.bridge.log.emit('info', f'检查解析内核更新失败：{e}')
            try:
                ver = runtime_update.check_update(proxy)
                if ver:
                    self.bridge.log.emit('info', f'YouTube 组件已更新到 Deno {ver}')
            except Exception as e:  # noqa: BLE001
                self.bridge.log.emit('info', f'检查 YouTube 组件更新失败：{e}')
        threading.Thread(target=work, daemon=True).start()

    def _on_core_update(self, new, cur):
        """有新内核，还没下载（源码版 / 关掉了自动更新）。"""
        self._core_new = new
        if not self._rerender_outdated():
            self.notify(f'解析内核有新版本 {new}（当前 {cur}），可在 设置 → 解析内核 里一键更新')

    def _on_core_ready(self, new):
        """新内核已经在后台下载好。"""
        self._core_new, self._core_pending = new, True
        retry = bool(self._rerender_outdated())
        self.notice.show_notice(f'解析内核已更新到 {new}，重启后生效', '立即重启',
                                lambda: self._update_core_and_retry() if retry else self._restart())
        self._place_toast()
        if self.settings_panel is not None:
            self.settings_panel.core_downloaded(new)

    def _rerender_outdated(self):
        """报错像是内核过期的失败卡片：重画一下，换上「更新内核后重试」按钮。返回有几张。"""
        rows = [r for r in self.rows.values() if r.state == 'error' and r.outdated]
        for r in rows:
            self._render_result(r)
        return len(rows)

    def _update_core_and_retry(self):
        """失败卡片上的「更新内核后重试」：下载新内核 → 记下要重试的链接 → 重启 → 自动重新解析。"""
        if self._core_pending:
            self._restart(retry=True)
            return
        if self._core_updating:
            return
        self._core_updating = True
        self.notify('正在下载新的解析内核，完成后会自动重启并重新解析…')
        channel = self.settings.core_channel

        def work():
            try:
                if getattr(sys, 'frozen', False):
                    core_update.update_frozen(channel=channel)
                else:
                    cmd = [sys.executable.replace('pythonw.exe', 'python.exe'), '-m', 'pip', 'install', '-U',
                           '--disable-pip-version-check', *(['--pre'] if channel == 'nightly' else []),
                           'yt-dlp[default,curl-cffi]']
                    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace', timeout=600,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if r.returncode != 0:
                        raise RuntimeError((r.stdout + r.stderr)[-400:])
                self.bridge.core_retry.emit('')
            except Exception as e:  # noqa: BLE001
                self.bridge.core_retry.emit(str(e) or type(e).__name__)
        threading.Thread(target=work, daemon=True).start()

    def _on_core_retry(self, error):
        self._core_updating = False
        if error:
            self._append_log('warning', f'更新解析内核失败：{error}')
            self.notify(f'更新解析内核失败：{error[:120]}', 'error')
            return
        self._core_pending = True
        self._restart(retry=True)

    def _restart(self, retry=False):
        """重启软件（新内核生效）。retry：重启后自动重新解析那些报错像是内核过期的链接。"""
        urls = [r.url for r in self.rows.values() if r.state == 'error' and r.outdated] if retry else []
        try:
            if urls:
                with open(RETRY_PATH, 'w', encoding='utf-8') as f:
                    json.dump(list(dict.fromkeys(urls)), f)
        except OSError:
            pass
        self._restart_pending = True
        self._really_quit = True
        self.close()
        if self._restart_pending:           # 还有任务没下完、用户选了先不退出
            self._restart_pending = False
            try:
                os.remove(RETRY_PATH)
            except OSError:
                pass

    def _spawn_self(self):
        """启动一个新的自己（紧接着本进程退出）。"""
        if self.instance is not None:
            self.instance.close()       # 先放开「只能开一个」的锁，不然新进程会以为自己是第二个实例，直接退出
        args = [sys.executable, *sys.argv[1:]] if getattr(sys, 'frozen', False) else [sys.executable, *sys.argv]
        try:
            with proc_job.detached():   # 不跟着本进程一起被结束
                subprocess.Popen(args, cwd=os.getcwd(), close_fds=True)
        except OSError as e:
            log.warning('重启失败: %s', e)

    def _resume_retry(self):
        """更新内核后重启：自动重新解析重启前失败的链接。"""
        try:
            with open(RETRY_PATH, encoding='utf-8') as f:
                urls = json.load(f)
            os.remove(RETRY_PATH)
        except (OSError, ValueError):
            return
        urls = [u for u in urls if isinstance(u, str)][:200] if isinstance(urls, list) else []
        if urls and self._submit_urls(urls):
            self.notify(f'解析内核已更新到 {core_update.current_version(block=False)}，正在重新解析刚才失败的 {len(urls)} 个链接',
                        'ok')

    def _secure_cookies(self):
        """把数据文件夹里的明文登录信息加密保存（旧版本留下的、用户手动放进 cookies 文件夹的）。"""
        def work():
            try:
                core.adopt_stray_cookie_files()
                core.folder_cookies()
            except Exception as e:  # noqa: BLE001
                log.warning('加密登录信息失败: %s', e)
        threading.Thread(target=work, daemon=True).start()

    def _check_app_update(self):
        """启动时在后台看一下 GitHub 上有没有软件新版本；失败不打扰。
        查到了也不弹窗：只在侧边栏左下角出现一条「新版本 x.y.z · 查看」，点开才显示更新窗口。"""
        from . import app_update, update_ui
        proxy = (self.settings.proxy or '').strip()

        def work():
            update_ui.clean_downloads()
            try:
                rel = app_update.check('windows', proxy=proxy)
            except Exception as e:  # noqa: BLE001
                self.bridge.log.emit('info', f'检查软件新版本失败：{e}')
                return
            if rel and rel['version'] != self.settings.skip_app_version:
                self.bridge.log.emit('info', f'软件有新版本 {rel["version"]}（当前 {APP_VERSION}）')
                self.bridge.app_update.emit(rel)
        threading.Thread(target=work, daemon=True).start()

    def _on_app_update(self, rel):
        self._app_rel = rel
        self.update_hint.set_version(rel['version'])
        self.update_hint.setVisible(True)

    def show_update(self, rel=None):
        """打开更新窗口（点侧边栏的小条，或者在「设置 → 关于」里检查到新版本后点「查看更新」）。"""
        from . import update_ui
        rel = rel or self._app_rel
        if not rel:
            return
        if rel is not self._app_rel:
            self._on_app_update(rel)
        if self.settings_panel is not None and self.settings_panel.isVisible():
            self.settings_panel.close_panel()
        dlg = update_ui.UpdateDialog(self.centralWidget(), rel, (self.settings.proxy or '').strip())
        res = dlg.exec()
        if res == 'skip':
            self.settings.skip_app_version = rel['version']
            self._save_settings()
            self._app_rel = None
            self.update_hint.hide()
        elif res == 'install':
            self._install_update(dlg.installer, rel)

    def _install_update(self, path, rel):
        """安装包已经下载好、sha256 也核对过了：启动它，然后退出软件（安装程序要替换程序文件）。"""
        from . import update_ui
        active = [t for t in self.manager.tasks if t.status in (core.RUNNING, core.MERGING, core.QUEUED)]
        pc = self._pan_counts()
        n = len(active) + pc['running'] + pc['queued']
        if n and not ui.confirm(self, f'还有 {n} 个任务没下完，现在安装新版本吗？',
                                '安装时软件会退出，装好后自动重新打开。已下载的部分会保留：'
                                '视频下次下载同一清晰度时自动续传，网盘文件重新打开后自动继续。\n'
                                '选「稍后」的话，安装包留着，下次点「下载并安装」不用重新下载。',
                                ok='现在安装', cancel='稍后'):
            return
        try:
            update_ui.run_installer(path)
        except Exception as e:  # noqa: BLE001
            ui.alert(self, '安装程序没有启动', f'{e}\n\n可以到下载页手动下载安装包。')
            return
        self._append_log('info', f'开始安装新版本 {rel["version"]}，软件即将退出')
        self._force_quit = True
        self._quit()

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
                if self.thumbs.has_raw(row.cover_url):
                    m.addAction(ic('search'), '预览封面', lambda: self._preview_cover(token))
            m.addSeparator()
        m.addAction(ic('refresh'), '重新解析', lambda: self._start_parse(token))
        m.addAction(ic('copy'), '复制链接', lambda: QGuiApplication.clipboard().setText(row.url))
        m.addAction(ic('external'), '在浏览器中打开', lambda: QDesktopServices.openUrl(QUrl(row.url)))
        if row.extra is None and os.name == 'nt':
            m.addAction(ic('globe'), '用浏览器打开并嗅探', lambda: self.sniff_start(row.url))
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
        if task.status in (core.DONE, core.EXISTS):
            self.cloud_menu(m, [self._task_path(task)], lambda: self.cloud_upload_task(task, manual=True))
            if self.cloud and (self.cloud.group_state(f'task:{task.id}') or ('',))[0] == 'failed':
                m.addAction(ic('refresh'), '重试上传到云盘', lambda: self.cloud_retry(f'task:{task.id}'))
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
            ui.alert(self, '无法使用这个文件夹', str(e))
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
        if key == 'show_hdr' and self.chip_hdr.isChecked() != s.show_hdr:      # 设置里改的：同步输入框下的开关
            self.chip_hdr.blockSignals(True)
            self.chip_hdr.setChecked(s.show_hdr)
            self.chip_hdr.blockSignals(False)
            self.chip_hdr.update()
        if key in ('codec_preference', 'show_hdr'):
            for row in self.rows.values():
                if row.state == 'ok':
                    row.video.options = core.build_options(row.video.info, s.codec_preference, hdr=s.show_hdr)
                    self._render_result(row)
        elif key == 'sub_langs':
            for row in self.rows.values():
                if row.state == 'ok':
                    self._render_result(row)
        elif key in ('save_cover', 'save_danmaku', 'save_subs') or (key == 'cloud_upload' and self.chip_cloud):
            chip = {'save_cover': self.chip_cover, 'save_danmaku': self.chip_dm, 'save_subs': self.chip_sub,
                    'cloud_upload': self.chip_cloud}[key]
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
        elif key == 'show_tips':
            ui.set_tips(s.show_tips)
            self._refresh_sidebar()
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
        if not ui.confirm(self, '退出 B站登录？', '本机保存的 B站登录信息会被删除。', ok='退出登录', danger=True):
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
        msg = logsafe.redact(msg)       # 签名参数、Cookie、Windows 用户名打码（日志是给用户复制出去反馈问题用的）
        prefix = {'warning': '[警告] ', 'error': '[错误] '}.get(level, '')
        self.log_view.appendPlainText(prefix + msg)
        getattr(log, 'error' if level == 'error' else 'warning' if level == 'warning' else 'info')(msg)

    def _refresh_status(self):
        if core.startup_log:            # 界面接上日志之前产生的消息（如内核自检）
            msgs, core.startup_log[:] = list(core.startup_log), []
            for level, msg in msgs:
                self._append_log(level, msg)
        if core.imported_plain:
            names, core.imported_plain[:] = list(core.imported_plain), []
            self._append_log('info', '这些 cookies 文件已加密保存，明文文件已删除：' + '、'.join(names))
            self.notify(f'已把 {len(names)} 个 cookies 文件加密保存，明文文件已删除', 'ok')
        # 窗口缩到托盘 / 最小化时没人看：每 3 秒统计一次就够了（只更新托盘提示），回来时恢复
        hidden = not self.isVisible() or self.isMinimized()
        want = 3000 if hidden else 600
        if self.timer.interval() != want:
            self.timer.setInterval(want)
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
        if self.tray:
            tip = APP_NAME + (f'\n下载中 {n_run} 个' + (f' · {fmt_speed(speed)}' if speed else '') +
                              (f' · 等待 {queued} 个' if queued else '') if n_run + queued else '')
            if tip != getattr(self, '_tray_tip', None):
                self._tray_tip = tip
                self.tray.setToolTip(tip)
        if hidden:
            return
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
        now = time.monotonic()
        if now - self._greet_checked >= 60:      # 问候语：时段变了才换，一分钟看一次就够
            self._greet_checked = now
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
        if (active or n_pan) and self._force_quit:
            for t in active:
                t._stop = 'pause'
        elif active or n_pan:
            what = '重启' if self._restart_pending else '退出'
            if not ui.confirm(self, f'还有 {len(active) + n_pan} 个任务没下完，确定{what}吗？',
                              '已下载的部分会保留：视频下次下载同一清晰度时自动续传，网盘文件下次打开软件自动继续。',
                              ok=what, danger=True):
                event.ignore()
                return
            for t in active:
                t._stop = 'pause'
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self._save_settings()
        self._save_tasks()
        self.pan.stop()
        if 'app.browser' in sys.modules:        # 用过内置浏览器：关掉还开着的窗口（它开着调试端口，不能留下）
            try:
                sys.modules['app.browser'].shutdown()
            except Exception as e:  # noqa: BLE001
                log.warning('关闭内置浏览器失败: %s', e)
        self.hotkey.unregister()
        if self.tray:
            self.tray.hide()
        event.accept()
        if self._restart_pending:
            self._restart_pending = False
            self._spawn_self()
        QApplication.instance().quit()


def _setup_logging():
    log_dir = os.path.join(DATA, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(os.path.join(log_dir, 'app.log'), maxBytes=1_000_000, backupCount=2,
                                      encoding='utf-8')
        handler.setFormatter(logsafe.Formatter('%(asctime)s %(levelname)s %(message)s'))
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
    _s = Settings.load()
    theme.apply(app, _s.theme, oled=_s.dark_oled)
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
    proc_job.install()                      # 之后启动的 ffmpeg、网盘引擎等子进程跟着软件一起退出
    win = MainWindow(instance)
    win.show()
    if instance is not None:
        activation_timer = QTimer(win)
        activation_timer.timeout.connect(
            lambda: win._show_from_tray() if instance.activation_requested() else None)
        activation_timer.start(150)
    sys.exit(app.exec())
