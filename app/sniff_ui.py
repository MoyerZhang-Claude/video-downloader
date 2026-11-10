"""网页资源嗅探的界面：解析结果列表最上面的一张卡片，列出在内置浏览器里抓到的视频地址。
点「下载」就把这个地址（带上页面的请求头和 Cookie）交给解析核心，变成一张普通的解析结果卡片，
之后和别的视频一样选清晰度、进下载队列、断点续传。"""
import threading

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from . import browser, core, sniffer, ui
from .ui import Card, HLine, Spark, sync_layout
from .widgets import ElidedLabel


def _btn(text, slot, name='', tip=''):
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    b.clicked.connect(slot)
    return b


def _duration(sec):
    sec = int(round(sec or 0))
    if not sec:
        return ''
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def describe(res):
    """一条资源的说明：清晰度 · 时长 · 大小 · 来源"""
    bits = []
    if res.heights:
        bits.append(f'最高 {res.heights[0]}p' + (f'（{len(res.heights)} 档清晰度）' if len(res.heights) > 1 else ''))
    if res.live:
        bits.append('直播')
    elif res.duration:
        bits.append(_duration(res.duration))
    if res.size:
        bits.append(core.fmt_size(res.size))
    bits.append(res.host)
    return '  ·  '.join(b for b in bits if b)


class _ResRow(QWidget):
    """卡片里的一条资源：类型标签 + 说明 + 「选清晰度」「下载」"""

    def __init__(self, win, res):
        super().__init__()
        self.setObjectName('clear')
        self.win, self.res = win, res
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 5, 0, 5)
        h.setSpacing(10)
        tag = QLabel(res.label)
        tag.setObjectName('tagInfo')
        tag.setFixedWidth(52)
        tag.setAlignment(Qt.AlignCenter)
        h.addWidget(tag)
        self.info = ElidedLabel('', 'metaStrong')
        h.addWidget(self.info, 1)
        self.pick = _btn('选清晰度', lambda: win.sniff_download(self.res, auto=False), 'ghost',
                         '加到解析结果里，自己选清晰度再下载')
        self.dl = _btn('下载', lambda: win.sniff_download(self.res, auto=True), 'primary', '按最高画质直接下载')
        h.addWidget(self.pick)
        h.addWidget(self.dl)
        self.update_text()

    def update_text(self):
        res = self.res
        if res.protected:
            self.info.set_full_text('受版权保护（DRM），无法下载  ·  ' + res.host, res.url)
        else:
            self.info.set_full_text(describe(res), res.url)
        for b in (self.pick, self.dl):
            b.setVisible(not res.protected)


