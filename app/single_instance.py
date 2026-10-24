"""Windows 单实例锁及唤醒通知；进程退出后由系统自动释放。"""
import ctypes
import sys
from ctypes import wintypes


class SingleInstance:
    def __init__(self, name='VideoDownloader.App'):
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
