"""谷歌云盘备份的界面部分（混入 MainWindow，和 PanMixin 一样）。

入口（都只在配置了谷歌云盘、并连接了账号时出现）：
- 输入框下的「云盘」开关：打开后，之后开始的下载（视频和网盘）下完会同时上传；
- 右键菜单「上传到谷歌云盘」：下载页的任务、已下载的视频、网盘批次——已经下好的文件随时补传；
- 上传进度显示在对应的任务 / 批次上（「云盘 45%」「已传到云盘」），全部传完弹一句提示。
上传任务按 group 归类：task:<任务ID> / batch:<网盘批次ID> / rec:<下载记录ID>。
"""
import json
import logging
import os
import threading
import time

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from . import core, gdrive, icons
from .theme import c

log = logging.getLogger(__name__)
_FLAGS_PATH = os.path.join(gdrive.DIR, 'batch_flags.json')     # 开着「云盘」开关时开始的网盘批次


class _CloudSignals(QObject):
    changed = Signal(object)          # 上传任务有变化（后台线程 → 界面线程）
    login_done = Signal(str, str)     # (邮箱, 错误)


class CloudMixin:
    # ------------------------------------------------------------ 初始化
    def _cloud_init(self):
        self.cloud = None
        self.cloud_drive = None
        self.chip_cloud = None
        self._cloud_sig = _CloudSignals()
        self._cloud_sig.changed.connect(self._cloud_on_change)
        self._cloud_flags = set()
        try:
            with open(_FLAGS_PATH, encoding='utf-8') as f:
                self._cloud_flags = set(json.load(f))
        except (OSError, ValueError):
            pass
        self._cloud_announced = set()
        self._cloud_rec_views = {}      # 下载记录 ID → [显示进度的函数]（侧边栏、已下载的视频列表）
        self._cloud_activate()

    def _cloud_activate(self):
        """导入过密钥文件（gdrive\\client.json）才启用上传队列"""
        if self.cloud is not None or not gdrive.available():
            return
        try:
            self.cloud_drive = gdrive.Drive(lambda: self.settings.proxy)
            self.cloud = gdrive.Uploader(self.cloud_drive, on_change=self._cloud_sig.changed.emit)
        except Exception as e:  # noqa: BLE001 - 云盘出问题不能影响软件本身
            log.warning('谷歌云盘初始化失败: %s', e)
            self.cloud = self.cloud_drive = None

    def cloud_import_client(self, path):
        """导入密钥文件。返回错误说明，成功返回空字符串。换了密钥要重新连接账号。"""
        err = gdrive.import_client(path)
        if err:
            return err
        if self.cloud_drive is not None:
            self.cloud_drive.auth.logout()
            self.cloud_drive.auth = gdrive.Auth(self.cloud_drive.session)
        self._cloud_activate()
        self._cloud_sync_chip()
        return ''

    @property
    def cloud_ready(self):
        return bool(self.cloud and self.cloud_drive.auth.logged_in)

    def _cloud_chip(self, Chip):
        """输入框下的「云盘」开关（连接了谷歌账号才显示）"""
        self.chip_cloud = Chip('云盘', 'cloud_up', checkable=True,
                               tip='下载完成后同时上传到谷歌云盘（已下载的文件可以右键「上传到谷歌云盘」）')
        self.chip_cloud.setChecked(self.settings.cloud_upload)
        self.chip_cloud.toggled.connect(lambda on: self._toggle_setting('cloud_upload', on))
        return self.chip_cloud

    def _cloud_sync_chip(self):
        if self.chip_cloud is not None:
            self.chip_cloud.setVisible(self.cloud_ready)

    def cloud_wanted(self):
        """现在开始的下载要不要同时上传"""
        return self.cloud_ready and self.settings.cloud_upload

    # ------------------------------------------------------------ 加入上传
    def _cloud_add(self, paths, folder_of, title, group):
        if not self.cloud_ready:
            self.notify('请先在 设置 → 谷歌云盘 里连接谷歌账号', 'error')
            return 0
        n = 0
        for p in paths:
            if p and os.path.isfile(p) and self.cloud.add(p, folder_of(p), title, group, self.settings.cloud_keep_local):
                n += 1
        if n:
            self._cloud_announced.discard(group)
        return n

    @staticmethod
    def _with_sidecars(path):
        return [path] + gdrive.sidecars(path) if path else []

    def _task_path(self, task):
        if task.burned_path and os.path.exists(task.burned_path):
            return task.burned_path       # 只保留弹幕版时，实际留下的是弹幕版
        return task.filepath

    def cloud_upload_task(self, task, manual=False):
        path = self._task_path(task)
        folder = gdrive.video_folder(task.video.platform, time.time())
        n = self._cloud_add(self._with_sidecars(path), lambda p: folder, task.video.title, f'task:{task.id}')
        if manual:
            self.notify(f'开始上传到谷歌云盘：{task.video.title}' if n else '这个视频已经在云盘里了')
        self._cloud_refresh_task(task.id)

    def cloud_upload_record(self, h):
        folder = gdrive.video_folder(h['platform'], h['ts'])
        title = h.get('title') or os.path.basename(h['filepath'] or '')
        n = self._cloud_add(self._with_sidecars(h['filepath']), lambda p: folder, title, f'rec:{h["id"]}')
        self.notify(f'开始上传到谷歌云盘：{title}（进度显示在这条记录上）' if n else '这个视频已经在云盘里了')
        self._cloud_refresh_rec(h['id'])

    # ------------------------------------------------------------ 下载记录上的进度
    def cloud_watch_record(self, rec_id, widget, show):
        """侧边栏 / 已下载的视频里的一条记录：上传时 show(短文字) 显示进度，传完 show('')。
        widget 被删掉（侧边栏刷新）后自动不再更新。"""
        if self.cloud is None:
            return
        import shiboken6
        views = [(w, f) for w, f in self._cloud_rec_views.get(rec_id, []) if shiboken6.isValid(w)]
        views.append((widget, show))
        self._cloud_rec_views[rec_id] = views
        text = self._cloud_rec_text(rec_id)
        if text:
            show(text)

    def _cloud_rec_text(self, rec_id):
        st = self.cloud.group_state(f'rec:{rec_id}') if self.cloud else None
        if not st or st[0] == gdrive.DONE:
            return ''
        state, done, total, ndone, n, err = st
        if state == gdrive.FAILED:
            return '失败'
        if state == gdrive.QUEUED and not done:
            return '等待'
        return f'{done * 100 // total if total else 0}%'

    def _cloud_refresh_rec(self, rec_id):
        text = self._cloud_rec_text(rec_id)
        import shiboken6
        alive = []
        for w, show in self._cloud_rec_views.get(rec_id, []):
            if shiboken6.isValid(w):        # 控件已经删掉了（侧边栏刷新过）就跳过
                show(text)
                alive.append((w, show))
        if alive:
            self._cloud_rec_views[rec_id] = alive
        else:
            self._cloud_rec_views.pop(rec_id, None)

    def cloud_upload_batch(self, b, manual=False):
        paths = b.done_paths(self.pan)

        def folder_of(p):
            rel = os.path.relpath(os.path.dirname(p), b.save_dir) if b.save_dir else ''
            return gdrive.pan_folder(b.provider_name, b.ts, '' if rel.startswith('..') else rel)
        n = self._cloud_add(paths, folder_of, b.title, f'batch:{b.id}')
        if manual:
            self.notify(f'开始上传 {n} 个文件到谷歌云盘：{b.title}' if n else '这批文件已经在云盘里了')
        self._cloud_refresh_batch(b.id)

    def cloud_flag_batch(self, batch_id, on=True):
        (self._cloud_flags.add if on else self._cloud_flags.discard)(batch_id)
        try:
            os.makedirs(gdrive.DIR, exist_ok=True)
            with open(_FLAGS_PATH, 'w', encoding='utf-8') as f:
                json.dump(sorted(self._cloud_flags), f)
        except OSError:
            pass

    def cloud_batch_finished(self, b):
        """网盘批次全部下完：开始下载时开着「云盘」开关的，现在上传"""
        if self.cloud is not None and b.id in self._cloud_flags:
            self.cloud_flag_batch(b.id, False)
            self.cloud_upload_batch(b)

    # ------------------------------------------------------------ 菜单
    def cloud_menu(self, m, paths, upload):
        """右键菜单里加「上传到谷歌云盘」或「在谷歌云盘中查看」"""
        if self.cloud is None:
            return
        ic = icons.icon('cloud_up', c('text2'))
        done = [self.cloud.uploaded_info(p) for p in paths if p]
        done = [d for d in done if d]
        m.addSeparator()
        if done:
            fid = done[0]['id']
            m.addAction(ic, '在谷歌云盘中查看', lambda: QDesktopServices.openUrl(QUrl(gdrive.file_link(fid))))
        if len(done) < len([p for p in paths if p]):
            a = m.addAction(ic, '上传到谷歌云盘' if not done else '上传剩下的文件到谷歌云盘', upload)
            a.setEnabled(any(p and os.path.isfile(p) for p in paths))

    def cloud_status(self, group):
        """任务 / 批次上显示的一小段：云盘 45% / 已传到云盘 / 云盘上传失败"""
        if self.cloud is None:
            return '', None
        st = self.cloud.group_state(group)
        if not st:
            return '', None
        state, done, total, ndone, n, err = st
        many = f' {ndone}/{n}' if n > 1 else ''
        if state == gdrive.DONE:
            return '已传到云盘', 'ok'
        if state == gdrive.FAILED:
            return f'云盘上传失败{many}', 'err'
        if state == gdrive.QUEUED and not done:
            return f'等待上传云盘{many}', 'text3'
        pct = done * 100 // total if total else 0
        return f'云盘 {pct}%{many}', 'info'

    def cloud_uploaded(self, path):
        return bool(self.cloud and path and self.cloud.uploaded_info(path))

    def cloud_retry(self, group):
        for j in self.cloud.group_jobs(group):
            if j['state'] == gdrive.FAILED:
                self.cloud.retry(j['id'])

    # ------------------------------------------------------------ 进度刷新
    def _cloud_on_change(self, job):
        group = job.get('group', '')
        kind, _, gid = group.partition(':')
        if kind == 'task':
            self._cloud_refresh_task(int(gid))
        elif kind == 'batch':
            self._cloud_refresh_batch(int(gid))
        elif kind == 'rec':
            self._cloud_refresh_rec(int(gid))
        st = self.cloud.group_state(group)
        if not st or st[0] in (gdrive.QUEUED, gdrive.RUNNING) or group in self._cloud_announced:
            return
        self._cloud_announced.add(group)
        state, _, _, ndone, n, err = st
        title = job.get('title') or ''
        if state == gdrive.DONE:
            self.notify(f'已上传到谷歌云盘：{title}' + (f'（{n} 个文件）' if n > 1 else ''), 'ok')
            if kind == 'rec' and getattr(self, 'page', '') == 'history':
                self._refresh_history()
        elif state == gdrive.FAILED:
            self.notify(f'上传到谷歌云盘失败：{err}', 'error')
            log.warning('谷歌云盘上传失败 %s：%s', title, err)

    def _cloud_refresh_task(self, task_id):
        for views in (self.task_views, self.task_lines):
            v = views.get(task_id)
            if v is not None:
                try:
                    v.refresh()
                except RuntimeError:
                    pass

    def _cloud_refresh_batch(self, batch_id):
        v = self.pan_views.get(batch_id) if hasattr(self, 'pan_views') else None
        if v is not None:
            try:
                v.refresh()
            except RuntimeError:
                pass

    # ------------------------------------------------------------ 账号
    def cloud_login(self, on_done):
        """后台打开浏览器授权；完成后 on_done(邮箱, 错误) 在界面线程里调用"""
        def finish(email, err):
            try:
                self._cloud_sig.login_done.disconnect()
            except (RuntimeError, TypeError):
                pass
            if not err:
                self._cloud_sync_chip()
                self.cloud.wake()
            on_done(email, err)
        self._cloud_sig.login_done.connect(finish)

        def work():
            try:
                self.cloud_drive.auth.login()
                user = self.cloud_drive.account().get('user') or {}
                email = user.get('emailAddress') or user.get('displayName') or ''
                self.cloud_drive.auth.set_email(email)
                self._cloud_sig.login_done.emit(email, '')
            except Exception as e:  # noqa: BLE001
                self._cloud_sig.login_done.emit('', str(e) or type(e).__name__)
        threading.Thread(target=work, daemon=True).start()

    def cloud_logout(self):
        self.cloud_drive.auth.logout()
        self._cloud_sync_chip()

    def cloud_open_root(self):
        QDesktopServices.openUrl(QUrl(self.cloud_drive.folder_link([gdrive.ROOT_NAME])))


def task_cloud_text(win, task):
    """TaskView 里用：这个任务的云盘状态文字和颜色"""
    if getattr(win, 'cloud', None) is None:
        return '', None
    text, role = win.cloud_status(f'task:{task.id}')
    if not text and getattr(task, 'cloud', False) and task.status not in (core.DONE, core.EXISTS,
                                                                           core.FAILED, core.CANCELED):
        return '下完后上传云盘', 'text3'
    return text, role