class SniffCard(Card):
    """一次嗅探。state：starting 正在打开浏览器 / running 正在监听 / stopped 已停止 / failed 没打开"""

    def __init__(self, win, url):
        super().__init__(radius=16)
        self.win, self.url = win, url
        self.sn = None
        self.state = 'starting'
        self.show_small = False
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.spark = Spark(22)
        self.spark.set_busy(True)
        top.addWidget(self.spark)
        self.title = ElidedLabel('正在打开内置浏览器…', 'cardTitle')
        top.addWidget(self.title, 1)
        self.stop_btn = _btn('停止并关闭浏览器', lambda: win.sniff_stop())
        self.again_btn = _btn('重新嗅探', lambda: win.sniff_start(self.url))
        self.remove_btn = _btn('移除', lambda: win.sniff_remove(), 'ghost')
        for b in (self.stop_btn, self.again_btn, self.remove_btn):
            top.addWidget(b)
        v.addLayout(top)
        self.sub = ElidedLabel(url, 'meta')
        v.addWidget(self.sub)
        self.tip = QLabel('在打开的浏览器窗口里播放视频，抓到的视频地址会出现在下面。有的网站要先点播放、看完广告才会开始加载正片。')
        self.tip.setObjectName('hint')
        self.tip.setWordWrap(True)
        v.addWidget(self.tip)
        self.line = HLine()
        v.addWidget(self.line)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        v.addLayout(self.rows)
        self.small_btn = _btn('', self._toggle_small, 'link')
        v.addWidget(self.small_btn, 0, Qt.AlignLeft)
        self.note = QLabel('')
        self.note.setObjectName('metaError')
        self.note.setWordWrap(True)
        v.addWidget(self.note)
        ui.tip_signals.changed.connect(self._tips_changed)
        self.refresh()

    def _tips_changed(self, _on):
        if shiboken6.isValid(self):
            self.refresh()

    def _toggle_small(self):
        self.show_small = not self.show_small
        self.refresh()

    def set_state(self, state, error=''):
        self.state = state
        self.error = error
        self.spark.set_busy(state in ('starting', 'running'))
        self.refresh()

    def refresh(self):
        if not shiboken6.isValid(self):
            return
        sn, state = self.sn, self.state
        res = list(sn.resources) if sn else []
        main = [r for r in res if not r.small]
        small = [r for r in res if r.small]
        name = (sn.title if sn else '') or self.url
        n = len(main)
        if state == 'starting':
            head = '正在打开内置浏览器…'
        elif state == 'running':
            head = f'正在嗅探（已找到 {n} 个）· {name}' if n else f'正在嗅探 · {name}'
        elif state == 'failed':
            head = '内置浏览器没有打开'
        else:
            head = f'嗅探到 {n} 个视频资源 · {name}' if n else f'没有嗅探到视频 · {name}'
        self.title.set_full_text(head)
        self.stop_btn.setVisible(state in ('starting', 'running'))
        self.again_btn.setVisible(state in ('stopped', 'failed'))
        self.remove_btn.setVisible(state in ('stopped', 'failed'))
        self.tip.setVisible(state in ('starting', 'running') and ui.tips_on())
        shown = main + (small if self.show_small else [])
        self.line.setVisible(bool(shown))
        sync_layout(self.rows, [(r.key, (describe(r), r.protected), lambda r=r: _ResRow(self.win, r),
                                 lambda w: w.update_text()) for r in shown])
        self.small_btn.setVisible(bool(small))
        self.small_btn.setText((f'收起 {len(small)} 个小文件' if self.show_small else
                                f'还有 {len(small)} 个小文件（多半是广告、预览），点这里展开'))
        note = ''
        if state == 'failed':
            note = getattr(self, 'error', '') or '内置浏览器没有打开'
        elif sn and (sn.drm or any(r.protected for r in res)):
            note = '这个页面的视频受版权保护（DRM），无法下载。'
        elif state == 'stopped' and not res:
            note = '没有抓到视频地址：可以点「重新嗅探」，在浏览器里把视频播放起来再等几秒。'
        self.note.setText(note)
        self.note.setVisible(bool(note))


