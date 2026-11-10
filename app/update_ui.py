"""软件更新的界面（电脑版）。

- UpdateHint：侧边栏左下角头像上方的小条「新版本 2.5.0 · 查看」。启动时查到新版本只显示它，不弹窗。
- UpdateDialog：点开后的更新窗口。顶部是星芒图标、「链接解析下载器 x.y.z」和一行灰色小字（当前版本 · 发布日期 ·
  安装包大小）；中间是按「新功能 / 改进 / 修复」分组的更新内容（多了在窗口里滚动，不截断）；底部是
  「下载并安装 / 以后再说 / 跳过此版本」和「在 GitHub 查看完整说明」。
- 应用内更新：点「下载并安装」后在窗口里显示进度，下载 -windows-setup.exe，用 GitHub 给出的 sha256 核对，
  通过后启动安装程序并退出软件；核对不通过绝不运行。便携版（解压即用的 zip）、从源码运行、
  GitHub 没给校验值时，按钮是「去下载页」。
"""
import datetime
import html
import os
import re
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import APP_NAME, APP_VERSION, app_update, icons, proc_job, ui
from .config import DATA, ROOT
from .theme import c

UPDATE_DIR = os.path.join(DATA, 'update')       # 下载的安装包放这里，装完下次启动时清掉


def fmt_size(n):
    if not n:
        return ''
    mb = n / 1024 / 1024
    return f'{mb:.0f} MB' if mb >= 10 else f'{mb:.1f} MB'


def fmt_date(iso):
    """GitHub 的发布时间（UTC，2026-11-10T08:00:00Z）→ 本地的「11 月 10 日」；不是今年的带上年份。"""
    try:
        t = datetime.datetime.fromisoformat((iso or '').replace('Z', '+00:00')).astimezone()
    except ValueError:
        return ''
    year = f'{t.year} 年 ' if t.year != datetime.datetime.now().year else ''
    return f'{year}{t.month} 月 {t.day} 日'


def _mix(fg, bg, k):
    """两种颜色按比例混合成一个不透明的颜色（富文本里的底色不好用半透明）。"""
    return QColor(round(fg.red() * k + bg.red() * (1 - k)), round(fg.green() * k + bg.green() * (1 - k)),
                  round(fg.blue() * k + bg.blue() * (1 - k))).name()


def rich(text):
    """发布说明里的一条 → 富文本：**粗体**、`行内代码`、[文字](链接) 只留文字。"""
    out = html.escape(text, quote=False)
    out = re.sub(r'\[([^\]]+)\]\((?:[^)]+)\)', r'\1', out)
    code_bg = _mix(c('text'), c('panel'), 0.08)
    out = re.sub(r'`([^`]+)`', lambda m: f'<span style="font-family: Consolas, \'Cascadia Mono\', monospace; '
                                         f'background-color: {code_bg};">&nbsp;{m.group(1)}&nbsp;</span>', out)
    out = re.sub(r'\*\*([^*]+)\*\*', lambda m: f'<span style="font-weight: 600; color: {c("text").name()};">'
                                               f'{m.group(1)}</span>', out)
    return out


def installed_copy():
    """是不是用安装包装的（程序文件夹里有卸载程序）：只有这种才能用新的安装包直接升级。"""
    return bool(getattr(sys, 'frozen', False)) and os.path.exists(os.path.join(ROOT, 'unins000.exe'))


def can_install(rel):
    return bool(installed_copy() and rel.get('digest') and rel.get('download')
                and (rel.get('name') or '').endswith('-setup.exe'))


def clean_downloads():
    """清掉已经用不上的安装包（版本不比现在新的：说明已经装好了）。后台调用。"""
    try:
        names = os.listdir(UPDATE_DIR)
    except OSError:
        return
    for name in names:
        m = re.match(r'LinkDownloader-(\d+(?:\.\d+)*)-windows-setup\.exe(\.part)?$', name)
        if m and (m.group(2) or app_update._norm(m.group(1)) <= app_update._norm(APP_VERSION)):
            try:
                os.remove(os.path.join(UPDATE_DIR, name))
            except OSError:
                pass


