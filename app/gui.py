import datetime
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler

import shiboken6
from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import APP_NAME, APP_VERSION, bilibili, core, task_store, theme
from .bili_dialog import BiliLoginDialog
from .episode_dialog import EpisodeDialog
from .hotkey import GlobalHotkey
from .section_dialog import SectionDialog
from .config import ROOT, Settings, ffmpeg_dir
from .history import History
from .settings_dialog import SettingsDialog
from .widgets import (
    SPINNER, CoverDialog, CoverView, ElidedLabel, GlassCard, GradientBackground, ThumbLoader, Toast, cover_url,
    platform_pill, save_cover_bytes,
)

log = logging.getLogger('app')

PRESETS = [('逐个选择（默认最高）', None), ('全部最高画质', 'best'), ('全部 1080p', 1080), ('全部 720p', 720),
           ('全部 480p', 480), ('全部 360p', 360), ('全部仅音频 MP3', 'mp3')]

PROGRESS_STATE = {core.DONE: 'done', core.EXISTS: 'done', core.FAILED: 'failed', core.PAUSED: 'paused',
                  core.CANCELED: 'paused', core.MERGING: 'merging'}


def fmt_duration(sec):
    if not sec:
        return ''
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def fmt_speed(bps):
    return f'{core.fmt_size(bps)}/s' if bps else ''


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


class ResultRow:
    def __init__(self, token, url):
        self.token = token
        self.url = url
        self.state = 'parsing'
        self.video = None
        self.error = ''
        self.error_raw = ''
        self.combo = None
        self.cover = None
        self.busy_label = None
        self.badge = None
        self.auto_download = False   # 从选集加入：解析完自动下载
        self.from_collection = False

    @property
    def cover_url(self):
        return cover_url(self.video.info) if self.video else ''


def button(text, slot, name='small', tip=''):
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    b.clicked.connect(slot)
    return b


def restyle(w, name=None, **props):
    if name is not None:
        w.setObjectName(name)
    for k, v in props.items():
        w.setProperty(k, v)
    w.style().unpolish(w)
    w.style().polish(w)


def hbox_cell(*widgets, margins=(6, 0, 6, 0), spacing=6, stretch_end=True):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(*margins)
    h.setSpacing(spacing)
    for x in widgets:
        h.addWidget(x)
    if stretch_end:
        h.addStretch(1)
    return w


def vbox_cell(*widgets, margins=(6, 8, 6, 8), spacing=4):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(*margins)
    v.setSpacing(spacing)
    v.addStretch(1)
    for x in widgets:
        if isinstance(x, QWidget):
            v.addWidget(x)
        else:
            v.addLayout(x)
    v.addStretch(1)
    return w


