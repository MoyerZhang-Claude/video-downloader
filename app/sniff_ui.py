"""网页资源嗅探的界面：解析结果列表里的一张卡片，列出从网页里找到的视频地址。

发一个网页地址（解析不了的网址会自动转到这里，也可以从「+」菜单里手动发起）：
先读网页源码找（page_sniff.sniff_static），找不到再在后台无界面地打开网页、自动播放，监听它请求的视频地址
（page_sniff.sniff_dynamic）。全程不弹浏览器窗口。
点「下载」就把这个地址（带上页面的请求头和 Cookie）交给解析核心，变成一张普通的解析结果卡片，
之后和别的视频一样选清晰度、进下载队列、断点续传。"""
import threading

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from . import browser, core, page_sniff, ui
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
    if res.codec:
        bits.append(res.codec)
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
        self.pick = _btn('选清晰度', lambda: win.sniff_download(self.res, auto=False, card=self.card()), 'ghost',
                         '加到解析结果里，自己选清晰度再下载')
        self.dl = _btn('下载', lambda: win.sniff_download(self.res, auto=True, card=self.card()), 'primary',
                       '按最高画质直接下载')
        h.addWidget(self.pick)
        h.addWidget(self.dl)
        self.update_text()

    def card(self):
        w = self.parentWidget()
        while w is not None and not isinstance(w, SniffCard):
            w = w.parentWidget()
        return w

    def update_text(self):
        res = self.res
        if res.protected:
            self.info.set_full_text('受版权保护（DRM），无法下载  ·  ' + res.host, res.url)
        else:
            self.info.set_full_text(describe(res), res.url)
        for b in (self.pick, self.dl):
            b.setVisible(not res.protected)


class _Found:
    """读网页源码找到的结果（和 sniffer.Sniffer 一样有 resources / title / drm，给卡片显示用）。"""

    def __init__(self, title, resources):
        self.title, self.resources, self.drm = title, resources, False


class SniffCard(Card):
    """一次嗅探。state：starting 正在读网页 / running 正在后台浏览器里查找 / stopped 结束了 / failed 出错了"""

    def __init__(self, win, url):
        super().__init__(radius=16)
        self.win, self.url = win, url
        self.sn = None
        self.cookies = {}               # 资源 key -> 下载时要带的 Cookie（后台浏览器里拿到的）
        self.state = 'starting'
        self.error = ''
        self.cancelled = False
        self.show_small = False
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.spark = Spark(22)
        self.spark.set_busy(True)
        top.addWidget(self.spark)
        self.title = ElidedLabel('正在读取网页…', 'cardTitle')
        top.addWidget(self.title, 1)
        self.stop_btn = _btn('停止', lambda: win.sniff_stop(self))
        self.again_btn = _btn('重新查找', lambda: win.sniff_start(self.url, replace=self))
        self.remove_btn = _btn('移除', lambda: win.sniff_remove(self), 'ghost')
        for b in (self.stop_btn, self.again_btn, self.remove_btn):
            top.addWidget(b)
        v.addLayout(top)
        self.sub = ElidedLabel(url, 'meta')
        v.addWidget(self.sub)
        self.tip = QLabel('')
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
        if not shiboken6.isValid(self):
            return
        self.state = state
        self.error = error
        self.spark.set_busy(state in ('starting', 'running'))
        self.refresh()

    def refresh(self):
        if not shiboken6.isValid(self):
            return
        sn, state = self.sn, self.state
        res = page_sniff.ordered(list(sn.resources)) if sn else []
        main = [r for r in res if not r.small]
        small = [r for r in res if r.small]
        name = (sn.title if sn else '') or self.url
        n = len(main)
        if state == 'starting':
            head = f'正在读取网页 · {name}'
        elif state == 'running':
            head = f'正在查找（已找到 {n} 个）· {name}' if n else f'正在查找 · {name}'
        elif state == 'failed':
            head = f'没有找到视频 · {name}'
        else:
            head = f'找到 {n} 个视频 · {name}' if n else f'没有找到视频 · {name}'
        self.title.set_full_text(head)
        self.stop_btn.setVisible(state in ('starting', 'running'))
        self.again_btn.setVisible(state in ('stopped', 'failed'))
        self.remove_btn.setVisible(state in ('stopped', 'failed'))
        tips = {'starting': '正在读取网页源码，找里面写着的视频地址…',
                'running': '网页源码里没有直接写视频地址：正在后台打开这个网页并自动播放，找播放器请求的视频地址'
                           '（不会弹出窗口，最多半分钟）…'}
        self.tip.setText(tips.get(state, ''))
        self.tip.setVisible(state in tips and ui.tips_on())
        shown = main + (small if self.show_small else [])
        self.line.setVisible(bool(shown))
        sync_layout(self.rows, [(r.key, (describe(r), r.protected), lambda r=r: _ResRow(self.win, r),
                                 lambda w: w.update_text()) for r in shown])
        self.small_btn.setVisible(bool(small))
        self.small_btn.setText((f'收起 {len(small)} 个小文件' if self.show_small else
                                f'还有 {len(small)} 个小文件（多半是广告、预览），点这里展开'))
        note = ''
        if state == 'failed':
            note = self.error or '没有找到视频'
        elif sn and (sn.drm or any(r.protected for r in res)):
            note = '这个页面的视频受版权保护（DRM），无法下载。'
        elif state == 'stopped' and not res:
            note = ('网页里没有找到视频地址。可能要登录才能看（设置 → 网络与登录），或者视频在别的页面里：'
                    '把播放页的网址发过来再试试。' if not self.cancelled else '已停止。')
        self.note.setText(note)
        self.note.setVisible(bool(note))


