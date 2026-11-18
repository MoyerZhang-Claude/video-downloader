"""「抓取评论」窗口：选条数、楼中楼、图片、导出格式，看进度，抓完打开网页 / 文件夹。

抓取在后台线程里跑（见 comments.py）。窗口不挡主界面：抓的过程中可以关掉窗口，抓完会在主界面提示。
同一个视频抓过的：再打开窗口直接显示上次的结果（打开网页 / 文件夹），想更新就点「重新抓取」。
支持 B站、YouTube（YouTube 可以选填官方 API 密钥）。
"""
import math
import os
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import bilibili, comments
from .ui import Combo, spark_paths
from .widgets import ElidedLabel


def spark_svg():
    """软件标志（星芒落点）→ SVG，用在导出的网页页眉"""
    paths = []
    for p in spark_paths(100):
        d = []
        i, n = 0, p.elementCount()
        while i < n:
            e = p.elementAt(i)
            if e.type == QPainterPath.ElementType.MoveToElement:
                d.append(f'M{e.x:.2f} {e.y:.2f}')
            elif e.type == QPainterPath.ElementType.LineToElement:
                d.append(f'L{e.x:.2f} {e.y:.2f}')
            elif e.type == QPainterPath.ElementType.CurveToElement and i + 2 < n:
                c2, c3 = p.elementAt(i + 1), p.elementAt(i + 2)
                d.append(f'C{e.x:.2f} {e.y:.2f} {c2.x:.2f} {c2.y:.2f} {c3.x:.2f} {c3.y:.2f}')
                i += 2
            i += 1
        paths.append('<path d="' + ' '.join(d) + 'Z"/>')
    return ('<svg viewBox="0 -4 100 100" xmlns="http://www.w3.org/2000/svg" fill="currentColor">' + ''.join(paths) + '</svg>')


def _label(text, name, wrap=True):
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def _restyle(w, name):
    w.setObjectName(name)
    w.style().unpolish(w)
    w.style().polish(w)


class _Bridge(QObject):
    progress = Signal(dict)
    done = Signal(object)
    failed = Signal(str)


_SCALES = [(50, '前 50 条'), (100, '前 100 条'), (200, '前 200 条'), (500, '前 500 条'), (1000, '前 1000 条'),
           (0, '全部'), (-1, '自定义…')]


def summary_text(n_main, n_sub, n_pics=0, stopped=''):
    msg = f'主评论 {n_main:,} 条、楼中楼 {n_sub:,} 条' + (f'、图片 {n_pics} 张' if n_pics else '')
    return f'没有抓完（{stopped}）：{msg}' if stopped else msg