class MainWindow(QMainWindow):
    R_COVER, R_VIDEO, R_QUALITY, R_OPS = range(4)
    T_COVER, T_FILE, T_PROGRESS, T_STATUS, T_OPS = range(5)

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
        self.task_widgets = {}      # task.id -> dict
        # 启动时剪贴板里已有的内容一律不理会，只提示之后新复制的链接
        self._last_clip = QGuiApplication.clipboard().text()
        self._clip_urls = []
        self._pending_cover_saves = {}   # url -> [(title, dir)]
        self.bili_account = None
        self._prev_status = {}           # task.id -> 上一次的状态（判断「刚完成」）
        self._batch_done = 0             # 本轮完成数，全部完成时提示
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

        self.setWindowTitle(f'{APP_NAME} {APP_VERSION}')
        self._build_ui()
        if self.settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
        else:
            avail = QGuiApplication.primaryScreen().availableGeometry()
            self.resize(min(1240, int(avail.width() * 0.9)), min(820, int(avail.height() * 0.9)))
            self.move(avail.center() - self.rect().center())

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(600)
        self.spin_timer = QTimer(self)
        self.spin_timer.setInterval(90)
        self.spin_timer.timeout.connect(self._spin)
        self._spin_i = 0
        QGuiApplication.instance().applicationStateChanged.connect(self._on_app_state)
        self._really_quit = False
        self.hotkey = GlobalHotkey(QApplication.instance(), self)
        self.hotkey.activated.connect(self._hotkey_parse)
        self._apply_hotkey(silent=True)
        if self.settings.check_core_update:
            QTimer.singleShot(8000, self._check_core_update)
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
        bg = GradientBackground()
        self.bg = bg
        root = QVBoxLayout(bg)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(12)

        # 标题栏
        head = QHBoxLayout()
        head.setSpacing(10)
        icon = QLabel()
        if app_icon_path():
            icon.setPixmap(QIcon(app_icon_path()).pixmap(34, 34))
        title = QLabel(APP_NAME)
        title.setObjectName('appTitle')
        sub = QLabel('粘贴分享链接 · 选择清晰度 · 一键下载')
        sub.setObjectName('appSub')
        tl = QVBoxLayout()
        tl.setSpacing(0)
        tl.addWidget(title)
        tl.addWidget(sub)
        head.addWidget(icon)
        head.addLayout(tl)
        head.addStretch(1)
        self.bili_btn = button('B站登录', self._bili_clicked, 'pill', '扫码登录 B站，解锁 1080P 高码率 / 4K 等大会员画质')
        head.addWidget(self.bili_btn)
        head.addWidget(button('⚙  设置', self._open_settings, 'pill'))
        root.addLayout(head)

        # 输入卡片
        card = GlassCard()
        bg.add_card(card)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(6)
        self.input = QPlainTextEdit()
        self.input.setObjectName('input')
        self.input.setPlaceholderText('在这里粘贴视频分享链接，或 App 里“复制链接”得到的整段文字……')
        self.input.setFixedHeight(68)
        hint = QLabel('支持 抖音 · 快手 · B站 · YouTube · X · TikTok 等上千个网站，可一次粘贴多条　　Ctrl+Enter 解析')
        hint.setObjectName('hint')
        left.addWidget(self.input)
        hint_row = QHBoxLayout()
        hint_row.setSpacing(8)
        hint_row.addWidget(hint)
        # 剪贴板提示条：复制了新链接后切回窗口时出现，点了才解析
        self.clip_bar = QWidget()
        cb = QHBoxLayout(self.clip_bar)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(6)
        self.clip_label = QLabel()
        self.clip_label.setObjectName('metaBusy')
        cb.addWidget(self.clip_label)
        cb.addWidget(button('解析', self._parse_clip, 'accent'))
        cb.addWidget(button('忽略', self._hide_clip_bar))
        self.clip_bar.hide()
        self._hint = hint
        hint_row.addWidget(self.clip_bar)
        hint_row.addStretch(1)
        left.addLayout(hint_row)
        right = QVBoxLayout()
        right.setSpacing(8)
        self.parse_btn = button('解  析', self._parse_input, 'primary')
        self.parse_btn.setMinimumWidth(150)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)
        sub_row.addWidget(button('粘贴并解析', self._paste_and_parse))
        sub_row.addWidget(button('清空', self.input.clear))
        right.addWidget(self.parse_btn)
        right.addLayout(sub_row)
        right.addStretch(1)
        cl.addLayout(left, 1)
        cl.addLayout(right)
        root.addWidget(card)
        QShortcut(QKeySequence('Ctrl+Return'), self.input, activated=self._parse_input)
        QShortcut(QKeySequence('Ctrl+Enter'), self.input, activated=self._parse_input)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)

        # 解析结果卡片
        rcard = GlassCard()
        bg.add_card(rcard)
        rv = QVBoxLayout(rcard)
        rv.setContentsMargins(14, 10, 14, 6)
        rv.setSpacing(6)
        rhead = QHBoxLayout()
        rhead.setSpacing(8)
        sec = QLabel('解析结果')
        sec.setObjectName('section')
        self.res_label = QLabel('')
        self.res_label.setObjectName('meta')
        rhead.addWidget(sec)
        rhead.addWidget(self.res_label)
        rhead.addStretch(1)
        lab = QLabel('清晰度')
        lab.setObjectName('meta')
        rhead.addWidget(lab)
        self.preset = QComboBox()
        for name, val in PRESETS:
            self.preset.addItem(name, val)
        self.preset.currentIndexChanged.connect(self._apply_preset_all)
        rhead.addWidget(self.preset)
        self.cover_check = QCheckBox('同时保存封面')
        self.cover_check.setToolTip('下载视频时，把封面另存为与视频同名的 JPG')
        self.cover_check.setChecked(self.settings.save_cover)
        self.cover_check.toggled.connect(self._toggle_save_cover)
        rhead.addSpacing(6)
        rhead.addWidget(self.cover_check)
        self.dm_check = QCheckBox('弹幕')
        self.dm_check.setToolTip('B站视频同时下载弹幕（转成 .danmaku.ass，PotPlayer / VLC 等播放器自动加载）')
        self.dm_check.setChecked(self.settings.save_danmaku)
        self.dm_check.toggled.connect(lambda v: self._toggle_setting('save_danmaku', v))
        self.sub_check = QCheckBox('字幕')
        self.sub_check.setToolTip('同时下载字幕（B站 CC 字幕、YouTube 字幕等，SRT 格式；语言可在设置里选）')
        self.sub_check.setChecked(self.settings.save_subs)
        self.sub_check.toggled.connect(lambda v: self._toggle_setting('save_subs', v))
        rhead.addWidget(self.dm_check)
        rhead.addWidget(self.sub_check)
        rhead.addSpacing(6)
        rhead.addWidget(button('全部下载', self._download_all, 'accent'))
        rhead.addWidget(button('清空结果', self._clear_results))
        rv.addLayout(rhead)
        self.res_table = QTableWidget(0, 4)
        self.res_table.setHorizontalHeaderLabels(['封面', '视频', '清晰度 / 格式', '操作'])
        self._setup_table(self.res_table, row_height=88, stretch=self.R_VIDEO,
                          widths={self.R_COVER: 146, self.R_QUALITY: 300, self.R_OPS: 250})
        self.res_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.res_table.customContextMenuRequested.connect(self._result_menu)
        rv.addWidget(self.res_table)
        splitter.addWidget(rcard)

        # 下载任务 / 日志卡片
        tcard = GlassCard()
        bg.add_card(tcard)
        tv = QVBoxLayout(tcard)
        tv.setContentsMargins(12, 6, 12, 8)
        self.tabs = QTabWidget()
        corner = hbox_cell(button('全部暂停', self._pause_all), button('全部继续', self._resume_all),
                           button('清除已结束', self._clear_finished), margins=(0, 0, 0, 4), stretch_end=False)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)
        self.task_table = QTableWidget(0, 5)
        self.task_table.setHorizontalHeaderLabels(['', '文件', '进度', '状态', '操作'])
        self._setup_table(self.task_table, row_height=62, stretch=self.T_FILE,
                          widths={self.T_COVER: 84, self.T_PROGRESS: 270, self.T_STATUS: 140, self.T_OPS: 180})
        self.task_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.task_table.customContextMenuRequested.connect(self._task_menu)
        self.task_table.cellDoubleClicked.connect(self._task_double_clicked)
        self.tabs.addTab(self.task_table, '下载任务')
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName('log')
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.tabs.addTab(self.log_view, '日志')
        self.tabs.addTab(self._build_history_tab(), '历史记录')
        self.tabs.currentChanged.connect(self._on_tab_changed)
        tv.addWidget(self.tabs)
        splitter.addWidget(tcard)
        splitter.setSizes([430, 300])
        root.addWidget(splitter, 1)

        # 底部：保存位置 + 状态
        bcard = GlassCard()
        bcard.radius = 12
        bg.add_card(bcard)
        bl = QHBoxLayout(bcard)
        bl.setContentsMargins(14, 7, 14, 7)
        bl.setSpacing(8)
        lab = QLabel('保存到')
        lab.setObjectName('meta')
        bl.addWidget(lab)
        self.dir_edit = QLineEdit(self.settings.download_dir)
        self.dir_edit.editingFinished.connect(self._dir_edited)
        bl.addWidget(self.dir_edit, 1)
        bl.addWidget(button('更改…', self._choose_dir))
        bl.addWidget(button('打开文件夹', self._open_dir))
        bl.addSpacing(18)
        self.status = QLabel()
        self.status.setObjectName('statusText')
        bl.addWidget(self.status)
        root.addWidget(bcard)

        self.setCentralWidget(bg)
        self.toast = Toast(bg)

    def _build_history_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(6)
        bar = QHBoxLayout()
        self.hist_search = QLineEdit()
        self.hist_search.setPlaceholderText('搜索标题 / 作者 / 平台')
        self.hist_search.setClearButtonEnabled(True)
        self.hist_search.textChanged.connect(lambda _: self._hist_timer.start())
        self._hist_timer = QTimer(self)
        self._hist_timer.setSingleShot(True)
        self._hist_timer.setInterval(200)
        self._hist_timer.timeout.connect(self._refresh_history)
        self.hist_count = QLabel('')
        self.hist_count.setObjectName('meta')
        bar.addWidget(self.hist_search, 1)
        bar.addWidget(self.hist_count)
        bar.addWidget(button('清空历史', self._clear_history))
        v.addLayout(bar)
        self.hist_table = QTableWidget(0, 5)
        self.hist_table.setHorizontalHeaderLabels(['时间', '视频', '清晰度', '大小', '操作'])
        self._setup_table(self.hist_table, row_height=44, stretch=1, widths={0: 120, 2: 130, 3: 90, 4: 260})
        self.hist_table.cellDoubleClicked.connect(self._hist_double_clicked)
        v.addWidget(self.hist_table)
        return page

    @staticmethod
    def _setup_table(table, row_height, stretch, widths):
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(row_height)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.verticalScrollBar().setSingleStep(24)
        table.viewport().setAutoFillBackground(False)
        hh = table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col in range(table.columnCount()):
            if col == stretch:
                hh.setSectionResizeMode(col, QHeaderView.Stretch)
            else:
                hh.setSectionResizeMode(col, QHeaderView.Fixed)
                table.setColumnWidth(col, widths.get(col, 80))

    def notify(self, text, kind='info'):
        self.toast.show_message(text, kind)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.place()

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, '_shown_once', False):
            self._shown_once = True
            self._style_titlebar()
            # 窗口出来后再在后台加载解析内核，第一次点「解析」时就已经准备好了
            QTimer.singleShot(30, lambda: threading.Thread(target=core.preload, daemon=True).start())

    def _style_titlebar(self):
        """Win11：让系统标题栏颜色和窗口渐变融为一体。"""
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            hwnd = int(self.winId())

            def dwm(attr, rgb):
                r, g, b = int(rgb[1:3], 16), int(rgb[3:5], 16), int(rgb[5:7], 16)
                value = ctypes.c_int(r | (g << 8) | (b << 16))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
            dwm(35, '#dfe3ff')    # 标题栏背景
            dwm(36, '#312e81')    # 标题文字
            dwm(34, '#c7d2fe')    # 窗口边框
        except Exception:  # noqa: BLE001 - 旧系统不支持时保持默认
            pass

    # ------------------------------------------------------------ 解析
    def _paste_and_parse(self):
        text = QGuiApplication.clipboard().text()
        if text.strip():
            self.input.setPlainText(text)
            self._parse_input()
        else:
            self.notify('剪贴板里没有文字', 'error')

    def _parse_input(self):
        urls = core.extract_urls(self.input.toPlainText())
        if not urls:
            self.notify('没有识别到链接', 'error')
            return
        added = self._submit_urls(urls)
        self.input.clear()
        skipped = len(urls) - added
        self.notify(f'开始解析 {added} 个链接' + (f'（{skipped} 个已在列表中）' if skipped else ''))

    def _submit_urls(self, urls, force=False, auto_download=False, from_collection=False):
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
            self.res_table.scrollToTop()
        return added

    def _new_row(self, url, after_token=None, at=None):
        token = self._next_token
        self._next_token += 1
        row = ResultRow(token, url)
        self.rows[token] = row
        r = self.res_table.rowCount() if at is None else min(at, self.res_table.rowCount())
        if after_token is not None:
            idx = self._result_index(after_token)
            if idx is not None:
                r = idx + 1
        self.res_table.insertRow(r)
        self._render_result(row, r)
        self._update_res_label()
        return token

    def _start_parse(self, token):
        row = self.rows[token]
        row.state, row.error = 'parsing', ''
        self._render_result(row)
        self._update_res_label()
        if not self.spin_timer.isActive():
            self.spin_timer.start()
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
        if row.cover_url:
            self.thumbs.request(row.cover_url, video.info.get('webpage_url') or row.url)
        if row.auto_download:
            row.auto_download = False
            if self._download_row(token, quiet=True):
                self.tabs.setCurrentIndex(0)

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

    def _result_index(self, token):
        for r in range(self.res_table.rowCount()):
            item = self.res_table.item(r, self.R_COVER)
            if item and item.data(Qt.UserRole) == token:
                return r
        return None

    def _render_result(self, row, r=None):
        if r is None:
            r = self._result_index(row.token)
            if r is None:
                return
        t = self.res_table
        token = row.token
        for col in range(t.columnCount()):
            t.setItem(r, col, QTableWidgetItem())
        t.item(r, self.R_COVER).setData(Qt.UserRole, token)

        cover = CoverView(128, 72)
        cover.clicked.connect(lambda: self._preview_cover(token))
        row.cover = cover
        row.busy_label = None
        row.combo = None
        t.setCellWidget(r, self.R_COVER, hbox_cell(cover, margins=(8, 0, 4, 0), stretch_end=False))

        if row.state == 'ok':
            v = row.video
            cover.set_placeholder(v.platform)
            pm = self.thumbs.get(row.cover_url)
            if pm:
                cover.set_pixmap(pm)
            title = ElidedLabel(v.title, 'videoTitle')
            title.set_full_text(v.title, f'{v.title}\n{row.url}')
            best = next((o.tag for o in v.options if o.kind == 'video'), '')
            meta_txt = ' · '.join(x for x in (v.uploader, fmt_duration(v.duration), f'最高 {best}' if best else '') if x)
            meta = ElidedLabel(meta_txt, 'meta')
            line = QHBoxLayout()
            line.setSpacing(8)
            line.addWidget(platform_pill(v.platform))
            badge = QLabel()
            badge.setStyleSheet('background: #d1fae5; color: #047857; border-radius: 8px; padding: 1px 8px;'
                                ' font-size: 11px; font-weight: 700;')
            row.badge = badge
            self._update_badge(row)
            line.addWidget(badge)
            if core.has_danmaku(v.info):
                line.addWidget(self._chip('弹幕', '#fce7f3', '#be185d', 'B站弹幕，可转成 ASS 字幕离线观看'))
            human, autos = core.pick_subtitles(v.info, self.settings.sub_langs)
            if human or autos:
                names = [core.sub_name(x) for x in human] + [core.sub_name(x, True) for x in autos]
                all_langs = [x for x in (v.info.get('subtitles') or {}) if x not in ('danmaku', 'live_chat')]
                line.addWidget(self._chip('字幕 ' + '/'.join(names), '#dbeafe', '#1d4ed8',
                                          f'将下载：{"、".join(names)}\n该视频共有 {len(all_langs)} 种人工字幕'))
            col = v.collection
            if col and col.get('entries') is None:
                cbtn = button('频道视频 · 选集…', lambda: self._open_collection(token), 'chip',
                              f'列出频道「{col.get("title") or ""}」的全部视频（最多 1000 个），勾选要下载的\n'
                              '（YouTube 的分享按钮会去掉列表信息；想下载某个播放列表，请复制浏览器地址栏里带 list= 的链接）')
                line.addWidget(cbtn)
            elif col:
                pos = f'{col["current"] + 1}/{len(col["entries"])}' if col.get('current', -1) >= 0 \
                    else f'{len(col["entries"])} 个'
                cbtn = button(f'{col["kind"]} {pos} · 选集…', lambda: self._open_collection(token), 'chip',
                              f'{col["kind"]}「{col.get("title") or ""}」共 {len(col["entries"])} 个视频，点击选择要下载哪几个')
                line.addWidget(cbtn)
            line.addWidget(meta, 1)
            t.setCellWidget(r, self.R_VIDEO, vbox_cell(title, line))

            combo = QComboBox()
            for opt in v.options:
                combo.addItem(opt.label, opt)
            if v.locked:
                need = '需要大会员账号' if bilibili.is_logged_in() else '点右上角「B站登录」扫码解锁'
                combo.addItem(f'🔒 {v.locked}（{need}）')
                combo.model().item(combo.count() - 1).setEnabled(False)
                combo.setToolTip(f'未解锁的清晰度：{v.locked}')
            row.combo = combo
            self._apply_preset(row)
            t.setCellWidget(r, self.R_QUALITY, vbox_cell(combo, margins=(4, 0, 10, 0)))

            dl = button('下载', lambda: self._download_row(token), 'accent')
            clip_btn = button('片段', lambda: self._download_section(token), tip='只下载其中一段（输入起止时间）')
            cov_btn = button('封面', lambda: self._save_cover(token), tip='保存封面到下载目录')
            cov_btn.setEnabled(bool(row.cover_url))
            if not row.cover_url:
                cov_btn.setToolTip('这个视频没有封面')
            rm = button('移除', lambda: self._remove_result(token))
            t.setCellWidget(r, self.R_OPS, hbox_cell(dl, clip_btn, cov_btn, rm, margins=(4, 0, 6, 0)))
        else:
            cover.set_placeholder('解析中' if row.state == 'parsing' else '失败')
            title = ElidedLabel(row.url, 'videoTitleDim')
            if row.state == 'parsing':
                meta = QLabel(f'{SPINNER[self._spin_i % len(SPINNER)]}  正在解析…')
                meta.setObjectName('metaBusy')
                row.busy_label = meta
                ops = hbox_cell(button('移除', lambda: self._remove_result(token)), margins=(4, 0, 6, 0))
            else:
                meta = ElidedLabel(row.error, 'metaError')
                meta.set_full_text(row.error, row.error_raw or row.error)
                ops = hbox_cell(button('重试', lambda: self._start_parse(token), 'accent'),
                                button('移除', lambda: self._remove_result(token)), margins=(4, 0, 6, 0))
            t.setCellWidget(r, self.R_VIDEO, vbox_cell(title, meta))
            t.setCellWidget(r, self.R_QUALITY, QWidget())
            t.setCellWidget(r, self.R_OPS, ops)

    def _spin(self):
        self._spin_i += 1
        frame = SPINNER[self._spin_i % len(SPINNER)]
        busy = False
        for row in self.rows.values():
            if row.state == 'parsing' and row.busy_label is not None and shiboken6.isValid(row.busy_label):
                row.busy_label.setText(f'{frame}  正在解析…')
                busy = True
        if not busy:
            self.spin_timer.stop()

    def _remove_result(self, token):
        r = self._result_index(token)
        if r is not None:
            self.res_table.removeRow(r)
        self.rows.pop(token, None)
        self._update_res_label()

    def _clear_results(self):
        self.res_table.setRowCount(0)
        self.rows.clear()
        self._update_res_label()

    def _update_res_label(self):
        total = len(self.rows)
        ok = sum(1 for r in self.rows.values() if r.state == 'ok')
        busy = sum(1 for r in self.rows.values() if r.state == 'parsing')
        txt = f'{ok} / {total}' if total else ''
        if busy:
            txt += f'　正在解析 {busy} 个'
        self.res_label.setText(txt)

    # ------------------------------------------------------------ 封面
    def _on_thumb_loaded(self, url):
        pm = self.thumbs.get(url)
        for row in self.rows.values():
            if row.state == 'ok' and row.cover_url == url and row.cover is not None and shiboken6.isValid(row.cover):
                if pm:
                    row.cover.set_pixmap(pm)
                else:
                    row.cover.set_placeholder(row.video.platform)
        for tid, w in self.task_widgets.items():
            if w['cover_url'] == url and pm and shiboken6.isValid(w['cover']):
                w['cover'].set_pixmap(pm)
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
        self.notify(f'✓ 封面已保存：{os.path.basename(path)}', 'ok')
        self._append_log('info', f'封面已保存 {path}')

    def _toggle_save_cover(self, checked):
        self.settings.save_cover = checked
        self._save_settings()

    # ------------------------------------------------------------ 清晰度预设
    def _apply_preset(self, row):
        preset = self.preset.currentData()
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
        self.manager.add(task)
        if not quiet:
            self.tabs.setCurrentIndex(0)
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
        self.manager.add(task)
        self.tabs.setCurrentIndex(0)
        self.notify('✓ 已加入片段下载', 'ok')

    def _flash_download_button(self, token):
        """点击「下载」后按钮立即变成绿色「✓ 已加入」，1.2 秒后恢复。"""
        r = self._result_index(token)
        cell = self.res_table.cellWidget(r, self.R_OPS) if r is not None else None
        btn = cell.findChild(QPushButton) if cell else None
        if btn is None:
            return
        btn.setText('✓ 已加入')
        restyle(btn, 'done')

        def revert():
            if shiboken6.isValid(btn):
                btn.setText('下载')
                restyle(btn, 'accent')
        QTimer.singleShot(1200, revert)

    def _download_all(self):
        n = skipped = 0
        for token, row in list(self.rows.items()):
            if self._download_row(token, quiet=True):
                n += 1
            elif row.state == 'ok' and row.combo is not None and row.combo.currentData() is not None and any(
                    h['quality'] == row.combo.currentData().tag and h['exists'] for h in self._history_of(row.video)):
                skipped += 1
        self.tabs.setCurrentIndex(0)
        if n:
            msg = f'✓ 已添加 {n} 个下载任务' + (f'，跳过 {skipped} 个已下载过的' if skipped else '')
        else:
            msg = f'这 {skipped} 个视频都已经下载过了（单独点「下载」可以重新下载）' if skipped else '没有可下载的结果'
        self.notify(msg, 'ok' if n else 'info')

    def _task_index(self, task_id):
        for r in range(self.task_table.rowCount()):
            item = self.task_table.item(r, self.T_COVER)
            if item and item.data(Qt.UserRole) == task_id:
                return r
        return None

    def _create_task_row(self, task):
        t = self.task_table
        r = t.rowCount()
        t.insertRow(r)
        for col in range(t.columnCount()):
            t.setItem(r, col, QTableWidgetItem())
        t.item(r, self.T_COVER).setData(Qt.UserRole, task.id)
        url = cover_url(task.video.info)
        cover = CoverView(64, 36, radius=6)
        cover.set_placeholder(task.video.platform)
        if self.thumbs.get(url):
            cover.set_pixmap(self.thumbs.get(url))
        elif url:
            self.thumbs.request(url, task.video.info.get('webpage_url') or task.video.url)
        t.setCellWidget(r, self.T_COVER, hbox_cell(cover, margins=(8, 0, 4, 0), stretch_end=False))

        name = ElidedLabel(task.video.title, 'videoTitle')
        extras = ['封面'] if task.save_cover else []
        if task.save_danmaku and self.settings.save_danmaku and core.has_danmaku(task.video.info):
            extras.append('弹幕')
        if task.save_subs and self.settings.save_subs and any(core.pick_subtitles(task.video.info, self.settings.sub_langs)):
            extras.append('字幕')
        tag = QLabel(task.tag.replace('] [', ' · ') + ''.join(f'　+{x}' for x in extras))
        tag.setObjectName('meta')
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(platform_pill(task.video.platform))
        line.addWidget(tag, 1)
        t.setCellWidget(r, self.T_FILE, vbox_cell(name, line, margins=(6, 6, 6, 6)))

        bar = QProgressBar()
        bar.setRange(0, 1000)
        detail = ElidedLabel('', 'meta')
        t.setCellWidget(r, self.T_PROGRESS, vbox_cell(bar, detail, margins=(6, 6, 12, 6)))

        b1 = button('暂停', lambda: self._task_primary(task))
        b2 = button('取消', lambda: self._task_secondary(task))
        b3 = button('目录', lambda: reveal_in_explorer(task.filepath or task._expected_name or task.out_dir))
        t.setCellWidget(r, self.T_OPS, hbox_cell(b1, b2, b3, margins=(4, 0, 6, 0)))
        self.task_widgets[task.id] = {'bar': bar, 'detail': detail, 'name': name, 'b1': b1, 'b2': b2, 'b3': b3,
                                      'cover': cover, 'cover_url': url, 'state': None}
        t.scrollToBottom()
        return r

    def _on_task_updated(self, task):
        r = self._task_index(task.id)
        if r is None:
            if task not in self.manager.tasks:
                return
            r = self._create_task_row(task)
        w = self.task_widgets[task.id]
        s = task.status
        prev = self._prev_status.get(task.id)
        if prev != s:
            self._prev_status[task.id] = s
            self._on_status_changed(task, prev)
        if task.filepath:
            w['name'].set_full_text(os.path.basename(task.filepath), task.filepath)
        w['bar'].setValue(int(task.progress * 1000))
        w['bar'].setFormat(f'{task.progress * 100:.1f}%')
        state = PROGRESS_STATE.get(s, 'running')
        if w['state'] != state:
            w['state'] = state
            restyle(w['bar'], state=state)

        parts = []
        if task.total:
            parts.append(f'{core.fmt_size(task.downloaded)} / {core.fmt_size(task.total)}'
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
        w['detail'].set_full_text('  ·  '.join(parts))

        status = core.STATUS_TEXT.get(s, s)
        if s == core.MERGING and task.stage:
            status = f'{task.stage}…'
        elif s == core.RUNNING and task.stage:
            status += f' · {task.stage}'
        if s == core.FAILED and task.error:
            status = f'失败：{task.error}'
        st = self.task_table.item(r, self.T_STATUS)
        st.setText(status)
        st.setForeground(QColor(theme.STATUS_COLORS.get(s, '#333')))
        st.setToolTip(task.error_raw or task.error or status)

        b1, b2 = w['b1'], w['b2']
        b1.setText({core.QUEUED: '暂停', core.RUNNING: '暂停', core.PAUSED: '继续', core.FAILED: '重试',
                    core.CANCELED: '重试', core.DONE: '打开', core.EXISTS: '打开'}.get(s, '暂停'))
        want = 'accent' if s in (core.DONE, core.EXISTS, core.PAUSED, core.FAILED, core.CANCELED) else 'small'
        if b1.objectName() != want:
            restyle(b1, want)
        b1.setEnabled(s != core.MERGING)
        b2.setText('移除' if s in (core.DONE, core.EXISTS, core.CANCELED, core.FAILED) else '取消')
        b2.setEnabled(s != core.MERGING or task.stage.startswith('弹幕版'))   # 生成弹幕版时可以取消

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
            self.notify(f'已恢复上次未完成的 {len(tasks)} 个任务（已暂停），点「全部继续」开始下载')

    def _toggle_setting(self, name, value):
        setattr(self.settings, name, value)
        self._save_settings()

    @staticmethod
    def _chip(text, bg, fg, tip=''):
        lab = QLabel(text)
        lab.setStyleSheet(f'background: {bg}; color: {fg}; border-radius: 8px; padding: 1px 8px;'
                          ' font-size: 11px; font-weight: 700;')
        if tip:
            lab.setToolTip(tip)
        return lab

    def _remove_task(self, task):
        if self.manager.remove(task):
            self._tasks_save_timer.start()
            r = self._task_index(task.id)
            if r is not None:
                self.task_table.removeRow(r)
            self.task_widgets.pop(task.id, None)

    def _open_file(self, task):
        # 有弹幕版时优先打开弹幕版（弹幕流畅，任何播放器都能看）
        path = task.burned_path if task.burned_path and os.path.exists(task.burned_path) else task.filepath
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            self.notify('文件不存在（可能已被移动或删除）', 'error')

    def _task_double_clicked(self, r, _c):
        task = self._task_at(r)
        if task and task.status in (core.DONE, core.EXISTS):
            self._open_file(task)

    def _task_at(self, r):
        item = self.task_table.item(r, self.T_COVER)
        if not item:
            return None
        tid = item.data(Qt.UserRole)
        return next((t for t in self.manager.tasks if t.id == tid), None)

    def _pause_all(self):
        n = 0
        for t in list(self.manager.tasks):
            if t.status in (core.QUEUED, core.RUNNING):
                self.manager.pause(t)
                n += 1
        self.notify(f'已暂停 {n} 个任务' if n else '没有进行中的任务')

    def _resume_all(self):
        n = 0
        for t in list(self.manager.tasks):
            if t.status == core.PAUSED:
                self.manager.resume(t)
                n += 1
        self.notify(f'已继续 {n} 个任务' if n else '没有暂停的任务')

    def _clear_finished(self):
        for t in list(self.manager.tasks):
            if t.status in (core.DONE, core.EXISTS, core.CANCELED):
                self._remove_task(t)

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
                if self.tabs.currentIndex() == 2:
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
        self.notify('✓ ' + msg, 'ok')
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

    def _on_tab_changed(self, idx):
        if idx == 2:
            self._refresh_history()

    def _refresh_history(self):
        t = self.hist_table
        t.setRowCount(0)
        if not self.history:
            self.hist_count.setText('历史记录不可用')
            return
        recs = self.history.recent(self.hist_search.text().strip())
        total = self.history.count()
        self.hist_count.setText(f'{len(recs)} / {total} 条')
        t.setRowCount(len(recs))
        for r, h in enumerate(recs):
            exists = bool(h['filepath']) and os.path.exists(h['filepath'])
            when = QTableWidgetItem(datetime.datetime.fromtimestamp(h['ts']).strftime('%Y-%m-%d %H:%M'))
            when.setData(Qt.UserRole, h)
            t.setItem(r, 0, when)
            for col in range(1, 5):
                t.setItem(r, col, QTableWidgetItem())
            title = ElidedLabel(h['title'] or h['url'], 'videoTitle' if exists else 'videoTitleDim')
            title.set_full_text(h['title'] or h['url'], f'{h["title"]}\n{h["filepath"]}' +
                                ('' if exists else '\n（文件已被移动或删除）'))
            t.setCellWidget(r, 1, hbox_cell(platform_pill(h['platform']), title, margins=(6, 0, 6, 0),
                                            stretch_end=False))
            t.item(r, 2).setText((h['quality'] or '').replace('] [', ' · '))
            t.item(r, 3).setText(core.fmt_size(h['size']) if h['size'] else '')
            ops = [button('打开', lambda _=False, p=h['filepath']: os.startfile(p), 'accent'),
                   button('目录', lambda _=False, p=h['filepath']: reveal_in_explorer(p)),
                   button('重新解析', lambda _=False, u=h['url']: self._reparse_url(u)),
                   button('删除', lambda _=False, i=h['id']: self._delete_history(i))]
            ops[0].setEnabled(exists)
            ops[1].setEnabled(exists)
            t.setCellWidget(r, 4, hbox_cell(*ops, margins=(4, 0, 6, 0)))

    def _hist_double_clicked(self, r, _c):
        h = self.hist_table.item(r, 0).data(Qt.UserRole) if self.hist_table.item(r, 0) else None
        if h and h['filepath'] and os.path.exists(h['filepath']):
            os.startfile(h['filepath'])

    def _reparse_url(self, url):
        if url and self._submit_urls([url]):
            self.notify('已加入解析列表')

    def _delete_history(self, row_id):
        self.history.delete(row_id)
        self._refresh_history()
        for r in self.rows.values():
            self._update_badge(r)

    def _clear_history(self):
        if not self.history or QMessageBox.question(
                self, APP_NAME, '确定清空全部下载历史吗？（只删除记录，不会删除已下载的文件）') != QMessageBox.Yes:
            return
        self.history.clear()
        self._refresh_history()
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
        urls = core.extract_urls(text)
        if not urls:
            self.notify('剪贴板里没有链接', 'error')
            return
        n = self._submit_urls(urls)
        self.notify(f'开始解析 {n} 个链接' if n else '这些链接已经在列表里了')

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
        self.notify(f'解析内核有新版本 {new}（当前 {cur}），可在 设置 → 内核 里一键更新')

    def _tray_message(self, title, text, icon=QSystemTrayIcon.Information):
        if self.tray:
            self.tray.showMessage(title, text, icon, 5000)

    # ------------------------------------------------------------ 右键菜单
    def _result_menu(self, pos):
        r = self.res_table.rowAt(pos.y())
        if r < 0:
            return
        token = self.res_table.item(r, self.R_COVER).data(Qt.UserRole)
        row = self.rows.get(token)
        if not row:
            return
        menu = QMenu(self)
        if row.state == 'ok':
            menu.addAction('下载', lambda: self._download_row(token))
            menu.addAction('下载片段…', lambda: self._download_section(token))
            if row.cover_url:
                menu.addAction('保存封面', lambda: self._save_cover(token))
                if row.cover_url in self.thumbs.raw:
                    menu.addAction('预览封面', lambda: self._preview_cover(token))
        menu.addAction('重新解析', lambda: self._start_parse(token))
        menu.addAction('复制链接', lambda: QGuiApplication.clipboard().setText(row.url))
        menu.addAction('在浏览器中打开', lambda: QDesktopServices.openUrl(QUrl(row.url)))
        if row.state == 'error':
            menu.addAction('复制错误信息', lambda: QGuiApplication.clipboard().setText(row.error_raw or row.error))
        menu.addSeparator()
        menu.addAction('移除', lambda: self._remove_result(token))
        menu.exec(self.res_table.viewport().mapToGlobal(pos))

    def _task_menu(self, pos):
        r = self.task_table.rowAt(pos.y())
        task = self._task_at(r) if r >= 0 else None
        if not task:
            return
        menu = QMenu(self)
        if task.status in (core.DONE, core.EXISTS):
            menu.addAction('打开文件', lambda: self._open_file(task))
            if task.burned_path and os.path.exists(task.burned_path) and task.filepath and task.filepath != task.burned_path:
                menu.addAction('打开原视频（不带弹幕）', lambda: os.startfile(task.filepath))
        menu.addAction('打开所在文件夹', lambda: reveal_in_explorer(task.filepath or task._expected_name or task.out_dir))
        if task.status in (core.FAILED, core.CANCELED, core.PAUSED):
            menu.addAction('重试 / 继续', lambda: self.manager.resume(task))
        menu.addAction('复制原链接', lambda: QGuiApplication.clipboard().setText(task.video.url))
        if task.error_raw:
            menu.addAction('复制错误信息', lambda: QGuiApplication.clipboard().setText(task.error_raw))
        if task.status not in (core.RUNNING, core.MERGING, core.QUEUED, core.PAUSED):
            menu.addSeparator()
            menu.addAction('从列表移除', lambda: self._remove_task(task))
        menu.exec(self.task_table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ 目录 / 设置
    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选择保存位置', self.settings.download_dir)
        if d:
            self._set_dir(os.path.normpath(d))

    def _dir_edited(self):
        d = self.dir_edit.text().strip()
        if d and d != self.settings.download_dir:
            self._set_dir(os.path.normpath(d))

    def _set_dir(self, d):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f'无法使用该目录：{e}')
            self.dir_edit.setText(self.settings.download_dir)
            return
        self.settings.download_dir = d
        self.dir_edit.setText(d)
        self._save_settings()
        self.notify('新任务将保存到这个目录')

    def _open_dir(self):
        d = self.settings.download_dir
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def _open_settings(self):
        old_codec = self.settings.codec_preference
        old_langs = self.settings.sub_langs
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self._save_settings()
            if self.settings.codec_preference != old_codec:
                for row in self.rows.values():
                    if row.state == 'ok':
                        row.video.options = core.build_options(row.video.info, self.settings.codec_preference)
                        self._render_result(row)
            for box, val in ((self.cover_check, self.settings.save_cover), (self.dm_check, self.settings.save_danmaku),
                             (self.sub_check, self.settings.save_subs)):
                box.blockSignals(True)
                box.setChecked(val)
                box.blockSignals(False)
            if self.settings.sub_langs != old_langs:
                for row in self.rows.values():
                    if row.state == 'ok':
                        self._render_result(row)
            self.manager._schedule()
            if (self.settings.hotkey or '') != self.hotkey.current:
                self._apply_hotkey()
            self.notify('✓ 设置已保存', 'ok')

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
        self.bili_btn.setText('B站：检查登录…')

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
        if not info:
            self.bili_btn.setText('B站登录')
        elif info.get('expired'):
            self.bili_btn.setText('B站：登录已失效，点此重新扫码')
        elif info.get('offline'):
            self.bili_btn.setText('B站：已登录')
        else:
            vip = info.get('vip_label') or ('大会员' if info.get('vip') else '非大会员')
            exp = bilibili.sessdata_expires()
            left = (exp - time.time()) / 86400 if exp else None
            if left is not None and left < 1.5:
                self.bili_btn.setText(f'B站 · {info.get("name")}（登录将在 {max(1, int(left * 24))} 小时后过期）')
                self.notify('B站登录快过期了，点右上角重新扫码即可续上', 'error')
            else:
                self.bili_btn.setText(f'B站 · {info.get("name")}（{vip}）')
            if exp:
                self.bili_btn.setToolTip(f'登录有效期至 {datetime.datetime.fromtimestamp(exp):%Y-%m-%d %H:%M}'
                                         '\n过期前会自动尝试续期；续不上时重新扫码即可')

    def _bili_clicked(self):
        info = self.bili_account
        logged = info and not info.get('expired')
        menu = QMenu(self)
        menu.addAction('长效登录（电视端扫码，约 180 天，推荐）', lambda: self._bili_login(True))
        menu.addAction('网页扫码登录（约 7 天）', lambda: self._bili_login(False))
        if logged:
            menu.addSeparator()
            menu.addAction('退出登录', self._bili_logout)
        menu.exec(self.bili_btn.mapToGlobal(self.bili_btn.rect().bottomLeft()))

    def _bili_login(self, long_term=True):
        dlg = BiliLoginDialog(self, long_term=long_term)
        if dlg.exec() and dlg.account is not None:
            self._on_bili_status(dlg.account)
            vip = dlg.account.get('vip')
            n = self._reparse_bilibili()
            msg = '✓ B站登录成功' + ('（大会员）' if vip else '（非大会员，1080P+ / 4K 仍需开通大会员）')
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
        self._clip_urls = urls
        first = urls[0] if len(urls[0]) <= 60 else urls[0][:57] + '…'
        self.clip_label.setText(f'📋 剪贴板里有新链接：{first}' if len(urls) == 1
                                else f'📋 剪贴板里有 {len(urls)} 个新链接')
        self.clip_label.setToolTip('\n'.join(urls))
        self._hint.hide()
        self.clip_bar.show()

    def _parse_clip(self):
        urls, self._clip_urls = self._clip_urls, []
        self._hide_clip_bar()
        n = self._submit_urls(urls)
        self.notify(f'开始解析 {n} 个链接' if n else '这些链接已经在列表里了')

    def _hide_clip_bar(self):
        self.clip_bar.hide()
        self._hint.show()

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
        speed = sum(t.speed or 0 for t in running if t.status == core.RUNNING)
        parts = [f'下载中 {len(running)}', f'等待 {queued}', f'完成 {done}']
        if failed:
            parts.append(f'失败 {failed}')
        if speed:
            parts.append(f'⚡ {fmt_speed(speed)}')
        self.status.setText('　·　'.join(parts))

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
        if active:
            ret = QMessageBox.question(
                self, APP_NAME, f'还有 {len(active)} 个任务未完成，确定退出吗？\n'
                                '（已下载的部分会保留，下次下载同一视频同一清晰度时自动续传）')
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            for t in active:
                t._stop = 'pause'
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self._save_settings()
        self._save_tasks()
        self.hotkey.unregister()
        if self.tray:
            self.tray.hide()
        event.accept()
        QApplication.instance().quit()


def _setup_logging():
    log_dir = os.path.join(ROOT, 'logs')
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
    app.setFont(QFont('Microsoft YaHei UI', 9))
    app.setStyleSheet(theme.stylesheet())
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
