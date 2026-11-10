import os
import sys

# pythonw / 打包后的窗口程序没有控制台，给标准输出一个去处，避免第三方库写入时报错
if sys.stdout is None or sys.stderr is None:
    _devnull = open(os.devnull, 'w', encoding='utf-8')
    sys.stdout = sys.stdout or _devnull
    sys.stderr = sys.stderr or _devnull

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if getattr(sys, 'frozen', False):
    # 打包版：如果在设置里更新过解析内核，优先使用 core_update 文件夹里的新版本
    from app import core_update
    core_update.install_override()


def selftest(out_path, urls):
    """--selftest 输出文件 链接...：不启动界面，解析链接并把清晰度列表写入文件（排查打包问题用）。"""
    import threading
    from app import core
    from app.config import Settings, ffmpeg_dir
    settings = Settings.load()
    from app import core_update
    core.preload()
    lines = [f'ffmpeg: {ffmpeg_dir()}', f'yt-dlp: {core_update.current_version()}']
    lock = threading.Lock()
    done = threading.Semaphore(0)

    def ok(url, v):
        with lock:
            lines.append(f'OK [{v.platform}] {v.title[:60]} | ' + ' ; '.join(o.label for o in v.options))
        done.release()

    def err(url, msg, raw):
        with lock:
            lines.append(f'FAIL {url} | {msg} | {raw[:300]}')
        done.release()

    parser = core.Parser(lambda: settings, lambda level, msg: None)
    for u in urls:
        parser.submit(u, ok, err, lambda url, subs: (lines.append(f'EXPAND {url} -> {len(subs)}'), done.release()))
    for _ in urls:
        done.acquire(timeout=300)
    # 解析 YouTube 时如果电脑上没有 JS 运行时，会先把 Deno 下载到数据目录：放在解析之后再看
    lines.insert(1, f'js runtimes: {core._js_runtimes()}')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--selftest':
        selftest(sys.argv[2], sys.argv[3:])
    else:
        from app.single_instance import SingleInstance
        instance = SingleInstance()
        try:
            if instance.primary:
                from app.gui import run
                run(instance)
        finally:
            instance.close()
