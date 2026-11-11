"""设置面板：盖在主窗口上的模态面板（像 Claude 应用的设置），左边分类、右边一行行的选项，改了立即生效。"""
import datetime
import os
import sys
import threading
import weakref

import shiboken6
from PySide6.QtCore import QObject, QProcess, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION, avatar, bilibili, core_update, hotkey, pan, ui
from .config import (
    BROWSERS, CODEC_PREFERENCES, COOKIE_MODES, DATA, FILENAME_TEMPLATES, PENDING, ROOT, SUB_LANG_MODES,
    SUBFOLDER_MODES, has_legacy_data, import_legacy,
)
from .ui import Combo, HLine, IconButton, NavItem, Segmented, Toggle

PAGES = [('general', '常规', 'settings'), ('download', '下载', 'download'), ('danmaku', '字幕与弹幕', 'subtitles'),
         ('notify', '通知', 'bell'), ('network', '网络与登录', 'globe'), ('core', '解析内核', 'cube'),
         ('about', '关于', 'info')]


def _label(text, name, wrap=False):
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def _btn(text, slot, name=''):
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def _clear():
    w = QWidget()
    w.setObjectName('clear')
    return w


class _Desc(QLabel):
    """设置项标题下面的小字。里面可以有两种内容：
    - 说明 set_tip()：解释这个功能是做什么的、使用建议、举例。关掉「显示功能说明」后不显示。
    - 状态 set_status()：已登录、当前版本、保存路径、检查结果、错误和警告……一直显示。
    两种都没有（或者说明被关掉了又没有状态）时整行隐藏，标题自己垂直居中。"""
    every = weakref.WeakSet()

    def __init__(self, tip='', status=''):
        super().__init__()
        self.setObjectName('rowDesc')
        self.setWordWrap(True)
        self._tip, self._status, self._solo = tip or '', status or '', False
        self.title = None
        _Desc.every.add(self)
        self._apply(first=True)

    def set_tip(self, text):
        self._tip = text or ''
        self._apply()

    def set_status(self, text, error=False, solo=False):
        """solo：有这段状态时不显示说明（操作的结果、错误：这时候说明已经不重要了）。"""
        self._status, self._solo = text or '', bool(text) and (solo or error)
        name = 'metaError' if error and text else 'rowDesc'
        if self.objectName() != name:
            self.setObjectName(name)
            self.style().unpolish(self)
            self.style().polish(self)
        self._apply()

    def setText(self, text):        # 运行中直接改文字的地方（登录状态、检查结果）都是状态
        self.set_status(text, solo=True)

    def show(self):
        self._apply()

    def _apply(self, first=False):
        tip = '' if self._solo or not ui.tips_on() else self._tip
        # 路径里没有空格，很长时整行换不了行、会把页面撑宽：在 \ 后面加上「可以在这里换行」的零宽空格
        status = self._status.replace('\\', '\\​') if ':\\' in self._status else self._status
        if tip and status:
            # 短的写在一行，长的、带路径的分两行
            if '\n' in tip or tip.endswith('：') or len(tip) + len(status) > 46:
                text = tip + '\n' + status
            else:
                text = tip.rstrip('；。') + '；' + status
        else:
            text = (tip.rstrip('：') if tip.endswith('：') else tip) or status
        QLabel.setText(self, text)
        if not text:
            self.hide()
        elif not first and self.parentWidget() is not None:
            # 还没放进窗口的控件不能调用 setVisible(True)（会变成一个独立的小窗口闪一下），所以第一次不动
            self.setVisible(True)


