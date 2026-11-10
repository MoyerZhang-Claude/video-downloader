"""Windows 单实例锁及唤醒通知；进程退出后由系统自动释放。"""
import ctypes
import hashlib
import os
import sys
from ctypes import wintypes


def default_name():
    """锁的名字跟着数据目录走：同一份数据只能开一个软件。
    数据目录是系统默认的 %APPDATA%/VideoDownloader 时用固定的名字（和旧版本互相认得出）；
    用环境变量把 APPDATA 指到别处（便携使用、测试）时是另一份数据，可以和正常那份同时开。"""
    from .config import DATA
    base = 'VideoDownloader.App'
    if sys.platform != 'win32':
        return base
    try:
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x001a, None, 0, buf)       # CSIDL_APPDATA
        standard = os.path.join(buf.value, 'VideoDownloader')
    except Exception:  # noqa: BLE001
        return base
    if not buf.value or os.path.normcase(os.path.abspath(DATA)) == os.path.normcase(os.path.abspath(standard)):
        return base
    return base + '.' + hashlib.sha1(os.path.normcase(os.path.abspath(DATA)).encode('utf-8')).hexdigest()[:12]


class SingleInstance:
    def __init__(self, name=None):
        name = name or default_name()
        self._mutex = self._event = None
        self.primary = True
        if sys.platform != 'win32':
            return
        self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for name_, args, result in (
            ('CreateMutexW', [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('CreateEventW', [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ('SetEvent', [wintypes.HANDLE], wintypes.BOOL),
            ('ReleaseMutex', [wintypes.HANDLE], wintypes.BOOL),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ):
            fn = getattr(self._kernel, name_)
            fn.argtypes, fn.restype = args, result
        try:
            # 先创建通知事件，保证主进程尚在初始化时也不会丢失唤醒请求。
            self._event = self._kernel.CreateEventW(None, False, False, 'Local\\' + name + '.Activate')
            if not self._event:
                raise ctypes.WinError(ctypes.get_last_error())
            self._mutex = self._kernel.CreateMutexW(None, False, 'Local\\' + name + '.Mutex')
            if not self._mutex:
                raise ctypes.WinError(ctypes.get_last_error())
            result = self._kernel.WaitForSingleObject(self._mutex, 0)
            if result == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            self.primary = result in (0, 0x80)  # 获得锁，或上一个进程异常退出
            if not self.primary:
                ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)
                if not self._kernel.SetEvent(self._event):
                    raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def activation_requested(self):
        return bool(self._event and self._kernel.WaitForSingleObject(self._event, 0) == 0)

    def close(self):
        if self._mutex:
            if self.primary:
                self._kernel.ReleaseMutex(self._mutex)
            self._kernel.CloseHandle(self._mutex)
            self._mutex = None
        if self._event:
            self._kernel.CloseHandle(self._event)
            self._event = None