def run_installer(path):
    """启动安装程序（它不跟着软件一起退出）。/SILENT：不用再点一遍「下一步」，只显示安装进度，装完自动打开新版。"""
    with proc_job.detached():
        try:
            subprocess.Popen([path, '/SILENT', '/NORESTART'], close_fds=True,
                             creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        except OSError:
            os.startfile(path, 'open', '/SILENT /NORESTART')


class UpdateHint(ui._HoverButton):
    """侧边栏底部的小条：「新版本 2.5.0 · 查看」。侧边栏收起时只剩一个图标。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.collapsed = False
        self.version = ''
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFont(ui.px_font(13))

    def set_version(self, version):
        self.version = version
        self.setToolTip(f'{APP_NAME} 有新版本 {version}（当前 {APP_VERSION}），点这里查看更新内容')
        self.update()

    def set_collapsed(self, on):
        self.collapsed = on
        self.update()

    def sizeHint(self):
        return QSize(40, 34)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        fill = QColor(c('tint_accent'))
        if self.isDown() or self._hov():
            fill.setAlpha(min(255, round(fill.alpha() * 1.5)))
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 9, 9)
        if self.collapsed:
            icons.paint(p, 'arrow_up', QRectF((self.width() - 16) / 2, (self.height() - 16) / 2, 16, 16), c('accent'), 2)
            return
        icons.paint(p, 'arrow_up', QRectF(11, (self.height() - 15) / 2, 15, 15), c('accent'), 2)
        p.setPen(c('accent'))
        p.setFont(self.font())
        text = ui.px_metrics(13).elidedText(f'新版本 {self.version} · 查看', Qt.ElideRight, self.width() - 36 - 8)
        p.drawText(QRectF(36, 0, self.width() - 44, self.height()), int(Qt.AlignVCenter | Qt.AlignLeft), text)


class _Notes(ui.SmoothScrollArea):
    """更新内容：不多时有多高就多高，多了最高 MAX_H，在里面滚动。"""
    MAX_H = 340

    def __init__(self, content, inner_width):
        super().__init__()
        self.inner = inner_width
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        self.setWidget(content)
        self.verticalScrollBar().setSingleStep(24)
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def sizeHint(self):
        w = self.widget()
        lay = w.layout()
        h = lay.totalHeightForWidth(self.inner) if lay.hasHeightForWidth() else w.sizeHint().height()
        return QSize(self.inner, min(self.MAX_H, h))


class _Sig(QObject):
    progress = Signal(float)
    done = Signal(str, str)         # (安装包路径, 错误)


class UpdateDialog(ui.Dialog):
    """exec() 返回：'install'（安装包已经下载并核对好，路径在 self.installer）/ 'skip'（跳过此版本）/
    None（以后再说、关掉）。"""
    WIDTH = 580

    def __init__(self, host, rel, proxy=''):
        super().__init__(host, f'{APP_NAME} {rel["version"]}', width=self.WIDTH)
        self.rel, self.proxy = rel, proxy
        self.installer = ''
        self._cancel = False
        self._busy = False
        self.title.setWordWrap(False)
        self.head.insertWidget(0, ui.Spark(30))
        bits = [f'当前 {APP_VERSION}']
        if fmt_date(rel.get('published')):
            bits.append(f'发布于 {fmt_date(rel["published"])}')
        if rel.get('size'):
            bits.append(f'安装包 {fmt_size(rel["size"])}')
        sub = QLabel(' · '.join(bits))
        sub.setObjectName('dialogSub')
        self.body.setSpacing(0)
        self.body.addWidget(sub)
        self.body.addSpacing(14)
        self.body.addWidget(ui.HLine())
        self.body.addSpacing(4)
        self.notes = _Notes(self._notes_widget(rel.get('sections') or []), self.WIDTH - 52)
        self.body.addWidget(self.notes, 1)
        self.body.addSpacing(4)
        self.body.addWidget(ui.HLine())
        self.body.addSpacing(8)
        link = QPushButton('在 GitHub 查看完整说明')
        link.setObjectName('link')
        link.setCursor(Qt.PointingHandCursor)
        link.setFocusPolicy(Qt.NoFocus)
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(rel['page'])))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(link)
        row.addStretch(1)
        self.body.addLayout(row)
        # 下载进度（点了「下载并安装」才出现）
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.status = QLabel('')
        self.status.setObjectName('dialogSub')
        self.status.setWordWrap(True)
        self.status.hide()
        self.body.addSpacing(6)
        self.body.addWidget(self.progress)
        self.body.addSpacing(6)
        self.body.addWidget(self.status)
        self.direct = can_install(rel)
        self.skip_btn = self.add_button('跳过此版本', 'skip', 'link', left=True)
        self.later_btn = self.add_button('以后再说')
        self.go_btn = self.add_button('下载并安装' if self.direct else '去下载页', 'go', 'primary')
        for b, fn in ((self.go_btn, self._go), (self.later_btn, self._later)):
            b.clicked.disconnect()
            b.clicked.connect(fn)
        if not self.direct and getattr(sys, 'frozen', False) and installed_copy():
            self._set_status('GitHub 没有给出这个安装包的校验值，为安全起见不自动安装：请到下载页手动下载。')
        self.sig = _Sig()
        self.sig.progress.connect(self._on_progress)
        self.sig.done.connect(self._on_done)
        self.closed.connect(self._on_closed)

    # 更新内容 ----------------------------------------------------------
    def _notes_widget(self, sections):
        w = QWidget()
        w.setObjectName('clear')
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 10, 8)
        v.setSpacing(0)
        text2 = c('text2').name()
        first = True
        for sec in sections:
            if sec.get('title'):
                if not first:
                    v.addSpacing(10)
                t = QLabel(sec['title'])
                t.setObjectName('updGroup')
                v.addWidget(t)
                v.addSpacing(2)
            for item in sec.get('items') or []:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(0)
                dot = QLabel('•')
                dot.setObjectName('updItem')
                dot.setFixedWidth(16)
                dot.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                row.addWidget(dot, 0, Qt.AlignTop)
                lab = QLabel(f'<span style="color: {text2};">{rich(item)}</span>')
                lab.setObjectName('updItem')
                lab.setTextFormat(Qt.RichText)
                lab.setWordWrap(True)
                lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
                row.addWidget(lab, 1)
                v.addLayout(row)
            first = False
        if first:
            empty = QLabel('这个版本没有写更新说明，可以到 GitHub 上看看。')
            empty.setObjectName('updItem')
            empty.setWordWrap(True)
            v.addWidget(empty)
        v.addStretch(1)
        return w

    # 按钮 --------------------------------------------------------------
    def _set_status(self, text, error=False):
        self.status.setText(text)
        self.status.setObjectName('metaError' if error else 'dialogSub')
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setVisible(bool(text))
        if self.isVisible():
            self._layout()

    def _later(self):
        if self._busy:                  # 下载中：这个按钮是「取消」
            self._cancel = True
            self.later_btn.setEnabled(False)
            return
        self.finish(None)

    def _go(self):
        if not self.direct:
            QDesktopServices.openUrl(QUrl(self.rel['page']))
            self.finish(None)
            return
        self._busy, self._cancel = True, False
        self.scrim_closes = False       # 下载到一半，不小心点到外面不要关
        self.skip_btn.hide()
        self.go_btn.setEnabled(False)
        self.go_btn.setText('正在下载…')
        self.later_btn.setText('取消')
        self.progress.setValue(0)
        self.progress.show()
        self._set_status('正在连接 GitHub…')
        rel, proxy = self.rel, self.proxy

        def work():
            try:
                path = app_update.download_installer(rel, UPDATE_DIR, self.sig.progress.emit, lambda: self._cancel,
                                                     proxy=proxy)
                self.sig.done.emit(path, '')
            except InterruptedError:
                self.sig.done.emit('', 'cancel')
            except Exception as e:  # noqa: BLE001
                self.sig.done.emit('', str(e) or type(e).__name__)
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, frac):
        self.progress.setValue(round(frac * 1000))
        size = self.rel.get('size') or 0
        self.status.setText(f'正在下载 {frac * 100:.0f}%' + (f' · {fmt_size(size * frac) or "0 MB"} / {fmt_size(size)}'
                                                           if size else ''))

    def _on_done(self, path, error):
        self._busy = False
        self.scrim_closes = True
        self.later_btn.setEnabled(True)
        self.later_btn.setText('以后再说')
        self.go_btn.setEnabled(True)
        if path:
            self.installer = path
            self.finish('install')
            return
        self.progress.hide()
        self.skip_btn.show()
        if error == 'cancel':
            self.go_btn.setText('下载并安装')
            self._set_status('')
            return
        # 下载失败或者校验不通过：安装包已经删掉了，没有运行
        self.go_btn.setText('重试')
        self._set_status(f'{error}', error=True)
        if not getattr(self, '_page_btn', None):
            self._page_btn = QPushButton('去下载页')
            self._page_btn.setCursor(Qt.PointingHandCursor)
            self._page_btn.setFocusPolicy(Qt.NoFocus)
            self._page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.rel['page'])))
            self.bar.insertWidget(self.bar.indexOf(self.later_btn), self._page_btn)
            self._layout()

    def _on_closed(self):
        self._cancel = True             # 按 Esc 关掉的：正在下载的话停下来
