import os
import sys
import threading

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from . import core_update, hotkey
from .config import BROWSERS, CODEC_PREFERENCES, COOKIE_MODES, FILENAME_TEMPLATES, SUB_LANG_MODES, SUBFOLDER_MODES


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle('设置')
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        pages = []
        for name in ('下载', '字幕与提醒', '网络与登录', '内核'):
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(4, 4, 4, 4)
            pages.append(v)
            tabs.addTab(page, name)
        layout.addWidget(tabs)

        # 下载
        box = QGroupBox('下载')
        form = QFormLayout(box)
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 10)
        self.concurrent.setValue(settings.max_concurrent)
        form.addRow('同时下载任务数', self.concurrent)
        self.fragments = QSpinBox()
        self.fragments.setRange(1, 16)
        self.fragments.setValue(settings.fragment_threads)
        self.fragments.setToolTip('分片格式（HLS/DASH，如 X、部分 YouTube 格式）每个任务的并发连接数')
        form.addRow('分片下载线程', self.fragments)
        self.template = QComboBox()
        for key, (name, _) in FILENAME_TEMPLATES.items():
            self.template.addItem(name, key)
        self.template.setCurrentIndex(max(0, self.template.findData(settings.filename_template)))
        form.addRow('文件名格式', self.template)
        self.codec = QComboBox()
        for key, name in CODEC_PREFERENCES.items():
            self.codec.addItem(name, key)
        self.codec.setCurrentIndex(max(0, self.codec.findData(settings.codec_preference)))
        self.codec.setToolTip('同一清晰度有多种编码时优先选哪种（YouTube、B站常见）')
        form.addRow('编码偏好', self.codec)
        self.subfolder = QComboBox()
        for key, name in SUBFOLDER_MODES.items():
            self.subfolder.addItem(name, key)
        self.subfolder.setCurrentIndex(max(0, self.subfolder.findData(settings.subfolder)))
        form.addRow('自动分文件夹', self.subfolder)
        self.clipboard = QCheckBox('复制链接后切回本窗口时，提示是否解析剪贴板里的新链接')
        self.clipboard.setChecked(settings.auto_parse_clipboard)
        form.addRow('', self.clipboard)
        self.save_cover = QCheckBox('下载视频时同时保存封面（与视频同名的 JPG）')
        self.save_cover.setChecked(settings.save_cover)
        form.addRow('', self.save_cover)
        self.embed_cover = QCheckBox('把封面写进视频文件（资源管理器缩略图、播放器里直接显示封面）')
        self.embed_cover.setChecked(settings.embed_cover)
        form.addRow('', self.embed_cover)
        pages[0].addWidget(box)

        # 字幕与弹幕
        box = QGroupBox('字幕与弹幕')
        form = QFormLayout(box)
        self.save_dm = QCheckBox('B站视频同时下载弹幕（转成与视频同名的 .danmaku.ass）')
        self.save_dm.setChecked(settings.save_danmaku)
        self.save_subs = QCheckBox('同时下载字幕（SRT，与视频同名，播放器自动加载）')
        self.save_subs.setChecked(settings.save_subs)
        self.sub_langs = QComboBox()
        for key, name in SUB_LANG_MODES.items():
            self.sub_langs.addItem(name, key)
        self.sub_langs.setCurrentIndex(max(0, self.sub_langs.findData(settings.sub_langs)))
        form.addRow('', self.save_dm)
        burn_row = QWidget()
        bh = QHBoxLayout(burn_row)
        bh.setContentsMargins(0, 0, 0, 0)
        self.burn_dm = QCheckBox('同时生成「弹幕版」视频（弹幕画进视频并提高帧率，任何播放器都流畅）')
        self.burn_dm.setChecked(settings.burn_danmaku)
        self.dm_fps = QComboBox()
        self.dm_fps.addItem('60 帧', 60)
        self.dm_fps.addItem('120 帧（高刷屏更顺，文件更大）', 120)
        self.dm_fps.setCurrentIndex(1 if settings.danmaku_fps == 120 else 0)
        bh.addWidget(self.burn_dm)
        bh.addWidget(self.dm_fps)
        bh.addStretch(1)
        form.addRow('', burn_row)
        self.keep_orig = QCheckBox('生成弹幕版后仍保留原视频（不带弹幕）和外挂弹幕文件')
        self.keep_orig.setChecked(settings.keep_original)
        self.keep_orig.setToolTip('默认不保留，只留流畅的弹幕版，省空间')
        form.addRow('', self.keep_orig)
        form.addRow('', self.save_subs)
        form.addRow('字幕语言', self.sub_langs)
        tip = QLabel('外挂弹幕（.danmaku.ass）只能跟着视频帧刷新，30 帧视频里看起来会一跳一跳；'
                     '「弹幕版」把弹幕按高帧率画进视频，和网页版一样顺滑（原视频保留）。'
                     '字幕 .srt 会被 PotPlayer / VLC 等播放器自动加载。')
        tip.setWordWrap(True)
        tip.setStyleSheet('color: #666;')
        form.addRow('', tip)
        pages[1].addWidget(box)

        # 完成提醒
        box = QGroupBox('下载完成后')
        form = QFormLayout(box)
        self.notify_done = QCheckBox('弹出系统通知（窗口不在前台时）')
        self.notify_done.setChecked(settings.notify_done)
        self.notify_sound = QCheckBox('全部完成时播放提示音')
        self.notify_sound.setChecked(settings.notify_sound)
        self.open_dir = QCheckBox('全部完成时自动打开下载文件夹')
        self.open_dir.setChecked(settings.open_dir_when_done)
        for w in (self.notify_done, self.notify_sound, self.open_dir):
            form.addRow('', w)
        pages[1].addWidget(box)

        # 网络
        box = QGroupBox('网络')
        form = QFormLayout(box)
        self.proxy = QLineEdit(settings.proxy)
        self.proxy.setPlaceholderText('留空 = 跟随系统代理；例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080')
        form.addRow('代理', self.proxy)
        pages[2].addWidget(box)

        # Cookies
        box = QGroupBox('Cookies（登录信息）')
        v = QVBoxLayout(box)
        from . import core
        found = core.cookie_file_names()
        tip = QLabel('最简单的用法：用浏览器扩展“Get cookies.txt LOCALLY”在对应网站点 Export，'
                     '把导出的 .txt 放进程序目录的 cookies 文件夹，自动生效，无需在这里设置。'
                     'B站用主界面右上角「B站登录」扫码即可（登录会自动续期）。\n'
                     f'当前 cookies 文件夹里已生效：{"、".join(found) if found else "（空）"}')
        tip.setWordWrap(True)
        tip.setStyleSheet('color: #666;')
        v.addWidget(tip)
        form = QFormLayout()
        self.cookie_mode = QComboBox()
        for key, name in COOKIE_MODES.items():
            self.cookie_mode.addItem(name, key)
        self.cookie_mode.setCurrentIndex(max(0, self.cookie_mode.findData(settings.cookie_mode)))
        form.addRow('来源', self.cookie_mode)
        self.browser = QComboBox()
        self.browser.addItems(BROWSERS)
        if settings.cookie_browser in BROWSERS:
            self.browser.setCurrentText(settings.cookie_browser)
        form.addRow('浏览器', self.browser)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.cookie_file = QLineEdit(settings.cookie_file)
        self.cookie_file.setPlaceholderText('Netscape 格式的 cookies.txt')
        browse = QPushButton('浏览…')
        browse.clicked.connect(self._pick_cookie_file)
        h.addWidget(self.cookie_file)
        h.addWidget(browse)
        form.addRow('cookies 文件', row)
        self._cookie_file_row = row
        v.addLayout(form)
        pages[2].addWidget(box)
        self.cookie_mode.currentIndexChanged.connect(self._sync_cookie_widgets)
        self._sync_cookie_widgets()

        # 后台运行（放在「下载」页）
        box = QGroupBox('后台运行与快捷键')
        form = QFormLayout(box)
        self.to_tray = QCheckBox('关闭窗口时缩小到托盘（下载继续；右键托盘图标「退出」才真正退出）')
        self.to_tray.setChecked(settings.close_to_tray)
        form.addRow('', self.to_tray)
        row = QWidget()
        hh = QHBoxLayout(row)
        hh.setContentsMargins(0, 0, 0, 0)
        self.hotkey = QLineEdit(settings.hotkey)
        self.hotkey.setPlaceholderText('例如 Ctrl+Alt+D，留空 = 不使用')
        self.hotkey.setMaximumWidth(200)
        self.hotkey_tip = QLabel()
        self.hotkey_tip.setObjectName('hint')
        self.hotkey.textChanged.connect(self._check_hotkey)
        hh.addWidget(self.hotkey)
        hh.addWidget(self.hotkey_tip, 1)
        form.addRow('全局快捷键', row)
        tip = QLabel('在任何地方复制视频链接后按这个快捷键，软件会弹出来并开始解析。')
        tip.setObjectName('hint')
        form.addRow('', tip)
        pages[0].addWidget(box)
        self._check_hotkey()

        # 内核
        box = QGroupBox('解析内核（yt-dlp）')
        v = QVBoxLayout(box)
        h = QHBoxLayout()
        self.core_label = QLabel(f'当前版本：{core_update.current_version()}'
                                 + ('（已在线更新）' if os.path.isdir(core_update.OVERRIDE_DIR) and
                                    getattr(sys, 'frozen', False) else ''))
        h.addWidget(self.core_label)
        h.addStretch(1)
        self.update_btn = QPushButton('检查并更新')
        self.update_btn.setObjectName('accent')
        self.update_btn.setToolTip('网站改版导致解析失败时，更新 yt-dlp 通常就能恢复')
        self.update_btn.clicked.connect(self._update_core)
        h.addWidget(self.update_btn)
        v.addLayout(h)
        self.core_status = QLabel('网站改版导致解析失败时，更新内核通常就能恢复。更新后重启软件生效。')
        self.core_status.setObjectName('hint')
        self.core_status.setWordWrap(True)
        v.addWidget(self.core_status)
        self.auto_check = QCheckBox('启动时检查内核是否有新版本')
        self.auto_check.setChecked(settings.check_core_update)
        v.addWidget(self.auto_check)
        if getattr(sys, 'frozen', False) and os.path.isdir(core_update.OVERRIDE_DIR):
            reset = QPushButton('恢复为打包时内置的版本')
            reset.setObjectName('small')
            reset.clicked.connect(self._reset_core)
            v.addWidget(reset)
        pages[3].addWidget(box)
        self._sig = _UpdateSignals()
        self._sig.progress.connect(lambda p: self.update_btn.setText(f'下载中 {p * 100:.0f}%'))
        self._sig.done.connect(self._frozen_update_done)

        for v in pages:
            v.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('保存')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._proc = None

    def _sync_cookie_widgets(self):
        mode = self.cookie_mode.currentData()
        self.browser.setEnabled(mode == 'browser')
        self._cookie_file_row.setEnabled(mode == 'file')

    def _pick_cookie_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 cookies.txt', self.cookie_file.text(),
                                              'Cookies (*.txt);;所有文件 (*)')
        if path:
            self.cookie_file.setText(path)

    def _check_hotkey(self):
        text = self.hotkey.text().strip()
        ok = not text or hotkey.parse(text) is not None
        self.hotkey_tip.setText('' if ok else '格式不对：需要 Ctrl / Alt / Win + 字母、数字或 F1~F12')
        self.hotkey_tip.setObjectName('hint' if ok else 'metaError')
        self.hotkey_tip.style().unpolish(self.hotkey_tip)
        self.hotkey_tip.style().polish(self.hotkey_tip)
        return ok

    def _reset_core(self):
        core_update.reset_frozen()
        QMessageBox.information(self, '解析内核', '已恢复为内置版本，重启软件后生效。')

    def _update_core(self):
        if getattr(sys, 'frozen', False):
            self.update_btn.setEnabled(False)
            self.update_btn.setText('检查中…')

            def work():
                try:
                    old, new, updated = core_update.update_frozen(self._sig.progress.emit)
                    self._sig.done.emit(True, old, new, updated, '')
                except Exception as e:  # noqa: BLE001
                    self._sig.done.emit(False, '', '', False, str(e))
            threading.Thread(target=work, daemon=True).start()
            return
        self.update_btn.setEnabled(False)
        self.update_btn.setText('更新中…')
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.finished.connect(self._update_done)
        self._proc.start(sys.executable.replace('pythonw.exe', 'python.exe'),
                         ['-m', 'pip', 'install', '-U', '--disable-pip-version-check',
                          'yt-dlp[default,curl-cffi]'])

    def _update_done(self, code, _status):
        out = bytes(self._proc.readAll()).decode('utf-8', 'replace')
        self.update_btn.setEnabled(True)
        self.update_btn.setText('检查并更新')
        if code == 0:
            ver = next((line.split('yt-dlp-')[-1].split()[0] for line in out.splitlines()
                        if 'Successfully installed' in line and 'yt-dlp-' in line), None)
            msg = f'已更新到 yt-dlp {ver}，重启程序后生效。' if ver else '内核已是最新版本。'
            QMessageBox.information(self, '更新内核', msg)
        else:
            QMessageBox.warning(self, '更新内核', '更新失败：\n' + out[-800:])

    def _frozen_update_done(self, ok, old, new, updated, error):
        self.update_btn.setEnabled(True)
        self.update_btn.setText('检查并更新')
        if not ok:
            QMessageBox.warning(self, '解析内核', f'更新失败：{error}\n（请检查网络或代理设置）')
        elif updated:
            self.core_status.setText(f'已更新：{old} → {new}，重启软件后生效。')
            QMessageBox.information(self, '解析内核', f'已更新到 yt-dlp {new}（原来是 {old}）。\n重启软件后生效。')
        else:
            self.core_status.setText(f'已经是最新版本（{new}）。')

    def accept(self):
        s = self.settings
        if not self._check_hotkey():
            QMessageBox.warning(self, '设置', '全局快捷键格式不对，例如 Ctrl+Alt+D。')
            return
        s.close_to_tray = self.to_tray.isChecked()
        s.hotkey = self.hotkey.text().strip()
        s.check_core_update = self.auto_check.isChecked()
        s.max_concurrent = self.concurrent.value()
        s.fragment_threads = self.fragments.value()
        s.filename_template = self.template.currentData()
        s.codec_preference = self.codec.currentData()
        s.auto_parse_clipboard = self.clipboard.isChecked()
        s.save_cover = self.save_cover.isChecked()
        s.embed_cover = self.embed_cover.isChecked()
        s.save_danmaku = self.save_dm.isChecked()
        s.burn_danmaku = self.burn_dm.isChecked()
        s.danmaku_fps = self.dm_fps.currentData()
        s.keep_original = self.keep_orig.isChecked()
        s.save_subs = self.save_subs.isChecked()
        s.sub_langs = self.sub_langs.currentData()
        s.subfolder = self.subfolder.currentData()
        s.notify_done = self.notify_done.isChecked()
        s.notify_sound = self.notify_sound.isChecked()
        s.open_dir_when_done = self.open_dir.isChecked()
        s.proxy = self.proxy.text().strip()
        s.cookie_mode = self.cookie_mode.currentData()
        s.cookie_browser = self.browser.currentText()
        s.cookie_file = self.cookie_file.text().strip()
        if s.cookie_mode == 'file' and not s.cookie_file:
            QMessageBox.warning(self, '设置', '请选择 cookies.txt 文件，或把来源改为“不使用”。')
            return
        super().accept()


class _UpdateSignals(QObject):
    progress = Signal(float)
    done = Signal(bool, str, str, bool, str)