class SettingsPanel(ui.Overlay):
    def __init__(self, host, win):
        super().__init__(host, 960, 680)
        self.win = win
        self.s = win.settings
        self._proc = None
        v = QVBoxLayout(self.panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(26, 18, 16, 10)
        head.addWidget(_label('设置', 'panelTitle'))
        head.addStretch(1)
        x = IconButton('x', '关闭（Esc）', size=34, icon_size=18)
        x.clicked.connect(self.close_panel)
        head.addWidget(x)
        v.addLayout(head)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        nav = QVBoxLayout()
        nav.setContentsMargins(16, 6, 8, 16)
        nav.setSpacing(2)
        self.navs = {}
        self._page_list = list(PAGES)
        self._page_list.insert(len(self._page_list) - 1, ('cloud', '谷歌云盘', 'cloud'))
        for key, name, icon in self._page_list:
            n = NavItem(name, icon, height=36)
            n.clicked.connect(lambda _=False, k=key: self.show_page(k))
            nav.addWidget(n)
            self.navs[key] = n
        nav.addStretch(1)
        navw = _clear()
        navw.setLayout(nav)
        navw.setFixedWidth(210)
        body.addWidget(navw)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        v.addLayout(body, 1)
        self.pages = {}
        for key, _, _ in self._page_list:
            col = _clear()
            lay = QVBoxLayout(col)
            lay.setContentsMargins(24, 0, 32, 28)
            lay.setSpacing(0)
            getattr(self, f'_page_{key}')(lay)
            lay.addStretch(1)
            sa = ui_scroll(col)
            self.stack.addWidget(sa)
            self.pages[key] = sa
        self._sig = _UpdateSignals()
        self._sig.progress.connect(lambda p: self.update_btn.setText(f'下载中 {p * 100:.0f}%'))
        self._sig.done.connect(self._frozen_update_done)
        self._sig.app_done.connect(self._app_check_done)
        self._sig.deno_progress.connect(lambda p: self.deno_btn.setText(f'下载中 {p * 100:.0f}%'))
        self._sig.deno_done.connect(self._deno_done)
        ui.tip_signals.changed.connect(self._tips_changed)
        self._refresh_core_version()
        self.show_page('general')

    # ------------------------------------------------------------ 框架
    def open_at(self, page=None):
        self.refresh_account()
        self.refresh_avatar()
        self.refresh_pan_accounts()
        self.refresh_pan_settings()
        self.refresh_sites()
        self._refresh_cookie_hint()
        self._refresh_core_version()
        if page:
            self.show_page(page)
        self.open()

    def show_page(self, key):
        for k, n in self.navs.items():
            n.setChecked(k == key)
        self.stack.setCurrentWidget(self.pages[key])

    def _section(self, lay, title, first=False):
        if not first:
            lay.addSpacing(26)
        lay.addWidget(_label(title, 'sectionTitle'))
        lay.addSpacing(2)
        self._fresh_section = True

    def _tips_changed(self, _on):
        for d in list(_Desc.every):
            if shiboken6.isValid(d):
                d._apply()

    def _row(self, lay, title, desc, control=None, last=False, tip=True, status=''):
        """一行设置：标题 + 小字 + 右边的控件。返回小字的标签（_Desc），之后可以 set_tip() / set_status()。
        desc 默认是「说明」（解释这个功能，受「显示功能说明」开关控制）；tip=False 表示它是状态（路径、警告……），一直显示。
        status：说明之外另有一段一直显示的内容（如限速的「0 = 不限速」）。"""
        w = _clear()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 13, 0, 13)
        h.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(3)
        t = _label(title, 'rowLabel')
        left.addWidget(t)
        d = _Desc(desc if tip else '', status or ('' if tip else desc))
        d.title = t
        left.addWidget(d)
        h.addLayout(left, 1)
        h.setAlignment(left, Qt.AlignVCenter)       # 小字隐藏后，标题和右边的控件对齐在一行的中间
        if control is not None:
            if isinstance(control, QWidget):
                h.addWidget(control, 0, Qt.AlignVCenter)
            else:
                h.addLayout(control)
        if not getattr(self, '_fresh_section', False):
            lay.addWidget(HLine())
        self._fresh_section = False
        lay.addWidget(w)
        return d

    def _toggle(self, key, on_change=None):
        t = Toggle(getattr(self.s, key))

        def changed(on):
            setattr(self.s, key, on)
            self.win.setting_changed(key)
            if on_change:
                on_change(on)
        t.toggled.connect(changed)
        return t

    def _combo(self, key, items, width=300):
        cb = Combo()
        for k, name in items:
            cb.addItem(name, k)
        cb.setCurrentIndex(max(0, cb.findData(getattr(self.s, key))))
        cb.setFixedWidth(width)

        def changed():
            setattr(self.s, key, cb.currentData())
            self.win.setting_changed(key)
        cb.currentIndexChanged.connect(changed)
        return cb

    def _spin(self, key, lo, hi):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(getattr(self.s, key))
        sp.setFixedWidth(90)
        sp.setAlignment(Qt.AlignCenter)

        def changed(v):
            setattr(self.s, key, v)
            self.win.setting_changed(key)
        sp.valueChanged.connect(changed)
        return sp

    # ------------------------------------------------------------ 常规
    def _page_general(self, lay):
        self._section(lay, '外观', first=True)
        seg = Segmented([('system', 'monitor', '跟随系统'), ('light', 'sun', '浅色'), ('dark', 'moon', '深色')],
                        self.s.theme)
        seg.changed.connect(self.win.apply_theme)
        self._row(lay, '主题', '跟随 Windows 的浅色 / 深色设置，或固定一种', seg)
        self._row(lay, '深色时用纯黑（OLED）', '背景变成纯黑，OLED 屏上黑色部分不发光、更省电。已针对纯黑做了护眼处理：'
                  '文字不用纯白、滚动的内容放在深灰卡片上减少拖影、用细边框分层。看久了累眼可以关掉，用官方的深灰色',
                  self._toggle('dark_oled', on_change=self.win.apply_oled))
        self._row(lay, '显示功能说明', '关掉后，每个选项下面解释它是做什么的小字不再显示，界面更干净；'
                  '状态（已登录、当前版本、保存位置）、错误和警告不受影响', self._toggle('show_tips'))
        self._section(lay, '个人')
        box = QHBoxLayout()
        box.setSpacing(6)
        self.avatar_view = ui.Avatar(40)
        box.addWidget(self.avatar_view)
        box.addSpacing(8)
        self.avatar_btns = [_btn('选择图片…', self._avatar_change), _btn('调整…', self._avatar_adjust),
                            _btn('移除', self._avatar_remove, 'danger')]
        for b in self.avatar_btns:
            box.addWidget(b)
        self._row(lay, '头像', '选一张图片，拖动、缩放框出圆形区域；显示在侧边栏左下角', box)
        self.refresh_avatar()
        nick = QLineEdit(self.s.nickname)
        nick.setPlaceholderText('例如：Moyer')
        nick.setFixedWidth(220)
        nick.setMaxLength(20)

        def set_nick():
            if nick.text().strip() != self.s.nickname:
                self.s.nickname = nick.text().strip()
                self.win.setting_changed('nickname')
        nick.editingFinished.connect(set_nick)
        self._row(lay, '首页该怎么称呼你？', '显示在首页的问候语里，比如「晚上好，Moyer」；留空就只打招呼', nick)
        self._section(lay, '后台与快捷键')
        self._row(lay, '关闭窗口时缩小到托盘', '下载在后台继续；右键托盘图标选「退出」才会真正退出', self._toggle('close_to_tray'))
        self.hotkey_edit = QLineEdit(self.s.hotkey)
        self.hotkey_edit.setPlaceholderText('例如 Ctrl+Alt+D')
        self.hotkey_edit.setFixedWidth(180)
        self.hotkey_edit.editingFinished.connect(self._set_hotkey)
        self.hotkey_desc = self._row(lay, '全局快捷键', '在任何地方复制视频链接后按下，软件会弹出来并开始解析；留空 = 不使用',
                                     self.hotkey_edit)
        self._row(lay, '剪贴板提示', '复制了新链接、切回本窗口时，在输入框上方提示是否解析',
                  self._toggle('auto_parse_clipboard'))

    def refresh_avatar(self):
        img = avatar.image()
        self.avatar_view.set_image(img)
        self.avatar_view.set_text(self.s.nickname)
        self.avatar_btns[0].setText('更换…' if img is not None else '选择图片…')
        for b in self.avatar_btns[1:]:
            b.setVisible(img is not None)    # 这些按钮已经在面板里，可以安全地 show

    def _avatar_change(self):
        if avatar.change(self.win):
            self.win.setting_changed('avatar')

    def _avatar_adjust(self):
        if avatar.adjust(self.win):
            self.win.setting_changed('avatar')

    def _avatar_remove(self):
        if ui.confirm(self.win, '移除头像？', '移除后左下角恢复显示名字首字母。', ok='移除', danger=True):
            avatar.remove()
            self.win.setting_changed('avatar')

    def _set_hotkey(self):
        text = self.hotkey_edit.text().strip()
        ok = not text or hotkey.parse(text) is not None
        # 格式不对是错误，一直显示；平时的那句是说明
        self.hotkey_desc.set_status('' if ok else '格式不对：需要 Ctrl / Alt / Win + 字母、数字或 F1~F12，例如 Ctrl+Alt+D',
                                    error=not ok)
        if ok and text != self.s.hotkey:
            self.s.hotkey = text
            self.win.setting_changed('hotkey')

    # ------------------------------------------------------------ 下载
    def _page_download(self, lay):
        self._section(lay, '保存', first=True)
        box = QHBoxLayout()
        box.setSpacing(6)
        box.addWidget(_btn('打开', self.win._open_dir))
        box.addWidget(_btn('更改…', self._change_dir))
        self.dir_desc = self._row(lay, '保存位置', self.s.download_dir, box, tip=False)
        self._row(lay, '自动分文件夹', '', self._combo('subfolder', SUBFOLDER_MODES.items()))
        self._row(lay, '文件名格式', '', self._combo('filename_template',
                                                     [(k, v[0]) for k, v in FILENAME_TEMPLATES.items()]))
        self._section(lay, '速度')
        self._row(lay, '同时下载的任务数', '多个视频同时下载；网速一般时 2～3 个最合适', self._spin('max_concurrent', 1, 10))
        self._row(lay, '分片下载线程', '分片格式（HLS / DASH，如 X、部分 YouTube 格式）每个任务的并发连接数',
                  self._spin('fragment_threads', 1, 16))
        self._section(lay, '格式与封面')
        self._row(lay, '编码偏好', '同一清晰度有多种编码时优先选哪种（YouTube、B站常见）',
                  self._combo('codec_preference', CODEC_PREFERENCES.items(), 340))
        self._row(lay, '显示 HDR / 杜比视界版本', '打开后，清晰度列表里会多出 HDR、杜比视界版本（任何电脑都能下载；在支持 HDR 的'
                  '屏幕上看颜色更鲜艳，普通屏幕上可能偏灰）。关闭则只列普通版本。输入框下方的「HDR」开关也能切换',
                  self._toggle('show_hdr'))
        self._row(lay, '同时保存封面', '下载视频时另存一张与视频同名的 JPG（也可以在输入框下方的「封面」开关切换）',
                  self._toggle('save_cover'))
        self._row(lay, '把封面写进视频文件', '资源管理器缩略图、播放器里直接显示封面', self._toggle('embed_cover'))
        self._section(lay, '网盘下载')
        self.pan_spins = {}
        self._row(lay, '同时下载的文件数', '网盘分享里的文件同时下载几个', self._pan_spin('maxConcurrent', 1, 20))
        self._row(lay, '每个文件的连接数', '多条连接一起下，能跑满带宽（百度网盘非会员会自动只用 1 条，避免被限速）',
                  self._pan_spin('connsPerTask', 1, 64))
        self._row(lay, '限速', '所有网盘下载加起来的速度上限', self._pan_spin('speedLimitKB', 0, 1_000_000, 130, ' KB/s'),
                  status='0 = 不限速')
        self._row(lay, '失败后自动重试', '网络断了、下载地址过期等情况自动重试的次数', self._pan_spin('maxAutoRetries', 0, 20))

    def _pan_spin(self, key, lo, hi, width=90, suffix=''):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setFixedWidth(width)
        sp.setAlignment(Qt.AlignCenter)
        if suffix:
            sp.setSuffix(suffix)
        if key == 'speedLimitKB':
            sp.setSpecialValueText('不限速')
        sp.setValue(int(self.win.pan.settings.get(key, lo) or lo))
        sp.editingFinished.connect(lambda k=key, w=sp: self._set_pan(k, w.value()))
        sp.valueChanged.connect(lambda v, k=key, w=sp: None if w.hasFocus() else self._set_pan(k, v))
        self.pan_spins[key] = sp
        return sp

    def _set_pan(self, key, value):
        eng = self.win.pan
        if not eng.settings or eng.settings.get(key) == value:
            return
        st = dict(eng.settings)
        st[key] = value
        eng.settings.update(st)
        eng.call('saveSettings', {'settings': st}, lambda r, e: r and eng.settings.update(r))

    def refresh_pan_settings(self):
        for key, sp in self.pan_spins.items():
            if key in self.win.pan.settings:
                sp.blockSignals(True)
                sp.setValue(int(self.win.pan.settings[key] or 0))
                sp.blockSignals(False)
            sp.setEnabled(self.win.pan.alive)

    def _change_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选择保存位置', self.s.download_dir)
        if d and self.win._set_dir(os.path.normpath(d)):
            self.dir_desc.setText(self.s.download_dir)

    # ------------------------------------------------------------ 字幕与弹幕
    def _page_danmaku(self, lay):
        self._section(lay, 'B站弹幕', first=True)
        self._row(lay, '下载弹幕', '转成与视频同名的 .danmaku.ass，PotPlayer / VLC 等播放器自动加载',
                  self._toggle('save_danmaku'))
        self._row(lay, '生成「弹幕版」视频', '把弹幕按高帧率画进视频，任何播放器都和网页一样顺滑',
                  self._toggle('burn_danmaku'))
        fps = Combo()
        fps.addItem('60 帧', 60)
        fps.addItem('120 帧（高刷屏更顺，文件更大）', 120)
        fps.setCurrentIndex(1 if self.s.danmaku_fps == 120 else 0)
        fps.setFixedWidth(300)

        def set_fps():
            self.s.danmaku_fps = fps.currentData()
            self.win.setting_changed('danmaku_fps')
        fps.currentIndexChanged.connect(set_fps)
        self._row(lay, '弹幕版帧率', '', fps)
        self._row(lay, '4K 视频的弹幕版最高 60 帧', '4K 120 帧要用软件逐帧编码，非常慢；生成的文件（H.264 Level 6）很多手机、电视也硬件解码不了。'
                  '关闭后 4K 视频也按上面选的帧率生成', self._toggle('danmaku_4k_60'))
        self._row(lay, '生成弹幕版后保留原视频', '默认只留流畅的弹幕版，省空间；打开后同时保留原视频和外挂弹幕文件',
                  self._toggle('keep_original'))
        self._section(lay, '字幕')
        self._row(lay, '下载字幕', 'B站 CC 字幕、YouTube 字幕等，保存为与视频同名的 SRT', self._toggle('save_subs'))
        self._row(lay, '字幕语言', '', self._combo('sub_langs', SUB_LANG_MODES.items(), 380))

    # ------------------------------------------------------------ 通知
    def _page_notify(self, lay):
        self._section(lay, '下载完成后', first=True)
        self._row(lay, '系统通知', '窗口不在前台时，在屏幕右下角弹出通知', self._toggle('notify_done'))
        self._row(lay, '提示音', '一批任务全部完成时响一声', self._toggle('notify_sound'))
        self._row(lay, '自动打开下载文件夹', '一批任务全部完成时打开', self._toggle('open_dir_when_done'))

    # ------------------------------------------------------------ 网络与登录
    def _page_network(self, lay):
        self._section(lay, '网络', first=True)
        proxy = QLineEdit(self.s.proxy)
        proxy.setPlaceholderText('留空 = 跟随系统代理，例如 http://127.0.0.1:7890')
        proxy.setFixedWidth(300)

        def set_proxy():
            if proxy.text().strip() != self.s.proxy:
                self.s.proxy = proxy.text().strip()
                self.win.setting_changed('proxy')
        proxy.editingFinished.connect(set_proxy)
        proxy.setToolTip('例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080')
        self._row(lay, '代理', '网盘下载重启软件后生效', proxy, tip=False)
        self._section(lay, '网站登录')
        # 每个网站一行（登录过的其它网站也列在这里）：内容会变，放在单独的容器里，refresh_sites() 重画
        wrap = _clear()
        self.sites_box = QVBoxLayout(wrap)
        self.sites_box.setContentsMargins(0, 0, 0, 0)
        self.sites_box.setSpacing(0)
        lay.addWidget(wrap)
        obox = QHBoxLayout()
        obox.setSpacing(6)
        self.site_other = QLineEdit()
        self.site_other.setPlaceholderText('网址，例如 https://www.example.com')
        self.site_other.setFixedWidth(260)
        self.site_other.returnPressed.connect(self._site_other)
        obox.addWidget(self.site_other)
        self.site_other_btn = _btn('登录', self._site_other)
        obox.addWidget(self.site_other_btn)
        self._fresh_section = False
        self.site_other_desc = self._row(lay, '其它网址', '需要登录才能看的其它网站：填网址，在打开的浏览器窗口里登录', obox)
        self._section(lay, 'B站账号')
        self.bili_box = QHBoxLayout()
        self.bili_box.setSpacing(6)
        self.bili_desc = self._row(lay, '扫码登录', '', self.bili_box)
        self._section(lay, '网盘账号')
        self.pan_rows = {}
        for prov in pan.LOGIN_PROVIDERS:
            box = QHBoxLayout()
            box.setSpacing(6)
            self.pan_rows[prov] = (self._row(lay, pan.PROVIDERS[prov], '', box), box)
        self._row(lay, 'Mega', 'Mega 的分享链接不需要登录就能下载')
        self._section(lay, '高级：其它登录方式')
        self.cookie_hint = self._row(lay, 'cookies 文件夹', '', _btn('打开文件夹', self._open_cookie_dir))
        mode = self._combo('cookie_mode', COOKIE_MODES.items(), 220)
        mode.currentIndexChanged.connect(self._sync_cookie_widgets)
        self.cookie_mode = mode
        self._row(lay, '额外的 cookies 来源', '一般不需要：用上面的「网站登录」就够了', mode)
        self.browser = Combo()
        self.browser.addItems(BROWSERS)
        if self.s.cookie_browser in BROWSERS:
            self.browser.setCurrentText(self.s.cookie_browser)
        self.browser.setFixedWidth(220)

        def set_browser():
            self.s.cookie_browser = self.browser.currentText()
            self.win.setting_changed('cookie_browser')
        self.browser.currentIndexChanged.connect(set_browser)
        self._row(lay, '从哪个浏览器读取', '新版 Chrome / Edge 的 cookies 读不出来，推荐 Firefox', self.browser, tip=False)
        fbox = QHBoxLayout()
        fbox.setSpacing(6)
        self.cookie_file = QLineEdit(self.s.cookie_file)
        self.cookie_file.setPlaceholderText('Netscape 格式的 cookies.txt')
        self.cookie_file.setFixedWidth(260)
        self.cookie_file.editingFinished.connect(self._set_cookie_file)
        fbox.addWidget(self.cookie_file)
        fbox.addWidget(_btn('浏览…', self._pick_cookie_file))
        self._row(lay, 'cookies.txt 文件', '', fbox)
        self._sync_cookie_widgets()

    # 网站登录（内置浏览器） ---------------------------------------------
    def refresh_sites(self):
        from . import browser, site_login
        from .gui import fmt_ago
        box = self.sites_box
        while box.count():
            w = box.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        have, name = browser.available(), browser.find_browser()[1]
        st = site_login.status()
        self._fresh_section = True
        for site in site_login.listed():
            info = st.get(site.key)
            btns = QHBoxLayout()
            btns.setSpacing(6)
            if info:
                relogin = _btn('重新登录', lambda _=False, x=site: self._site_login(x))
                relogin.setEnabled(have)
                btns.addWidget(relogin)
                btns.addWidget(_btn('退出登录', lambda _=False, x=site: self._site_logout(x), 'danger'))
                desc = f'已登录 · {fmt_ago(info.get("saved") or 0)}读取（用之前会自动刷新）'
            else:
                b = _btn('登录', lambda _=False, x=site: self._site_login(x), 'primary')
                b.setEnabled(have)
                btns.addWidget(b)
                desc = '未登录'
            self._row(box, site.name, desc, btns, tip=False)
        self._fresh_section = False
        self.site_other.setEnabled(have)
        self.site_other_btn.setEnabled(have)
        self.site_other_desc.set_tip(f'需要登录才能看的其它网站：填网址，在打开的 {name} 窗口里登录' if have else '')
        self.site_other_desc.set_status(
            '登录信息加密保存在本机' if have else
            '没有找到 Edge 或 Chrome，网站登录用不了：可以用下面「高级」里的 cookies 文件夹导入 cookies.txt')

    def _site_login(self, site):
        from . import login_ui
        if login_ui.login(self.win, site):
            self.win._after_site_login(site)
        self.refresh_sites()

    def _site_logout(self, site):
        from . import login_ui
        login_ui.logout(self.win, site, self.refresh_sites)

    def _site_other(self):
        from . import site_login
        site = site_login.custom(self.site_other.text())
        if site is None:
            self.win.notify('请填要登录的网站网址，例如 https://www.example.com', 'error')
            return
        self.site_other.clear()
        self._site_login(site)

    def refresh_account(self):
        while self.bili_box.count():
            w = self.bili_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        info = self.win.bili_account
        logged = info and not info.get('expired')
        if logged:
            vip = info.get('vip_label') or ('大会员' if info.get('vip') else '非大会员')
            exp = bilibili.sessdata_expires()
            until = f'，有效期至 {datetime.datetime.fromtimestamp(exp):%Y-%m-%d}（过期前自动续期）' if exp else ''
            self.bili_desc.set_tip('')
            self.bili_desc.set_status(f'已登录：{info.get("name") or "（网络不通，稍后显示）"} · {vip}{until}')
            self.bili_box.addWidget(_btn('重新扫码', lambda: self.win._bili_login(True)))
            self.bili_box.addWidget(_btn('退出登录', self.win._bili_logout, 'danger'))
        else:
            self.bili_desc.set_tip('登录后解锁 1080P 高码率 / 4K 等大会员画质。长效登录约 180 天，网页登录约 7 天')
            self.bili_desc.set_status(('登录已失效，请重新扫码。' if info and info.get('expired') else '未登录。')
                                      + '登录信息加密保存在本机')
            self.bili_box.addWidget(_btn('长效登录', lambda: self.win._bili_login(True), 'primary'))
            self.bili_box.addWidget(_btn('网页登录', lambda: self.win._bili_login(False)))

    def refresh_pan_accounts(self):
        eng = self.win.pan
        for prov, (desc, box) in self.pan_rows.items():
            while box.count():
                w = box.takeAt(0).widget()
                if w:
                    w.deleteLater()
            if not eng.alive:
                desc.setText(eng.error or '网盘解析组件正在启动…')
                desc.show()
        if not eng.alive:
            return

        def done(res, err):
            if err or not res:
                return
            for a in res:
                row = self.pan_rows.get(a.get('provider'))
                if not row:
                    continue
                desc, box = row
                prov = a['provider']
                if a.get('loggedIn'):
                    bits = [a.get('nickname') or '已登录', a.get('vip') or '']
                    if a.get('expiresAt'):
                        bits.append(f'有效期至 {datetime.datetime.fromtimestamp(a["expiresAt"]):%Y-%m-%d}')
                    desc.set_tip('')
                    desc.set_status('已登录：' + ' · '.join(b for b in bits if b))
                    box.addWidget(_btn('重新扫码', lambda p=prov: self._pan_login(p)))
                    box.addWidget(_btn('退出登录', lambda p=prov: self._pan_logout(p), 'danger'))
                else:
                    desc.set_tip(a.get('note') or '')
                    desc.set_status('未登录。登录信息加密保存在本机（只有当前 Windows 用户能解开）')
                    box.addWidget(_btn('扫码登录', lambda p=prov: self._pan_login(p), 'primary'))
                desc.show()
        eng.call('accounts', None, done)

    def _pan_login(self, provider):
        if self.win.pan_login(provider):
            self.refresh_pan_accounts()

    def _pan_logout(self, provider):
        name = pan.PROVIDERS.get(provider, provider)
        if not ui.confirm(self.win, f'退出{name}登录？', '本机保存的登录信息会被删除。', ok='退出登录', danger=True):
            return
        self.win.pan.call('logout', {'provider': provider}, lambda r, e: self.refresh_pan_accounts())

    def _refresh_cookie_hint(self):
        from . import core
        found = core.cookie_file_names()
        found = [n for n in found if not n.startswith(core.SITE_PREFIX)]      # 内置浏览器登录的在上面单独列了
        self.cookie_hint.set_tip('也可以导入 cookies.txt：用浏览器扩展「Get cookies.txt LOCALLY」在对应网站导出 .txt，'
                                 '放进 cookies 文件夹（点右边的按钮打开）')
        self.cookie_hint.set_status('软件读到后会加密保存，并删除明文文件。'
                                    f'当前已导入：{"、".join(found) if found else "（没有）"}')

    def _open_cookie_dir(self):
        from . import core
        os.makedirs(core.COOKIE_DIR, exist_ok=True)
        os.startfile(core.COOKIE_DIR)

    def _sync_cookie_widgets(self):
        mode = self.cookie_mode.currentData()
        self.browser.setEnabled(mode == 'browser')
        self.cookie_file.setEnabled(mode == 'file')
        if mode == 'file' and not self.s.cookie_file:
            self._pick_cookie_file()
            if not self.s.cookie_file:
                self.cookie_mode.setCurrentIndex(max(0, self.cookie_mode.findData('none')))

    def _pick_cookie_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 cookies.txt', self.cookie_file.text(),
                                              'Cookies (*.txt);;所有文件 (*)')
        if path:
            self.cookie_file.setText(path)
            self._set_cookie_file()

    def _set_cookie_file(self):
        path = self.cookie_file.text().strip()
        if path != self.s.cookie_file:
            self.s.cookie_file = path
            self.win.setting_changed('cookie_file')

    # ------------------------------------------------------------ 解析内核
    def _page_core(self, lay):
        self._section(lay, '解析内核（yt-dlp）', first=True)
        frozen = getattr(sys, 'frozen', False)
        self.update_btn = _btn('检查并更新', self._update_core, 'primary')
        self.core_desc = self._row(lay, '当前版本', '网站改版导致解析失败时，更新内核通常就能恢复。更新后重启软件生效。',
                                   self.update_btn)
        self._row(lay, '更新通道', '预览版每天更新，网站改版后修得最快，偶尔会有新问题；一般用稳定版就够了',
                  self._combo('core_channel', [('stable', '稳定版'), ('nightly', '预览版')], 220))
        self._row(lay, '启动时检查新版本', '解析失败、看起来像是内核过期时也会马上检查一次', self._toggle('check_core_update'))
        if frozen:
            self._row(lay, '自动下载新内核', '检查到新版本就在后台下载好，窗口底部提示「重启后生效」；关闭则只提示有新版本',
                      self._toggle('auto_core_update'))
            self.core_reset_row = self._row(lay, '恢复为内置版本', '在线更新后反而出问题时使用，重启后生效',
                                            _btn('恢复', self._reset_core))
        self._section(lay, 'YouTube 组件（Deno）')
        self.deno_btn = _btn('', self._deno_clicked)
        self.deno_desc = self._row(lay, 'YouTube 组件', '', self.deno_btn)
        self._refresh_deno()

    def _refresh_core_version(self):
        ver = core_update.current_version(block=False)
        frozen = getattr(sys, 'frozen', False)
        pending = core_update.pending_version() if frozen else None
        self.core_desc.title.setText(f'当前版本 {ver}' + ('（已在线更新）' if frozen and core_update.override_active() else ''))
        if pending:
            self.core_desc.setText(f'新版本 {pending} 已下载，重启软件后生效。')
            self.update_btn.setText('立即重启')
        self.about_ver.setText(f'版本 {APP_VERSION} · 解析内核 yt-dlp {ver}')
        self._refresh_deno()

    def core_downloaded(self, new):
        """后台自动下载好了新内核（主窗口通知过来）。"""
        self.update_btn.setEnabled(True)
        self._refresh_core_version()

    def _reset_core(self):
        core_update.reset_frozen()
        self.update_btn.setText('检查并更新')
        self.core_desc.setText('已恢复为内置版本，重启软件后生效。')
        self.win._core_pending, self.win._core_new = False, ''
        self.win.notice.show_notice('解析内核已恢复为内置版本，重启后生效', '立即重启', self.win._restart)
        self.win._place_toast()

    def _update_core(self):
        frozen = getattr(sys, 'frozen', False)
        if frozen and core_update.pending_version():        # 已经下载好了：按钮是「立即重启」
            self.win._restart()
            return
        self.update_btn.setEnabled(False)
        if frozen:
            self.update_btn.setText('检查中…')
            channel = self.s.core_channel

            def work():
                try:
                    old, new, updated = core_update.update_frozen(self._sig.progress.emit, channel)
                    self._sig.done.emit(True, old, new, updated, '')
                except Exception as e:  # noqa: BLE001
                    self._sig.done.emit(False, '', '', False, str(e))
            threading.Thread(target=work, daemon=True).start()
            return
        self.update_btn.setText('更新中…')
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.finished.connect(self._update_done)
        self._proc.start(sys.executable.replace('pythonw.exe', 'python.exe'),
                         ['-m', 'pip', 'install', '-U', '--disable-pip-version-check',
                          *(['--pre'] if self.s.core_channel == 'nightly' else []), 'yt-dlp[default,curl-cffi]'])

    def _update_done(self, code, _status):
        out = bytes(self._proc.readAll()).decode('utf-8', 'replace')
        self.update_btn.setEnabled(True)
        self.update_btn.setText('检查并更新')
        if code == 0:
            ver = next((line.split('yt-dlp-')[-1].split()[0] for line in out.splitlines()
                        if 'Successfully installed' in line and 'yt-dlp-' in line), None)
            msg = f'已更新到 yt-dlp {ver}，重启软件后生效。' if ver else '内核已是最新版本。'
            self.core_desc.setText(msg)
            if ver:
                self.win.notice.show_notice(f'解析内核已更新到 {ver}，重启后生效', '立即重启', self.win._restart)
                self.win._place_toast()
        else:
            self.core_desc.setText('更新失败：' + (out.strip().splitlines() or ['未知错误'])[-1][:200])

    def _frozen_update_done(self, ok, old, new, updated, error):
        self.update_btn.setEnabled(True)
        self.update_btn.setText('检查并更新')
        if not ok:
            self.core_desc.setText(f'更新失败：{error}' + ('' if '新版软件' in error else '（请检查网络或代理设置）'))
        elif updated:
            self.win._on_core_ready(new)        # 窗口底部出现「重启后生效 · 立即重启」
        else:
            self.core_desc.setText(f'已经是最新版本（{new}）。')

    # YouTube 组件 ----------------------------------------------------
    def _refresh_deno(self):
        from . import core, runtime_update
        if not hasattr(self, 'deno_desc'):
            return
        ver = runtime_update.installed_version()
        others = [n for n in core._find_js_runtimes(verify=False) if not (n == 'deno' and ver)]
        if ver:
            self.deno_desc.title.setText(f'YouTube 组件：Deno {ver}')
            self.deno_desc.set_tip('YouTube 的验证脚本要靠它运行。每周自动检查一次新版本')
            self.deno_btn.setText('检查更新')
        elif others:
            self.deno_desc.title.setText(f'YouTube 组件：使用电脑上已有的 {" / ".join(others)}')
            self.deno_desc.set_tip('电脑上已经装了 JS 运行时，不需要另外下载')
            self.deno_btn.setText('下载 Deno')
        else:
            self.deno_desc.title.setText('YouTube 组件：还没有下载')
            self.deno_desc.set_tip('第一次解析 YouTube 链接时会自动下载（约 40MB，只需一次）；也可以现在就下载')
            self.deno_btn.setText('现在下载')
        self.deno_desc.set_status('')

    def _deno_clicked(self):
        from . import runtime_update
        self.deno_btn.setEnabled(False)
        self.deno_btn.setText('请稍候…')
        proxy = (self.s.proxy or '').strip()
        have = bool(runtime_update.installed_version())

        def work():
            try:
                if have:
                    ver = runtime_update.check_update(proxy, force=True)
                    self._sig.deno_done.emit(f'已更新到 Deno {ver}' if ver else '已经是最新版本', '')
                else:
                    ver = runtime_update.install(lambda p: self._sig.deno_progress.emit(p), proxy)
                    self._sig.deno_done.emit(f'Deno {ver} 已下载', '')
            except Exception as e:  # noqa: BLE001
                self._sig.deno_done.emit('', str(e) or type(e).__name__)
        threading.Thread(target=work, daemon=True).start()

    def _deno_done(self, msg, error):
        self.deno_btn.setEnabled(True)
        self._refresh_deno()
        if error:
            self.deno_desc.set_status(f'下载失败：{error[:160]}（请检查网络或代理设置）', error=True)
        else:
            self.win.notify(msg, 'ok')

    # ------------------------------------------------------------ 谷歌云盘
    def _page_cloud(self, lay):
        self._section(lay, '账号', first=True)
        self.cloud_key_btn = _btn('', self._cloud_import)
        self.cloud_key_row = self._row(lay, '密钥文件', '', self.cloud_key_btn)
        self.cloud_btn = _btn('', self._cloud_login_clicked)
        self.cloud_row = self._row(lay, '谷歌账号', '', self.cloud_btn)
        self._cloud_render()
        self._section(lay, '上传')
        self._row(lay, '怎么上传', '打开输入框下方的「云盘」开关，之后的下载完成后会自动上传；\n'
                  '已经下好的视频、网盘文件，右键选「上传到谷歌云盘」。进度显示在对应的下载任务上。')
        self._row(lay, '上传后保留本地文件', '关闭后，上传成功的文件会移到回收站（可以从回收站恢复）',
                  self._toggle('cloud_keep_local'), tip=False)
        self._row(lay, '云盘里的位置', '链接解析下载器 / 视频分区 / 平台 / 年-月\n'
                  '链接解析下载器 / 网盘分区 / 网盘名 / 年-月\n认不出来源平台的网站放在「其他网站」',
                  self._cloud_open_btn)
        self._section(lay, '帮助')
        self._row(lay, '使用教程与常见问题', '第一次用要先在 Google Cloud 创建一个自己的密钥（免费，约 10 分钟，只需做一次）',
                  _btn('查看教程', self._cloud_help, 'primary'))
        self._cloud_render()

    def _cloud_help(self):
        from .cloud_help import show_help
        show_help(self.win, self.win.notify)

    def _cloud_import(self):
        path, _ = QFileDialog.getOpenFileName(self.win, '选择在 Google Cloud 下载的密钥文件（client_secret_….json）',
                                              os.path.expanduser('~/Downloads'), 'JSON (*.json)')
        if not path:
            return
        err = self.win.cloud_import_client(path)
        if err:
            ui.alert(self.win, '密钥文件没有导入', err)
            return
        self._cloud_render()
        self.win.notify('已导入密钥文件，接下来点「连接」登录谷歌账号', 'ok')

    def _cloud_render(self):
        from . import gdrive
        has_key = gdrive.available()
        self.cloud_key_row.title.setText('密钥文件：已导入' if has_key else '密钥文件：未导入')
        self.cloud_key_row.set_tip('想换一个 Google Cloud 项目的密钥，可以重新导入（需要重新连接账号）' if has_key else
                                   '先按下面的「使用教程」在 Google Cloud 创建「桌面应用」密钥，下载 JSON 文件后在这里导入')
        self.cloud_key_btn.setText('重新导入…' if has_key else '导入密钥文件…')
        if not hasattr(self, '_cloud_open_btn'):
            self._cloud_open_btn = _btn('在浏览器中打开', lambda: self.win.cloud_open_root())
        auth = self.win.cloud_drive.auth if self.win.cloud_drive else None
        self._cloud_open_btn.setEnabled(bool(auth and auth.logged_in))
        if auth and auth.logged_in:
            self.cloud_row.title.setText(f'已连接：{auth.email or "谷歌账号"}')
            self.cloud_row.set_tip('')
            self.cloud_row.set_status('软件只能看到、管理它自己上传的文件，碰不到你云盘里的其它内容')      # 隐私说明：一直显示
            self.cloud_btn.setText('断开')
        else:
            self.cloud_row.title.setText('谷歌账号：未连接')
            self.cloud_row.set_status('')
            self.cloud_row.set_tip('点「连接」会打开浏览器，登录谷歌账号并允许访问即可' if auth else '导入密钥文件后才能连接')
            self.cloud_btn.setText('连接')
        self.cloud_btn.setEnabled(bool(auth))

    def _cloud_login_clicked(self):
        auth = self.win.cloud_drive.auth
        if auth.logged_in:
            if not ui.confirm(self.win, '断开谷歌账号？', '已经上传的文件不受影响；没传完的会暂停，重新连接后继续。',
                              ok='断开', danger=True):
                return
            self.win.cloud_logout()
            self._cloud_render()
            return
        self.cloud_btn.setEnabled(False)
        self.cloud_btn.setText('等待浏览器授权…')

        def done(email, err):
            self._cloud_render()
            if err:
                ui.alert(self.win, '谷歌云盘没有连接成功', str(err))
            else:
                self.win.notify(f'已连接谷歌云盘：{email}', 'ok')
        self.win.cloud_login(done)

    # ------------------------------------------------------------ 关于
    def _page_about(self, lay):
        lay.addSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(16)
        from .gui import app_icon_path
        ic = QLabel()
        if app_icon_path():
            ic.setPixmap(QIcon(app_icon_path()).pixmap(64, 64))
        top.addWidget(ic)
        tv = QVBoxLayout()
        tv.setSpacing(4)
        tv.addWidget(_label(APP_NAME, 'pageTitle'))
        self.about_ver = _label('', 'meta')
        tv.addWidget(self.about_ver)
        top.addLayout(tv, 1)
        lay.addLayout(top)
        lay.addSpacing(20)
        text = _label('粘贴分享链接就能下载：视频支持 抖音、快手、B站、YouTube、X、TikTok 等上千个网站；'
                      '网盘支持 百度网盘、夸克网盘、123云盘、Mega 的分享。\n\n'
                      '这个软件的代码几乎全部由 Claude 编写。图标「星芒落点」和界面风格都是向 Claude 应用致敬的原创设计，'
                      '不是 Anthropic 的官方作品，本项目与 Anthropic 没有任何官方关系。', 'rowDesc', wrap=True)
        text.setStyleSheet('font-size: 13px;')
        lay.addWidget(text)
        self._section(lay, '致谢')
        for name, desc in (('yt-dlp', '视频解析核心（Unlicense）'), ('FFmpeg', '音视频合并与转换（GPL）'),
                           ('PanDL 网盘下载器', '网盘解析与下载核心（同一作者的项目，Go 编写，已合并进本软件）'),
                           ('Qt / PySide6', '界面框架（LGPL）'),
                           ('Hanken Grotesk、Source Serif 4', '界面英文字体，截取拉丁字母后改名 VDSans / VDSerif'
                                                              '（SIL Open Font License 1.1）'),
                           ('各平台 logo', 'B站、X、YouTube、抖音、快手、百度网盘、夸克网盘、123云盘、Mega 的 logo '
                                          '是各自的商标，只用来标识对应的分区')):
            self._row(lay, name, desc)
        self._section(lay, '软件更新')
        self.app_check_btn = _btn('检查更新', self._check_app)
        self.app_update_row = self._row(lay, f'当前版本 {APP_VERSION}', '新版本发布在 GitHub。用安装包装的可以在软件里直接下载并安装；'
                                        '便携版下载新的压缩包解压覆盖。设置和历史记录都会保留',
                                        self.app_check_btn)
        self._row(lay, '启动时检查软件新版本', '有新版本时在侧边栏左下角提示，可以选择跳过某个版本', self._toggle('check_app_update'))
        self._section(lay, '数据')
        box = QHBoxLayout()
        box.setSpacing(6)
        box.addWidget(_btn('打开', lambda: os.startfile(DATA)))
        box.addWidget(_btn('导入旧版数据…', self._import_legacy))
        self._row(lay, '数据文件夹', '设置、历史记录、登录信息、头像和日志都在这里，卸载或升级软件不会丢：', box, status=DATA)
        lay.addSpacing(18)
        h = QHBoxLayout()
        h.setSpacing(8)
        from .gui import GITHUB_URL
        h.addWidget(_btn('GitHub 项目主页', lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)), 'primary'))
        h.addWidget(_btn('打开程序文件夹', lambda: os.startfile(ROOT)))
        h.addStretch(1)
        lay.addLayout(h)

    def _check_app(self):
        from . import app_update
        self._app_rel = getattr(self, '_app_rel', None)
        if self._app_rel:           # 已经查到新版：按钮变成「查看更新」
            self.win.show_update(self._app_rel)
            return
        self.app_check_btn.setEnabled(False)
        self.app_check_btn.setText('检查中…')
        self.app_update_row.set_status('')
        proxy = (self.s.proxy or '').strip()

        def work():
            try:
                self._sig.app_done.emit(app_update.check('windows', proxy=proxy), '')
            except Exception as e:  # noqa: BLE001
                self._sig.app_done.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _app_check_done(self, rel, error):
        self.app_check_btn.setEnabled(True)
        if error:
            self.app_check_btn.setText('检查更新')
            self.app_update_row.set_status(f'检查失败：{error}（GitHub 在国内有时连不上，可以稍后再试或开代理）', error=True)
        elif rel:
            self._app_rel = rel
            self.app_check_btn.setText('查看更新')
            self.app_update_row.title.setText(f'有新版本 {rel["version"]}（当前 {APP_VERSION}）')
            self.win.show_update(rel)
        else:
            self.app_check_btn.setText('检查更新')
            self.app_update_row.title.setText(f'当前版本 {APP_VERSION} · 已经是最新版')

    def _import_legacy(self):
        """2.1 之前的版本把数据放在程序文件夹里：选那个文件夹，把设置、历史、登录信息、头像搬过来。"""
        src = QFileDialog.getExistingDirectory(self.win, '选择旧版「链接解析下载器 / 视频解析下载器」所在的文件夹')
        if not src:
            return
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(DATA)):
            return
        if not has_legacy_data(src):
            ui.alert(self.win, '没有找到旧版的数据', '这个文件夹里没有 config.json / history.db。\n请选择旧版软件 exe 所在的那个文件夹。')
            return
        if not ui.confirm(self.win, '用旧版的数据替换现在的？', '会换成这个文件夹里的设置、历史记录、网站登录信息和头像。\n'
                          '旧文件夹里的文件不会动。导入后需要重新打开软件。', ok='导入', danger=True):
            return
        try:
            got = import_legacy(src, dest=PENDING)
        except OSError as e:
            ui.alert(self.win, '导入失败', str(e))
            return
        if ui.confirm(self.win, '已准备好，现在退出软件吗？', f'导入了 {len(got)} 项，重新打开软件后生效。', ok='退出软件', cancel='稍后'):
            self.win._quit()


def ui_scroll(content):
    from .gui import scroll_area
    return scroll_area(content)


class _UpdateSignals(QObject):
    progress = Signal(float)
    done = Signal(bool, str, str, bool, str)
    app_done = Signal(object, str)         # 软件新版本检查：(新版本信息或 None, 错误)
    deno_progress = Signal(float)
    deno_done = Signal(str, str)           # YouTube 组件：(结果, 错误)