class CommentDialog(QDialog):
    """url：视频链接；title、count：解析结果里已知的标题、评论数（不知道就留空，开始后从网站拿）"""

    def __init__(self, win, url, title='', count=0):
        super().__init__(win)
        self.win, self.url = win, url
        self.count = int(count or 0)
        self.yt = comments.platform_of(url) == 'youtube'
        self.pf = 'YouTube' if self.yt else 'B站'
        self.done_info = None             # 抓完 / 上次抓的结果：{files, out_dir, title}
        self.cancel = threading.Event()
        self.running = False
        self.setWindowTitle('抓取评论')
        self.setMinimumWidth(600)
        saved = comments.Options.from_dict(win.settings.extra.get('comments'))

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(8)
        v.addWidget(_label(f'抓取 {self.pf} 评论', 'sectionTitle'))
        self.title = ElidedLabel(title or url, 'rowLabel')
        v.addWidget(self.title)
        self.sub = _label(self._count_text(), 'rowDesc')
        v.addWidget(self.sub)
        v.addSpacing(8)

        self.form = QWidget()
        f = QVBoxLayout(self.form)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)

        if self.yt:                        # YouTube：内置方式（yt-dlp）或官方 API
            self.way = _label('', 'rowDesc')
            self.way_btn = QPushButton()
            self.way_btn.setObjectName('link')
            self.way_btn.setCursor(Qt.PointingHandCursor)
            self.way_btn.clicked.connect(self._edit_key)
            f.addLayout(self._row('方式', self.way, self.way_btn))
            self._update_way()

        self.sort = Combo()
        self.sort.addItem(f'按热度（和 {self.pf} 默认一样）', 'hot')
        self.sort.addItem('按时间（最新的在前）', 'time')
        self.sort.setCurrentIndex(0 if saved.sort == 'hot' else 1)
        f.addLayout(self._row('排序', self.sort))

        self.main = Combo()
        for n, t in _SCALES:
            self.main.addItem(t, n)
        self.main_n = self._spin(saved.max_main if saved.max_main not in (50, 100, 200, 500, 1000, 0) else 300)
        i = self.main.findData(saved.max_main)
        self.main.setCurrentIndex(i if i >= 0 else self.main.count() - 1)
        self.main_unit = _label('条', 'rowDesc', False)
        f.addLayout(self._row('主评论', self.main, self.main_n, self.main_unit))

        self.subm = Combo()
        self.subm.addItem('每层全部', 'all')
        self.subm.addItem('每层最多…', 'limit')
        self.subm.addItem('不要楼中楼', 'none')
        self.subm.setCurrentIndex(max(0, self.subm.findData(saved.sub_mode)))
        self.sub_n = self._spin(saved.sub_limit or 20)
        self.sub_unit = _label('条（从最早的回复开始）', 'rowDesc', False)
        f.addLayout(self._row('楼中楼', self.subm, self.sub_n, self.sub_unit))

        self.images = QCheckBox('保存评论里的图片（不含动图）')
        self.images.setChecked(saved.images)
        self.quality = Combo()
        self.quality.addItem('压缩（推荐，小很多）', 'compressed')
        self.quality.addItem('原图', 'original')
        self.quality.setCurrentIndex(0 if saved.image_quality != 'original' else 1)
        img_row = QWidget()
        img_row.setLayout(self._row('图片', self.images, self.quality))
        img_row.layout().setContentsMargins(0, 0, 0, 0)
        img_row.setVisible(not self.yt)               # YouTube 的评论里没有图片
        f.addWidget(img_row)
        self.avatars = QCheckBox('保存头像（网页里显示，每个只有几 KB）')
        self.avatars.setChecked(saved.avatars)
        f.addLayout(self._row('头像' if self.yt else '', self.avatars))

        self.fmt = {}
        boxes = []
        for key, text, tip in (('html', '网页', '像评论区一样看：楼中楼能展开收起、能搜索、看对话、看大图；整个文件夹拷到哪都能打开'),
                               ('xlsx', 'Excel', '一行一条评论，楼中楼可以在 Excel 里折叠；方便排序、筛选、统计'),
                               ('csv', 'CSV', '纯文本表格，给别的软件或程序用')):
            cb = QCheckBox(text)
            cb.setToolTip(tip)
            cb.setChecked(key in saved.formats)
            cb.toggled.connect(self._validate)
            self.fmt[key] = cb
            boxes.append(cb)
        f.addLayout(self._row('导出', *boxes))
        self.where = _label('', 'rowDesc', False)
        self.where_btn = QPushButton('打开')
        self.where_btn.setObjectName('link')
        self.where_btn.setCursor(Qt.PointingHandCursor)
        self.where_btn.clicked.connect(self._open_root)
        f.addLayout(self._row('保存到', self.where, self.where_btn))
        v.addWidget(self.form)

        v.addSpacing(4)
        self.est = _label('', 'rowDesc')
        v.addWidget(self.est)
        if not self.yt and not bilibili.is_logged_in():
            v.addWidget(_label('还没有登录 B站：B站只给没登录的人看前几页评论。左下角头像菜单里扫码登录后能抓完整。',
                               'metaError'))

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        v.addWidget(self.bar)
        self.status = _label('', 'rowDesc')
        self.status.hide()
        v.addWidget(self.status)

        v.addSpacing(8)
        btns = QHBoxLayout()
        btns.addStretch(1)

        def pb(text, slot, name=''):
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            btns.addWidget(b)
            return b
        self.b_left = pb('取消', self._left)
        self.b_mid = pb('打开文件夹', self._mid)
        self.b_redo = pb('重新抓取', self._start)
        self.b_go = pb('开始抓取', self._go, 'primary')
        self.b_go.setDefault(True)
        self.b_mid.hide()
        self.b_redo.hide()
        self.b_redo.setToolTip('按上面的设置再抓一次（会另存一个新文件夹，上次的不会被覆盖）')
        v.addLayout(btns)

        for w in (self.sort, self.main, self.subm, self.quality):
            w.currentIndexChanged.connect(self._sync)
        for w in (self.main_n, self.sub_n):
            w.valueChanged.connect(self._sync)
        self.images.toggled.connect(self._sync)
        self.bridge = _Bridge(self)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.done.connect(self._on_done)
        self.bridge.failed.connect(self._on_failed)
        self._sync()

        prev = comments.last_export(url)     # 这个视频抓过：直接显示上次的结果
        if prev:
            when = time.strftime('%Y-%m-%d %H:%M', time.localtime(prev.get('time') or 0))
            self._show_done({'files': prev['files'], 'out_dir': prev.get('out_dir') or '', 'title': prev.get('title') or ''},
                            f'上次抓取（{when}）：' + summary_text(prev.get('n_main', 0), prev.get('n_sub', 0),
                                                              prev.get('n_pics', 0), prev.get('stopped', '')))

    # ------------------------------------------------------------ 表单
    def _spin(self, value):
        s = QSpinBox()
        s.setRange(1, 100000)
        s.setValue(int(value))
        s.setFixedWidth(90)
        s.setAlignment(Qt.AlignRight)
        return s

    def _row(self, title, *widgets):
        h = QHBoxLayout()
        h.setSpacing(10)
        lab = _label(title, 'rowLabel', False)
        lab.setFixedWidth(64)
        h.addWidget(lab)
        for w in widgets:
            h.addWidget(w)
        h.addStretch(1)
        return h

    def _count_text(self):
        if self.count:
            return f'{self.pf} 显示 {self.count:,} 条评论（包括楼中楼）'
        return '评论数开始抓取后才知道'

    def _update_way(self):
        key = (self.win.settings.yt_api_key or '').strip()
        if key:
            self.way.setText(f'官方 API（密钥 …{key[-4:]}）：时间、点赞数准确；用不了时自动换内置方式')
            self.way_btn.setText('更换密钥')
        else:
            self.way.setText('内置方式（不需要密钥）：时间是估算的，点赞数是约数')
            self.way_btn.setText('填写官方 API 密钥…')

    def _edit_key(self):
        from .folder_dialog import TextDialog
        dlg = TextDialog(self, 'YouTube Data API v3 密钥', self.win.settings.yt_api_key or '',
                         '可选。填了以后用官方接口抓 YouTube 评论：时间、点赞数准确，也更快；每天有免费额度（大约能抓几十万条）。'
                         '\n获取：谷歌云控制台（console.cloud.google.com）→ 选一个项目 → 「API 和服务」→「库」里启用 '
                         'YouTube Data API v3 →「凭据」→「创建凭据」→「API 密钥」，复制过来。\n清空 = 改回内置方式。',
                         'AIza…')
        if dlg.exec():
            self.win.settings.yt_api_key = dlg.value.strip()
            self.win._save_settings()
            self._update_way()
            self._estimate()

    def _collect(self):
        o = comments.Options()
        o.sort = self.sort.currentData()
        n = self.main.currentData()
        o.max_main = self.main_n.value() if n == -1 else n
        o.sub_mode = self.subm.currentData()
        o.sub_limit = self.sub_n.value()
        o.images = self.images.isChecked() and not self.yt
        o.image_quality = self.quality.currentData()
        o.avatars = self.avatars.isChecked()
        o.formats = tuple(k for k, cb in self.fmt.items() if cb.isChecked())
        o.out_root = os.path.join(self.win.settings.download_dir, '评论')
        return o

    def _sync(self):
        custom, limit = self.main.currentData() == -1, self.subm.currentData() == 'limit'
        self.main_n.setVisible(custom)
        self.main_unit.setVisible(custom)
        self.sub_n.setVisible(limit)
        self.sub_unit.setVisible(limit)
        self.quality.setEnabled(self.images.isChecked())
        root = os.path.join(self.win.settings.download_dir, '评论')
        short = root if len(root) <= 46 else root[:16] + '…' + root[-28:]
        self.where.setText(short)
        self.where.setToolTip(root + '\n每次抓取单独一个文件夹：「视频标题 的评论 日期 时间」')
        self._estimate()
        self._validate()

    def _estimate(self):
        if not self.count:
            self.est.setText('评论多的视频要几分钟。')
            return
        o = self._collect()
        total = self.count
        mains = total * 0.35                                  # 粗估：主评论大约占三分之一
        want = min(mains, o.max_main) if o.max_main else mains
        share = want / mains if mains else 0
        subs = 0 if o.sub_mode == 'none' else (total - mains) * share
        if o.sub_mode == 'limit':
            subs = min(subs, want * min(o.sub_limit, 8))
        if self.yt:
            sec = 10 + want / 20 * 1.2 + (0 if o.sub_mode == 'none' else want * 0.25 * 1.2 + subs / 10 * 1.2)
            if (self.win.settings.yt_api_key or '').strip():
                sec = 3 + want / 100 + (0 if o.sub_mode == 'none' else want * 0.1 * 0.4)
            tail = ''
        else:
            sec = (math.ceil(want / 20) + 1 + (0 if o.sub_mode == 'none' else want * 0.18 + subs / 20)) * 0.9 \
                + (want + subs) * 0.01 + 3
            tail = '每页之间会停半秒左右，免得被 B站限流。'
        t = '不到 1 分钟' if sec < 60 else f'大约 {math.ceil(sec / 60)} 分钟'
        self.est.setText(f'预计{t}（只是估计）。{tail}')

    def _validate(self):
        self.b_go.setEnabled(not self.running and (self.done_info is not None or any(cb.isChecked() for cb in self.fmt.values())))
        self.b_redo.setEnabled(not self.running and any(cb.isChecked() for cb in self.fmt.values()))

    # ------------------------------------------------------------ 抓取
    def _go(self):
        if self.done_info:                     # 抓完了 / 上次抓过：「打开网页」
            self._open_main()
            return
        self._start()

    def _start(self):
        o = self._collect()
        if not o.formats:
            return
        self.win.settings.extra['comments'] = o.to_dict()
        self.win._save_settings()
        self.running = True
        self.done_info = None
        self.cancel.clear()
        self.form.setEnabled(False)
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText('正在获取视频信息…')
        _restyle(self.status, 'rowDesc')
        self.status.show()
        self.est.hide()
        self.b_go.setEnabled(False)
        self.b_go.setText('正在抓取…')
        self.b_left.setText('停止并保存')
        self.b_left.setEnabled(True)
        self.b_mid.setText('在后台继续')
        self.b_mid.show()
        self.b_redo.hide()
        if not comments.LOGO_SVG:
            comments.LOGO_SVG = spark_svg()
        st = self.win.settings
        proxy = (st.proxy or '').strip()

        def work():
            try:
                r = comments.Fetcher(o, proxy, self.bridge.progress.emit, self.cancel, settings=st,
                                     yt_key=st.yt_api_key).fetch(self.url)
                comments.remember(r, self.url)
                self.bridge.done.emit(r)
            except comments.CommentError as e:
                self.bridge.failed.emit(str(e))
            except Exception as e:  # noqa: BLE001
                self.bridge.failed.emit(f'出错了：{e.__class__.__name__}: {e}')
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, d):
        stage = d['stage']
        parts = []
        if stage == 'all':                     # YouTube 内置方式：主评论和回复一起来
            parts.append(f'已抓 {d["count"]:,} 条（含回复）')
            if d['main']:
                parts.append(f'正在第 {d["main"]:,}' + (f' / {d["main_target"]:,}' if d['main_target'] else '') + ' 条主评论')
        elif d['main_target'] and stage == 'main':
            parts.append(f'主评论 {d["main"]:,} / {d["main_target"]:,}')
        elif stage not in ('start',) and d['main']:
            parts.append(f'主评论 {d["main"]:,}')
        if stage in ('sub', 'media', 'export') and d['sub_target']:
            parts.append(f'楼中楼 {d["sub"]:,} / {d["sub_target"]:,}')
        if stage in ('media', 'export') and d['media_total']:
            parts.append(f'图片和头像 {d["media"]:,} / {d["media_total"]:,}')
        if stage == 'export':
            parts.append('正在生成文件…')
        if d.get('wait'):
            parts.append(f'{self.pf} 说太频繁了，等 {d["wait"]} 秒再继续')
        self.status.setText(' · '.join(parts) or '正在获取视频信息…')
        if stage == 'all':
            frac = 0.8 * min(1, (d['main'] / d['main_target']) if d['main_target'] and d['main']
                             else d['count'] / max(1, d['count_goal']))
        else:
            main_goal = d['main_target'] or d['main_total'] * 0.35 or 1       # 「全部」时按总数的三分之一粗估
            frac = {'start': 0, 'main': 0.35 * min(1, d['main'] / max(1, main_goal)),
                    'sub': 0.35 + 0.45 * min(1, d['sub'] / max(1, d['sub_target'])),
                    'media': 0.8 + 0.18 * min(1, d['media'] / max(1, d['media_total'])), 'export': 0.98}.get(stage, 0)
        self.bar.setValue(max(self.bar.value(), int(frac * 1000)))

    def _show_done(self, info, text):
        """抓完 / 上次抓过：状态 + 「打开网页」「打开文件夹」「重新抓取」"""
        self.done_info = info
        self.bar.hide()
        self.status.setText(text)
        _restyle(self.status, 'rowDesc')
        self.status.show()
        self.est.show()
        self.form.setEnabled(True)            # 可以改设置再「重新抓取」
        self.b_left.setText('关闭')
        self.b_left.setEnabled(True)
        self.b_mid.setText('打开文件夹')
        self.b_mid.setVisible(bool(info.get('out_dir')))
        self.b_redo.show()
        self.b_go.setText('打开网页' if 'html' in info['files'] else '打开表格')
        self._validate()

    def _on_done(self, r):
        self.running = False
        notes = [n for n in r.notes if not n.startswith('没有登录')]      # 没登录的提示上面已经有了
        self._show_done({'files': dict(r.files), 'out_dir': r.out_dir, 'title': r.meta.get('title') or ''},
                        '已保存：' + summary_text(r.n_main, r.n_sub, r.n_pics, r.stopped) +
                        ('\n' + '\n'.join(notes) if notes else ''))
        if r.meta.get('reply_total') and not self.count:
            self.count = r.meta['reply_total']
            self.sub.setText(self._count_text())
        if not self.isVisible():                     # 在后台抓完的：主界面提示
            self.win.notice.show_notice(f'「{(r.meta.get("title") or "")[:20]}」的评论已保存', '打开', self._open_main)

    def _on_failed(self, msg):
        self.running = False
        self.bar.hide()
        self.status.setText(msg)
        _restyle(self.status, 'metaError')
        self.form.setEnabled(True)
        self.b_left.setText('关闭')
        self.b_left.setEnabled(True)
        self.b_mid.hide()
        self.b_go.setText('重试')
        self.est.show()
        self._validate()
        if not self.isVisible():
            self.win.notify(f'评论没抓到：{msg}', 'error')

    # ------------------------------------------------------------ 按钮
    def _open_main(self):
        info = self.done_info
        if not info:
            return
        path = info['files'].get('html') or info['files'].get('xlsx') or info['files'].get('csv')
        if path and os.path.exists(path):
            os.startfile(path)
        self.win.notice.hide()

    def _open_root(self):
        root = os.path.join(self.win.settings.download_dir, '评论')
        os.makedirs(root, exist_ok=True)
        os.startfile(root)

    def _left(self):
        if self.running:                       # 停止：已经抓到的照样保存
            self.cancel.set()
            self.b_left.setEnabled(False)
            self.status.setText('正在停止，马上保存已经抓到的…')
            return
        self.close()

    def _mid(self):
        if self.running:                       # 在后台继续：关窗口，抓完在主界面提示
            self.hide()
            self.win.notify('评论在后台继续抓，抓完会提示你')
            return
        if self.done_info and self.done_info.get('out_dir') and os.path.isdir(self.done_info['out_dir']):
            os.startfile(self.done_info['out_dir'])

    def closeEvent(self, e):
        if self.running:                       # 抓取中关窗口 = 在后台继续
            e.ignore()
            self._mid()
            return
        super().closeEvent(e)

    def reject(self):
        if self.running:
            self._mid()
            return
        super().reject()
