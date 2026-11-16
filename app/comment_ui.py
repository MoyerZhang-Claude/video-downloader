"""「抓取评论」窗口：选条数、楼中楼、图片、导出格式，看进度，抓完打开网页 / 文件夹。

抓取在后台线程里跑（见 comments.py）。窗口不挡主界面：抓的过程中可以关掉窗口，抓完会在主界面提示。
"""
import math
import os
import threading

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


class _Bridge(QObject):
    progress = Signal(dict)
    done = Signal(object)
    failed = Signal(str)


_SCALES = [(50, '前 50 条'), (100, '前 100 条'), (200, '前 200 条'), (500, '前 500 条'), (1000, '前 1000 条'),
           (0, '全部'), (-1, '自定义…')]


class CommentDialog(QDialog):
    """url：视频链接；title、count：解析结果里已知的标题、评论数（不知道就留空，开始后从 B站拿）"""

    def __init__(self, win, url, title='', count=0):
        super().__init__(win)
        self.win, self.url = win, url
        self.count = int(count or 0)
        self.result = None
        self.cancel = threading.Event()
        self.running = False
        self.setWindowTitle('抓取评论')
        self.setMinimumWidth(580)
        saved = comments.Options.from_dict(win.settings.extra.get('comments'))
        self.opts = saved

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(8)
        v.addWidget(_label('抓取评论', 'sectionTitle'))
        self.title = ElidedLabel(title or url, 'rowLabel')
        v.addWidget(self.title)
        self.sub = _label(self._count_text(), 'rowDesc')
        v.addWidget(self.sub)
        v.addSpacing(8)

        self.form = QWidget()
        f = QVBoxLayout(self.form)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)

        self.sort = Combo()
        self.sort.addItem('按热度（和 B站默认一样）', 'hot')
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
        f.addLayout(self._row('图片', self.images, self.quality))
        self.avatars = QCheckBox('保存头像（网页里显示，每个只有几 KB）')
        self.avatars.setChecked(saved.avatars)
        f.addLayout(self._row('', self.avatars))

        self.fmt = {}
        boxes = []
        for key, text, tip in (('html', '网页', '像 B站评论区一样看：楼中楼能展开收起、能搜索、看对话、看大图；整个文件夹拷到哪都能打开'),
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
        if not bilibili.is_logged_in():
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
        self.b_left = QPushButton('取消')
        self.b_left.setCursor(Qt.PointingHandCursor)
        self.b_left.clicked.connect(self._left)
        self.b_mid = QPushButton('打开文件夹')
        self.b_mid.setCursor(Qt.PointingHandCursor)
        self.b_mid.clicked.connect(self._mid)
        self.b_mid.hide()
        self.b_go = QPushButton('开始抓取')
        self.b_go.setObjectName('primary')
        self.b_go.setCursor(Qt.PointingHandCursor)
        self.b_go.setDefault(True)
        self.b_go.clicked.connect(self._go)
        for b in (self.b_left, self.b_mid, self.b_go):
            btns.addWidget(b)
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
            return f'B站显示 {self.count:,} 条评论（包括楼中楼）'
        return '评论数开始抓取后才知道'

    def _collect(self):
        o = comments.Options()
        o.sort = self.sort.currentData()
        n = self.main.currentData()
        o.max_main = self.main_n.value() if n == -1 else n
        o.sub_mode = self.subm.currentData()
        o.sub_limit = self.sub_n.value()
        o.images = self.images.isChecked()
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
            self.est.setText('每页之间会停半秒左右，免得被 B站限流；评论多的视频要几分钟。')
            return
        o = self._collect()
        total = self.count
        mains = total * 0.35                                  # 粗估：主评论大约占三分之一
        want = min(mains, o.max_main) if o.max_main else mains
        share = want / mains if mains else 0
        reqs = math.ceil(want / 20) + 1
        if o.sub_mode != 'none':
            subs = (total - mains) * share
            if o.sub_mode == 'limit':
                subs = min(subs, want * min(o.sub_limit, 8))
            reqs += want * 0.18 + subs / 20
        sec = reqs * 0.9 + (want + (total - mains) * share) * 0.01 + 3
        t = '不到 1 分钟' if sec < 60 else f'大约 {math.ceil(sec / 60)} 分钟'
        self.est.setText(f'预计{t}（只是估计）。每页之间会停半秒左右，免得被 B站限流。')

    def _validate(self):
        self.b_go.setEnabled(any(cb.isChecked() for cb in self.fmt.values()) and not self.running)

    # ------------------------------------------------------------ 抓取
    def _go(self):
        if self.result:                        # 抓完了：「打开网页」
            self._open_main()
            return
        o = self._collect()
        self.win.settings.extra['comments'] = o.to_dict()
        self.win._save_settings()
        self.running = True
        self.cancel.clear()
        self.form.setEnabled(False)
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText('正在获取视频信息…')
        self.status.setObjectName('rowDesc')
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.show()
        self.est.hide()
        self.b_go.setEnabled(False)
        self.b_go.setText('正在抓取…')
        self.b_left.setText('停止并保存')
        self.b_mid.setText('在后台继续')
        self.b_mid.show()
        if not comments.LOGO_SVG:
            comments.LOGO_SVG = spark_svg()
        proxy = (self.win.settings.proxy or '').strip()

        def work():
            try:
                r = comments.Fetcher(o, proxy, self.bridge.progress.emit, self.cancel).fetch(self.url)
                self.bridge.done.emit(r)
            except comments.CommentError as e:
                self.bridge.failed.emit(str(e))
            except Exception as e:  # noqa: BLE001
                self.bridge.failed.emit(f'出错了：{e.__class__.__name__}: {e}')
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, d):
        stage = d['stage']
        parts = []
        if d['main_target'] and stage == 'main':
            parts.append(f'主评论 {d["main"]:,} / {d["main_target"]:,}')
        elif stage != 'start':
            parts.append(f'主评论 {d["main"]:,}')
        if stage in ('sub', 'media', 'export') and d['sub_target']:
            parts.append(f'楼中楼 {d["sub"]:,} / {d["sub_target"]:,}')
        if stage in ('media', 'export') and d['media_total']:
            parts.append(f'图片 {d["media"]:,} / {d["media_total"]:,}')
        if stage == 'export':
            parts.append('正在生成文件…')
        if d.get('wait'):
            parts.append(f'B站说太频繁了，等 {d["wait"]} 秒再继续')
        self.status.setText(' · '.join(parts) or '正在获取视频信息…')
        main_goal = d['main_target'] or d['main_total'] * 0.35 or 1       # 「全部」时按总数的三分之一粗估
        frac = {'start': 0, 'main': 0.35 * min(1, d['main'] / max(1, main_goal)),
                'sub': 0.35 + 0.45 * min(1, d['sub'] / max(1, d['sub_target'])),
                'media': 0.8 + 0.18 * min(1, d['media'] / max(1, d['media_total'])), 'export': 0.98}.get(stage, 0)
        self.bar.setValue(int(frac * 1000))

    def _on_done(self, r):
        self.running = False
        self.b_left.setEnabled(True)
        self.result = r
        self.bar.setValue(1000)
        msg = f'已保存：主评论 {r.n_main:,} 条、楼中楼 {r.n_sub:,} 条' + (f'、图片 {r.n_pics} 张' if r.n_pics else '')
        if r.stopped:
            msg = f'没有抓完（{r.stopped}）。{msg}'
        notes = [n for n in r.notes if not n.startswith('没有登录')]      # 没登录的提示上面已经有了
        self.status.setText(msg + ('\n' + '\n'.join(notes) if notes else ''))
        self.b_left.setText('关闭')
        self.b_mid.setText('打开文件夹')
        self.b_mid.show()
        self.b_go.setText('打开网页' if 'html' in r.files else '打开表格')
        self.b_go.setEnabled(True)
        if not self.isVisible():                     # 在后台抓完的：主界面提示
            self.win.notice.show_notice(f'「{r.meta["title"][:20]}」的评论已保存', '打开', self._open_main)

    def _on_failed(self, msg):
        self.running = False
        self.b_left.setEnabled(True)
        self.bar.hide()
        self.status.setText(msg)
        self.status.setObjectName('metaError')
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.form.setEnabled(True)
        self.b_left.setText('关闭')
        self.b_mid.hide()
        self.b_go.setText('重试')
        self.b_go.setEnabled(True)
        self.est.show()
        if not self.isVisible():
            self.win.notify(f'评论没抓到：{msg}', 'error')

    # ------------------------------------------------------------ 按钮
    def _open_main(self):
        r = self.result
        if not r:
            return
        path = r.files.get('html') or r.files.get('xlsx') or r.files.get('csv')
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
        if self.result and self.result.out_dir:
            os.startfile(self.result.out_dir)

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
