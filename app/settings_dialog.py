"""设置面板：盖在主窗口上的模态面板（像 Claude 应用的设置），左边分类、右边一行行的选项，改了立即生效。"""
import datetime
import os
import sys
import threading

from PySide6.QtCore import QObject, QProcess, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout,
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
        for key, name, icon in PAGES:
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
        for key, _, _ in PAGES:
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
        self._refresh_core_version()
        self.show_page('general')

    # ------------------------------------------------------------ 框架
    def open_at(self, page=None):
        self.refresh_account()
        self.refresh_avatar()
        self.refresh_pan_accounts()
        self.refresh_pan_settings()
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

    def _row(self, lay, title, desc, control=None, last=False):
        w = _clear()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 13, 0, 13)
        h.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(3)
        t = _label(title, 'rowLabel')
        left.addWidget(t)
        d = _label(desc, 'rowDesc', wrap=True)
        if not desc:
            d.hide()     # 只能隐藏：还没放进窗口的控件调用 show/setVisible(True) 会变成一个独立的小窗口闪一下
        d.title = t
        left.addWidget(d)
        h.addLayout(left, 1)
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
        if QMessageBox.question(self.win, '移除头像', '移除后左下角恢复显示名字首字母。') == QMessageBox.Yes:
            avatar.remove()
            self.win.setting_changed('avatar')

    def _set_hotkey(self):
        text = self.hotkey_edit.text().strip()
        ok = not text or hotkey.parse(text) is not None
        self.hotkey_desc.setText('在任何地方复制视频链接后按下，软件会弹出来并开始解析；留空 = 不使用' if ok
                                 else '格式不对：需要 Ctrl / Alt / Win + 字母、数字或 F1~F12，例如 Ctrl+Alt+D')
        self.hotkey_desc.setObjectName('rowDesc' if ok else 'metaError')
        self.hotkey_desc.style().unpolish(self.hotkey_desc)
        self.hotkey_desc.style().polish(self.hotkey_desc)
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
        self.dir_desc = self._row(lay, '保存位置', self.s.download_dir, box)
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
        self._row(lay, '同时保存封面', '下载视频时另存一张与视频同名的 JPG（也可以在输入框下方的「封面」开关切换）',
                  self._toggle('save_cover'))
        self._row(lay, '把封面写进视频文件', '资源管理器缩略图、播放器里直接显示封面', self._toggle('embed_cover'))
        self._section(lay, '网盘下载')
        self.pan_spins = {}
        self._row(lay, '同时下载的文件数', '网盘分享里的文件同时下载几个', self._pan_spin('maxConcurrent', 1, 20))
        self._row(lay, '每个文件的连接数', '多条连接一起下，能跑满带宽（百度网盘非会员会自动只用 1 条，避免被限速）',
                  self._pan_spin('connsPerTask', 1, 64))
        self._row(lay, '限速', '所有网盘下载加起来的速度上限；0 = 不限速', self._pan_spin('speedLimitKB', 0, 1_000_000, 130, ' KB/s'))
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
        proxy.setPlaceholderText('留空 = 跟随系统代理')
        proxy.setFixedWidth(300)

        def set_proxy():
            if proxy.text().strip() != self.s.proxy:
                self.s.proxy = proxy.text().strip()
                self.win.setting_changed('proxy')
        proxy.editingFinished.connect(set_proxy)
        self._row(lay, '代理', '例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080（网盘下载重启软件后生效）', proxy)
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
        self._section(lay, '其它网站的登录信息（Cookies）')
        self.cookie_hint = self._row(lay, 'cookies 文件夹', '', _btn('打开文件夹', self._open_cookie_dir))
        mode = self._combo('cookie_mode', COOKIE_MODES.items(), 220)
        mode.currentIndexChanged.connect(self._sync_cookie_widgets)
        self.cookie_mode = mode
        self._row(lay, '额外的 cookies 来源', '一般不需要：把 cookies.txt 放进上面的文件夹就够了', mode)
        self.browser = Combo()
        self.browser.addItems(BROWSERS)
        if self.s.cookie_browser in BROWSERS:
            self.browser.setCurrentText(self.s.cookie_browser)
        self.browser.setFixedWidth(220)

        def set_browser():
            self.s.cookie_browser = self.browser.currentText()
            self.win.setting_changed('cookie_browser')
        self.browser.currentIndexChanged.connect(set_browser)
        self._row(lay, '从哪个浏览器读取', '新版 Chrome / Edge 的 cookies 读不出来，推荐 Firefox', self.browser)
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
            self.bili_desc.setText(f'已登录：{info.get("name") or "（网络不通，稍后显示）"} · {vip}{until}')
            self.bili_desc.show()
            self.bili_box.addWidget(_btn('重新扫码', lambda: self.win._bili_login(True)))
            self.bili_box.addWidget(_btn('退出登录', self.win._bili_logout, 'danger'))
        else:
            self.bili_desc.setText(('登录已失效，请重新扫码。' if info and info.get('expired') else '')
                                   + '登录后解锁 1080P 高码率 / 4K 等大会员画质。长效登录约 180 天，网页登录约 7 天。'
                                   r'登录信息只保存在本机（%APPDATA%\VideoDownloader）')
            self.bili_desc.show()
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
                    desc.setText('已登录：' + ' · '.join(b for b in bits if b))
                    box.addWidget(_btn('重新扫码', lambda p=prov: self._pan_login(p)))
                    box.addWidget(_btn('退出登录', lambda p=prov: self._pan_logout(p), 'danger'))
                else:
                    desc.setText((a.get('note') or '') + '。登录信息加密保存在本机（只有当前 Windows 用户能解开）')
                    box.addWidget(_btn('扫码登录', lambda p=prov: self._pan_login(p), 'primary'))
                desc.show()
        eng.call('accounts', None, done)

    def _pan_login(self, provider):
        if self.win.pan_login(provider):
            self.refresh_pan_accounts()

    def _pan_logout(self, provider):
        name = pan.PROVIDERS.get(provider, provider)
        if QMessageBox.question(self.win, f'退出{name}', f'退出{name}登录？本机保存的登录信息会被删除。') != QMessageBox.Yes:
            return
        self.win.pan.call('logout', {'provider': provider}, lambda r, e: self.refresh_pan_accounts())

    def _refresh_cookie_hint(self):
        from . import core
        found = core.cookie_file_names()
        self.cookie_hint.setText('TikTok、Vimeo 等需要登录的网站：用浏览器扩展「Get cookies.txt LOCALLY」在对应网站导出 '
                                 '.txt，放进 cookies 文件夹（点右边的按钮打开），自动生效。\n'
                                 f'当前已生效：{"、".join(found) if found else "（空）"}')
        self.cookie_hint.show()

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
        self._row(lay, '启动时检查新版本', '有新版本时在窗口底部提示', self._toggle('check_core_update'))
        if frozen and os.path.isdir(core_update.OVERRIDE_DIR):
            self._row(lay, '恢复为内置版本', '在线更新后反而出问题时使用，重启后生效', _btn('恢复', self._reset_core))

    def _refresh_core_version(self):
        ver = core_update.current_version(block=False)
        updated = getattr(sys, 'frozen', False) and os.path.isdir(core_update.OVERRIDE_DIR)
        self.core_desc.title.setText(f'当前版本 {ver}' + ('（已在线更新）' if updated else ''))
        self.about_ver.setText(f'版本 {APP_VERSION} · 解析内核 yt-dlp {ver}')

    def _reset_core(self):
        core_update.reset_frozen()
        QMessageBox.information(self, '解析内核', '已恢复为内置版本，重启软件后生效。')

    def _update_core(self):
        self.update_btn.setEnabled(False)
        if getattr(sys, 'frozen', False):
            self.update_btn.setText('检查中…')

            def work():
                try:
                    old, new, updated = core_update.update_frozen(self._sig.progress.emit)
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
                         ['-m', 'pip', 'install', '-U', '--disable-pip-version-check', 'yt-dlp[default,curl-cffi]'])

    def _update_done(self, code, _status):
        out = bytes(self._proc.readAll()).decode('utf-8', 'replace')
        self.update_btn.setEnabled(True)
        self.update_btn.setText('检查并更新')
        if code == 0:
            ver = next((line.split('yt-dlp-')[-1].split()[0] for line in out.splitlines()
                        if 'Successfully installed' in line and 'yt-dlp-' in line), None)
            msg = f'已更新到 yt-dlp {ver}，重启软件后生效。' if ver else '内核已是最新版本。'
            self.core_desc.setText(msg)
        else:
            QMessageBox.warning(self, '更新内核', '更新失败：\n' + out[-800:])

    def _frozen_update_done(self, ok, old, new, updated, error):
        self.update_btn.setEnabled(True)
        self.update_btn.setText('检查并更新')
        if not ok:
            QMessageBox.warning(self, '解析内核', f'更新失败：{error}\n（请检查网络或代理设置）')
        elif updated:
            self.core_desc.setText(f'已更新：{old} → {new}，重启软件后生效。')
        else:
            self.core_desc.setText(f'已经是最新版本（{new}）。')

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
        self._section(lay, '数据')
        box = QHBoxLayout()
        box.setSpacing(6)
        box.addWidget(_btn('打开', lambda: os.startfile(DATA)))
        box.addWidget(_btn('导入旧版数据…', self._import_legacy))
        self._row(lay, '数据文件夹', f'设置、历史记录、登录信息、头像和日志都在这里，卸载或升级软件不会丢：\n{DATA}', box)
        lay.addSpacing(18)
        h = QHBoxLayout()
        h.setSpacing(8)
        from .gui import GITHUB_URL
        h.addWidget(_btn('GitHub 项目主页', lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)), 'primary'))
        h.addWidget(_btn('打开程序文件夹', lambda: os.startfile(ROOT)))
        h.addStretch(1)
        lay.addLayout(h)

    def _import_legacy(self):
        """2.1 之前的版本把数据放在程序文件夹里：选那个文件夹，把设置、历史、登录信息、头像搬过来。"""
        src = QFileDialog.getExistingDirectory(self.win, '选择旧版「链接解析下载器 / 视频解析下载器」所在的文件夹')
        if not src:
            return
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(DATA)):
            return
        if not has_legacy_data(src):
            QMessageBox.information(self.win, '导入旧版数据', '这个文件夹里没有找到旧版的数据（config.json / history.db）。\n'
                                                       '请选择旧版软件 exe 所在的那个文件夹。')
            return
        if QMessageBox.question(self.win, '导入旧版数据',
                                '用这个文件夹里的设置、历史记录、网站登录信息和头像，替换现在的？\n'
                                '（旧文件夹里的文件不会动。导入后需要重新打开软件。）') != QMessageBox.Yes:
            return
        try:
            got = import_legacy(src, dest=PENDING)
        except OSError as e:
            QMessageBox.warning(self.win, '导入旧版数据', f'导入失败：{e}')
            return
        if QMessageBox.question(self.win, '导入旧版数据',
                                f'已准备好（{len(got)} 项），重新打开软件后生效。现在退出软件吗？') == QMessageBox.Yes:
            self.win._quit()


def ui_scroll(content):
    from .gui import scroll_area
    return scroll_area(content)


class _UpdateSignals(QObject):
    progress = Signal(float)
    done = Signal(bool, str, str, bool, str)
