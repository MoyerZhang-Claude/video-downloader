"""Windows 全局快捷键（RegisterHotKey）：软件在后台或最小化到托盘时也能响应。"""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_MODS = {'ctrl': MOD_CONTROL, 'control': MOD_CONTROL, 'alt': MOD_ALT, 'shift': MOD_SHIFT, 'win': MOD_WIN}
HOTKEY_ID = 0xB17E


def parse(seq):
    """'Ctrl+Alt+D' → (修饰键, 虚拟键码)；格式不对返回 None。"""
    parts = [p.strip().lower() for p in (seq or '').replace(' ', '').split('+') if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods |= _MODS[p]
    key = parts[-1].upper()
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key)
    elif key.startswith('F') and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        return None
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return None          # 至少要有 Ctrl / Alt / Win，避免和普通打字冲突
    return mods, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, emitter):
        super().__init__()
        self.emitter = emitter

    def nativeEventFilter(self, event_type, message):
        if event_type in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.emitter.activated.emit()
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    activated = Signal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.current = ''
        self._filter = None
        if sys.platform == 'win32':
            self._filter = _Filter(self)
            app.installNativeEventFilter(self._filter)

    def register(self, seq):
        """注册（会先取消旧的）。返回 True / False（快捷键格式不对或已被其它程序占用）。"""
        self.unregister()
        if sys.platform != 'win32' or not seq:
            return False
        parsed = parse(seq)
        if not parsed:
            return False
        mods, vk = parsed
        if ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
            self.current = seq
            return True
        return False

    def unregister(self):
        if sys.platform == 'win32' and self.current:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
        self.current = ''
