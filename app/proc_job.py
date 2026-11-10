"""子进程跟着软件一起退出（Windows Job Object）。

Windows 上父进程退出不会带走子进程：退出软件时如果正在合并音视频、生成弹幕版，ffmpeg 会留在后台继续跑，
占着 CPU 和文件，用户还看不到；软件崩溃时网盘引擎、内置浏览器也会留下。

做法：启动时建一个「关闭时结束所有成员」的 Job，之后用 subprocess 启动的每个子进程都放进去
（ffmpeg / ffprobe、yt-dlp 调用的 Deno、网盘引擎、内置浏览器……它们再启动的进程也自动在里面）。
软件不管怎么结束（正常退出、崩溃、被任务管理器结束），系统关掉 Job 句柄时会把里面的进程全部结束。

软件自己不进 Job：用 os.startfile 打开的播放器、资源管理器是用户要留着用的，不能跟着软件一起关。
需要留下来的子进程（重启软件时的新进程）用 `with proc_job.detached():` 启动。
"""
import ctypes
import subprocess
import sys
import threading
from contextlib import contextmanager
from ctypes import wintypes

_job = None
_local = threading.local()

_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                               'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


def install():
    """程序启动时调用一次。返回是否生效（不是 Windows、或系统不允许时返回 False，软件照常运行）。"""
    global _job
    if _job is not None:
        return True
    if sys.platform != 'win32':
        return False
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return False
    except (OSError, AttributeError):
        return False
    _job = job          # 句柄一直留到进程结束：进程一没，系统关掉它，Job 里的子进程随之结束

    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if getattr(_local, 'detached', 0):
            return
        try:
            k32.AssignProcessToJobObject(_job, int(self._handle))
        except Exception:  # noqa: BLE001 - 放不进去就算了，不影响子进程运行
            pass

    subprocess.Popen.__init__ = init
    return True


@contextmanager
def detached():
    """在这个 with 里启动的子进程不进 Job，软件退出后继续运行。"""
    _local.detached = getattr(_local, 'detached', 0) + 1
    try:
        yield
    finally:
        _local.detached -= 1
