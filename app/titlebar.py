"""Windows：去掉系统标题栏，界面一直铺到窗口顶部（像 Claude 桌面 App），右上角自己画最小化 / 最大化 / 关闭。

做法：保留系统窗口边框（拖边缘改大小、贴边分屏、窗口阴影和动画都还在），只在 WM_NCCALCSIZE 里把顶部的标题栏去掉；
WM_NCHITTEST 里告诉系统：顶部 TITLE_H 高度内的空白处是「标题栏」（可以拖动窗口、双击最大化），最上面几个像素可以拖动改高度。
"""
import ctypes
import ctypes.wintypes as wt
import sys

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QHBoxLayout, QLineEdit, QWidget

from .theme import c

TITLE_H = 36            # 顶部可以拖动窗口的高度（逻辑像素）
BTN_W = 46

WM_NCCALCSIZE, WM_NCHITTEST = 0x0083, 0x0084
HTCLIENT, HTCAPTION, HTTOP, HTTOPLEFT, HTTOPRIGHT = 1, 2, 12, 13, 14
_BORDER_HITS = {10, 11, 15, 16, 17}         # 左、右、下、左下、右下（系统边框自己处理）


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [('rgrc', wt.RECT * 3), ('lppos', ctypes.c_void_p)]


def _user32():
    u = ctypes.windll.user32
    u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    u.DefWindowProcW.restype = ctypes.c_ssize_t
    u.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wt.UINT]
    u.GetDpiForWindow.argtypes = [wt.HWND]
    u.IsZoomed.argtypes = [wt.HWND]
    u.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
    u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    return u


class CaptionButton(QAbstractButton):
    """最小化 / 最大化（还原）/ 关闭：Windows 11 的样式，关闭按钮悬停变红。"""

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.maximized = False
        self.setFixedSize(BTN_W, TITLE_H)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setToolTip({'min': '最小化', 'max': '最大化', 'close': '关闭'}[kind])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hov, down = self.underMouse(), self.isDown()
        if hov or down:
            if self.kind == 'close':
                p.fillRect(self.rect(), QColor('#c42b1c') if not down else QColor('#b22a1b'))
            else:
                p.fillRect(self.rect(), c('press') if down else c('hover'))
        color = QColor('#ffffff') if self.kind == 'close' and (hov or down) else c('text2')
        if not self.window().isActiveWindow() and not hov:
            color = c('text4')
        pen = QPen(color, 1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        cx, cy = self.width() / 2, self.height() / 2
        s = 5.0
        if self.kind == 'min':
            p.drawLine(QPointF(cx - s, cy), QPointF(cx + s, cy))
        elif self.kind == 'max':
            if self.maximized:          # 还原：两个叠着的方框
                p.drawRoundedRect(QRectF(cx - s, cy - s + 2, 2 * s - 2, 2 * s - 2), 1.2, 1.2)
                p.drawLine(QPointF(cx - s + 2, cy - s), QPointF(cx + s - 1, cy - s))
                p.drawLine(QPointF(cx + s, cy - s + 1), QPointF(cx + s, cy + s - 2))
            else:
                p.drawRoundedRect(QRectF(cx - s, cy - s, 2 * s, 2 * s), 1.5, 1.5)
        else:
            p.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
            p.drawLine(QPointF(cx + s, cy - s), QPointF(cx - s, cy + s))

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class CaptionButtons(QWidget):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.bmin, self.bmax, self.bclose = CaptionButton('min'), CaptionButton('max'), CaptionButton('close')
        self.bmin.clicked.connect(win.showMinimized)
        self.bmax.clicked.connect(lambda: win.showNormal() if win.isMaximized() else win.showMaximized())
        self.bclose.clicked.connect(win.close)
        for b in (self.bmin, self.bmax, self.bclose):
            h.addWidget(b)
        self.setFixedSize(BTN_W * 3, TITLE_H)

    def sync(self):
        self.bmax.maximized = self.win.isMaximized()
        self.bmax.setToolTip('还原' if self.bmax.maximized else '最大化')
        self.move(self.win.width() - self.width(), 0)
        self.raise_()
        for b in (self.bmin, self.bmax, self.bclose):
            b.update()


def _interactive(w, win):
    """鼠标下面是不是可以点的控件（按钮、输入框、下拉框…）：是的话不当成标题栏"""
    while w is not None and w is not win:
        if isinstance(w, (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox)):
            return True
        if w.property('titlebarInteractive'):
            return True
        w = w.parentWidget()
    return False


class Frameless(QObject):
    """装在主窗口上：MainWindow.nativeEvent 把消息交给 native_event()。"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.enabled = sys.platform == 'win32'
        self.buttons = None
        if not self.enabled:
            return
        self.u = _user32()
        self.buttons = CaptionButtons(win)
        win.installEventFilter(self)

    def apply(self):
        """窗口第一次显示后调用：让系统重新计算边框（去掉标题栏）"""
        if not self.enabled:
            return
        hwnd = int(self.win.winId())
        # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        self.u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010)
        self.buttons.sync()

    def eventFilter(self, obj, ev):
        if obj is self.win and ev.type() in (QEvent.Resize, QEvent.WindowStateChange, QEvent.Show,
                                              QEvent.ActivationChange, QEvent.ChildAdded):
            if self.buttons is not None:
                self.buttons.sync()
        return False

    def _frame_px(self, hwnd):
        dpi = self.u.GetDpiForWindow(hwnd) or 96
        return self.u.GetSystemMetricsForDpi(33, dpi) + self.u.GetSystemMetricsForDpi(92, dpi)   # SM_CYFRAME + SM_CXPADDEDBORDER

    def native_event(self, event_type, message):
        if not self.enabled or event_type != b'windows_generic_MSG':
            return None
        msg = wt.MSG.from_address(int(message))
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
            top = params.rgrc[0].top
            self.u.DefWindowProcW(msg.hWnd, msg.message, msg.wParam, msg.lParam)     # 左右下的边框照常
            params.rgrc[0].top = top + (self._frame_px(msg.hWnd) if self.u.IsZoomed(msg.hWnd) else 0)   # 顶部不要标题栏
            return 0
        if msg.message == WM_NCHITTEST:
            res = self.u.DefWindowProcW(msg.hWnd, msg.message, msg.wParam, msg.lParam)
            if res in _BORDER_HITS:
                return res
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            origin = wt.POINT(0, 0)
            self.u.ClientToScreen(msg.hWnd, ctypes.byref(origin))
            px, py = x - origin.x, y - origin.y                        # 窗口内的物理像素
            dpr = self.win.devicePixelRatioF() or 1.0
            lx, ly = px / dpr, py / dpr
            maximized = bool(self.u.IsZoomed(msg.hWnd))
            if not maximized and 0 <= py < max(4, round(5 * dpr)):      # 最上面一条：拖动改高度
                if lx < 16:
                    return HTTOPLEFT
                if lx > self.win.width() - 16:
                    return HTTOPRIGHT
                return HTTOP
            if 0 <= ly < TITLE_H:
                child = self.win.childAt(int(lx), int(ly))
                if not _interactive(child, self.win):
                    return HTCAPTION
            return HTCLIENT
        return None