class SniffMixin:
    """混进主窗口：网页嗅探的入口和操作。可以同时有好几张嗅探卡片；后台浏览器同一时间只开一个（排队）。"""

    def _sniff_init(self):
        self.sniff_cards = []
        self._sniff_titles = {}         # 页面标题 -> 已经用了几次（同一个页面找到好几个视频时，文件名不能重名）

    @property
    def sniff_card(self):
        """还有没有嗅探卡片（兼容旧的写法：主窗口只关心有没有）。"""
        self.sniff_cards = [c for c in self.sniff_cards if shiboken6.isValid(c)]
        return self.sniff_cards[0] if self.sniff_cards else None

    def sniff_prompt(self, url=''):
        """「+」菜单里的「从网页里找视频…」：问网址。"""
        text = (url or self.composer.text()).strip()
        urls = core.extract_urls(text)
        dlg = ui.Dialog(self.central, '从网页里找视频',
                        '有的网站没有专门的解析器：发一个网页地址，软件读网页、必要时在后台打开网页播放一下，'
                        '把里面的视频地址找出来。不会弹出浏览器窗口。\n（直接在输入框里发解析不了的网址，也会自动这样找。）',
                        width=520)
        edit = QLineEdit(urls[0] if urls else '')
        edit.setPlaceholderText('网页地址，例如 https://www.example.com/video/123')
        dlg.body.addSpacing(8)
        dlg.body.addWidget(edit)
        dlg.add_button('取消')
        ok = dlg.add_button('查找视频', 'ok', 'primary')
        edit.returnPressed.connect(ok.click)
        edit.setFocus()
        if dlg.exec() != 'ok':
            return
        found = core.extract_urls(edit.text()) or core.extract_urls('https://' + edit.text().strip())
        if not found:
            self.notify('没有识别到网址', 'error')
            return
        self.sniff_start(found[0])

    def sniff_start(self, url, replace=None, at=0):
        """开始查找 url 里的视频：新加一张卡片（replace：重新查找时替换掉原来那张）。"""
        if replace is not None:
            at = max(0, self.res_list.indexOf(replace))
            self.sniff_remove(replace, update=False)
        elif self.fresh:                    # 在首页发起：和解析新链接一样，之前的结果清掉
            self._clear_results()
            self.fresh = False
        card = SniffCard(self, url)
        self.sniff_cards.append(card)
        self.res_list.insertWidget(at, card)
        emit = self.bridge.ui_call.emit
        log = self.bridge.log.emit

        def work():
            try:
                title, found = page_sniff.sniff_static(url, cookies=self._sniff_cookies(url), log=log)
            except page_sniff.SniffError as e:
                title, found, err = '', [], str(e)
            else:
                err = ''
            card.sn = _Found(title, found)
            if found or card.cancelled:
                emit(lambda: card.set_state('stopped'))
                return
            if not browser.available():
                emit(lambda: card.set_state('failed', err or '网页源码里没有找到视频地址；电脑上没有 Edge 或 Chrome，'
                                                             '没法在后台打开网页查找。'))
                return
            emit(lambda: card.set_state('running'))
            try:
                sn, cookies = page_sniff.sniff_dynamic(url, on_change=lambda: emit(card.refresh),
                                                       stopped=lambda: card.cancelled, log=log,
                                                       _attach=lambda s: setattr(card, 'sn', s))
                card.cookies = cookies
                emit(lambda: card.set_state('stopped'))
            except Exception as e:  # noqa: BLE001
                msg = str(e) or type(e).__name__
                emit(lambda: card.set_state('failed', f'后台浏览器没有打开：{msg}'))
        threading.Thread(target=work, daemon=True, name='sniff').start()
        self._go('parse')
        self._update_res_label()
        if at == 0:
            self.res_scroll.verticalScrollBar().setValue(0)
        return card

    def _sniff_cookies(self, url):
        """读网页源码时带上这个网站登录过的 Cookie（要登录才能看的页面）。"""
        try:
            from . import site_login
            site = site_login.for_url(url)
            if site is None or not site_login.logged_in(site.key):
                return None
            from . import secure_store
            return {c.name: c.value for c in secure_store.load_cookies(site_login.path_of(site.key)) if c.value}
        except Exception:  # noqa: BLE001
            return None

    def sniff_stop(self, card):
        if card is None or not shiboken6.isValid(card):
            return
        card.cancelled = True               # 后台的查找看到这个标记就结束、关掉浏览器
        if card.state == 'starting':
            card.set_state('stopped')

    def sniff_remove(self, card=None, update=True):
        """移除一张嗅探卡片（card=None：全部，清空解析结果时用）。"""
        cards = [card] if card is not None else list(self.sniff_cards)
        for c in cards:
            if c in self.sniff_cards:
                self.sniff_cards.remove(c)
            if not shiboken6.isValid(c):
                continue
            c.cancelled = True
            c.spark.set_busy(False)
            self.res_list.removeWidget(c)
            c.hide()
            c.setParent(None)
            c.deleteLater()
        if update:
            self._update_res_label()

    def sniff_download(self, res, auto=True, card=None):
        """把找到的视频地址交给解析核心（带上页面的请求头和 Cookie），变成一张普通的解析结果卡片。
        auto：解析完按最高画质直接下载；否则留在列表里让用户选清晰度。"""
        if res.protected:
            self.notify('这个视频受版权保护（DRM），无法下载', 'error')
            return
        old = next((r for r in self.rows.values() if r.url == res.url and r.state != 'error'), None)
        if old is not None:
            self.notify('这个视频已经在解析结果里了')
            if old.card is not None and shiboken6.isValid(old.card):
                self.res_scroll.ensureWidgetVisible(old.card, 0, 40)
            return
        sn = card.sn if card is not None else None
        base = res.page_title or (sn.title if sn else '') or res.host
        used = self._sniff_titles.get(base, 0)
        self._sniff_titles[base] = used + 1
        title = base if not used else f'{base} ({used + 1})'
        from .site_login import _to_cookie
        cookies = (card.cookies.get(res.key) if card is not None else None) or []
        at = self.res_list.indexOf(card) + 1 if card is not None and shiboken6.isValid(card) else 0
        token = self._new_row(res.url, at=max(0, at))
        row = self.rows[token]
        row.extra = {'headers': dict(res.headers), 'cookies': [_to_cookie(c) for c in cookies], 'title': title,
                     'page': res.page_url, 'id': res.stable_id(), 'codec': res.codec,
                     'height': res.heights[0] if len(res.heights) == 1 else 0}
        row.auto_download = auto
        self._start_parse(token)
        self.notify('已加入解析，解析完自动下载' if auto else '已加入解析结果，选好清晰度后点「下载」')