class SniffMixin:
    """混进主窗口：网页嗅探的入口和操作。同一时间只有一次嗅探（内置浏览器只有一个窗口）。"""

    def _sniff_init(self):
        self.sniff_card = None
        self._sniff_titles = {}         # 页面标题 -> 已经用了几次（同一个页面抓到好几个资源时，文件名不能重名）

    def sniff_prompt(self, url=''):
        """「打开网页嗅探视频…」：问网址。"""
        text = (url or self.composer.text()).strip()
        urls = core.extract_urls(text)
        dlg = ui.Dialog(self.central, '打开网页嗅探视频', '有的网站没有专门的解析器，或者视频地址要等播放器运行起来才出现。'
                        '在内置浏览器里打开网页并播放视频，软件会把视频地址抓出来。', width=520)
        edit = QLineEdit(urls[0] if urls else '')
        edit.setPlaceholderText('网页地址，例如 https://www.example.com/video/123')
        dlg.body.addSpacing(8)
        dlg.body.addWidget(edit)
        dlg.add_button('取消')
        ok = dlg.add_button('打开并嗅探', 'ok', 'primary')
        edit.returnPressed.connect(ok.click)
        edit.setFocus()
        if dlg.exec() != 'ok':
            return
        found = core.extract_urls(edit.text()) or core.extract_urls('https://' + edit.text().strip())
        if not found:
            self.notify('没有识别到网址', 'error')
            return
        self.sniff_start(found[0])

    def sniff_start(self, url):
        if not browser.available():
            ui.alert(self, '没有找到 Edge 或 Chrome', '网页嗅探要用到电脑上的 Edge 或 Chrome 浏览器，这台电脑上没有找到。')
            return
        self.sniff_remove(update=False)
        if self.fresh:                      # 在首页发起：和解析新链接一样，之前的结果清掉
            self._clear_results()
            self.fresh = False
        card = SniffCard(self, url)
        self.sniff_card = card
        self.res_list.insertWidget(0, card)
        emit = self.bridge.ui_call.emit
        sn = sniffer.Sniffer(url, on_change=lambda: emit(card.refresh),
                             on_closed=lambda: emit(lambda: self._sniff_closed(card)))
        card.sn = sn

        def work():
            try:
                sn.start()
                emit(lambda: shiboken6.isValid(card) and card.state == 'starting' and card.set_state('running'))
            except Exception as e:  # noqa: BLE001
                msg = str(e) or type(e).__name__
                sn.stop()
                emit(lambda: shiboken6.isValid(card) and card.set_state('failed', msg))
        threading.Thread(target=work, daemon=True).start()
        self._go('parse')
        self._update_res_label()
        self.res_scroll.verticalScrollBar().setValue(0)

    def _sniff_closed(self, card):
        """用户自己把浏览器窗口关了。"""
        if shiboken6.isValid(card) and card.state in ('starting', 'running'):
            card.sn.stop(close_browser=False)
            card.set_state('stopped')

    def sniff_stop(self):
        card = self.sniff_card
        if card is None or not shiboken6.isValid(card):
            return
        sn = card.sn
        card.set_state('stopped')
        if sn is not None:
            threading.Thread(target=sn.stop, daemon=True).start()       # 关浏览器要等一下：不卡界面

    def sniff_remove(self, update=True):
        card, self.sniff_card = self.sniff_card, None
        if card is None:
            return
        if card.sn is not None and card.sn.running:
            threading.Thread(target=card.sn.stop, daemon=True).start()
        if shiboken6.isValid(card):
            card.spark.set_busy(False)
            self.res_list.removeWidget(card)
            card.hide()
            card.setParent(None)
            card.deleteLater()
        if update:
            self._update_res_label()

    def sniff_download(self, res, auto=True):
        """把嗅探到的资源交给解析核心（带上页面的请求头和 Cookie），变成一张普通的解析结果卡片。
        auto：解析完按最高画质直接下载；否则留在列表里让用户选清晰度。"""
        if res.protected:
            self.notify('这个视频受版权保护（DRM），无法下载', 'error')
            return
        old = next((r for r in self.rows.values() if r.url == res.url and r.state != 'error'), None)
        if old is not None:
            self.notify('这个资源已经在解析结果里了')
            if old.card is not None and shiboken6.isValid(old.card):
                self.res_scroll.ensureWidgetVisible(old.card, 0, 40)
            return
        card = self.sniff_card
        sn = card.sn if card is not None else None
        base = res.page_title or (sn.title if sn else '') or res.host
        used = self._sniff_titles.get(base, 0)
        self._sniff_titles[base] = used + 1
        title = base if not used else f'{base} ({used + 1})'
        emit = self.bridge.ui_call.emit

        def add(cookies):
            from .site_login import _to_cookie
            token = self._new_row(res.url, at=1 if self.sniff_card is not None else 0)
            row = self.rows[token]
            row.extra = {'headers': dict(res.headers), 'cookies': [_to_cookie(c) for c in cookies], 'title': title,
                         'page': res.page_url, 'id': res.stable_id()}
            row.auto_download = auto
            self._start_parse(token)
            self.notify('已加入解析，解析完自动下载' if auto else '已加入解析结果，选好清晰度后点「下载」')

        def work():
            try:
                cookies = sn.cookies_for(res.url) if sn is not None else []
            except Exception:  # noqa: BLE001 - 拿不到 Cookie 也先试试
                cookies = []
            emit(lambda: add(cookies))
        threading.Thread(target=work, daemon=True).start()
