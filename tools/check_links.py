"""解析自检：把测试链接逐个解析一遍，再试着下载每个视频开头的一小段，看哪个网站坏了。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\check_links.py              # 默认读 test.txt
    .venv\\Scripts\\python.exe tools\\check_links.py 链接文件.txt
或者直接双击「测试解析.bat」。

- 链接文件里可以夹杂任何文字（分享文案、备注），只挑出里面的链接；网盘链接跳过（网盘用 pan\\cmd\\pandl-check 测）。
- 「解析」通过后还会真的去请求视频地址（只取开头 64 KB，不保存）：
  能解析但下不了（地址 403、签名失效）是网站改版时最常见的坏法，只看解析结果发现不了。
- 用的是电脑版同一套解析核心、设置和登录信息（B站登录、cookies 文件夹）。
- 全部通过退出码 0，有失败退出码 1。打包发布前跑一遍。
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PROBE_BYTES = 64 * 1024
TIMEOUT = 120


def _short(text, n):
    text = ' '.join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + '…'


def parse(core, parser, url):
    """返回 ('ok', ParsedVideo) / ('expand', 合集) / ('fail', 错误)。"""
    box, done = {}, threading.Event()

    def ok(_u, v):
        box.setdefault('r', ('ok', v))
        done.set()

    def err(_u, msg, raw):
        box.setdefault('r', ('fail', f'{msg} | {_short(raw, 200)}'))
        done.set()

    def expand(_u, col):
        box.setdefault('r', ('expand', col))
        done.set()

    parser.submit(url, ok, err, expand, probe=False)
    if not done.wait(TIMEOUT):
        return 'fail', f'超过 {TIMEOUT} 秒没有结果'
    return box['r']


def probe_stream(core, ydl, info, fid):
    """请求这个格式的视频地址开头一小段。返回 (成功, 说明)。"""
    from yt_dlp.networking import Request
    f = next((x for x in info.get('formats') or [] if x.get('format_id') == fid), None) or info
    headers = dict(f.get('http_headers') or info.get('http_headers') or {})
    proto = f.get('protocol') or ''
    url = f.get('url') or ''
    if f.get('fragments'):            # DASH 分片：取第一个分片
        frag = f['fragments'][0]
        url = frag.get('url') or (f.get('fragment_base_url') or '') + (frag.get('path') or '')
    if not url:
        return False, '没有下载地址'
    if not proto.startswith('m3u8'):
        headers['Range'] = f'bytes=0-{PROBE_BYTES - 1}'
    try:
        with ydl.urlopen(Request(url, headers=headers)) as r:
            data = r.read(PROBE_BYTES)
            status = getattr(r, 'status', 200)
    except Exception as e:  # noqa: BLE001
        return False, _short(e, 160)
    if proto.startswith('m3u8'):
        return (b'#EXTM3U' in data[:200]), f'm3u8 {status}'
    if not data:
        return False, f'HTTP {status}，内容为空'
    return True, f'HTTP {status} {len(data) // 1024} KB'


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'test.txt')
    if not os.path.isfile(path):
        print(f'找不到链接文件：{path}')
        return 2
    with open(path, encoding='utf-8') as f:
        text = f.read()

    from app import core, core_update, pan
    from app.config import Settings
    settings = Settings.load()
    urls = core.extract_urls(text)
    video_urls = [u for u in urls if not pan.is_pan_url(u)]
    skipped = len(urls) - len(video_urls)
    print(f'yt-dlp {core_update.current_version()} · {len(video_urls)} 个视频链接'
          + (f'（跳过 {skipped} 个网盘链接）' if skipped else ''))
    print()

    core.preload()
    parser = core.Parser(lambda: settings, lambda level, msg: None, workers=4)
    ydl = core.new_ydl(core.base_options(settings, core._Logger()))
    rows, failed = [], 0

    # 并行解析（4 个一组），再逐个测下载
    results = {}

    def run(u):
        t0 = time.time()
        results[u] = (*parse(core, parser, u), time.time() - t0)
    threads = [threading.Thread(target=run, args=(u,)) for u in video_urls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for u in video_urls:
        kind, val, secs = results[u]
        if kind == 'fail':
            failed += 1
            rows.append(('✗', '?', _short(u, 50), f'解析失败：{val}', secs))
            continue
        if kind == 'expand':
            rows.append(('✓', '合集', _short(val.get('title') or u, 30),
                         f'播放列表 / 合集，共 {len(val.get("entries") or [])} 个', secs))
            continue
        v = val
        if not v.options:
            failed += 1
            rows.append(('✗', v.platform, _short(v.title, 30), '解析成功但没有可下载的清晰度', secs))
            continue
        opt = v.options[0]             # 默认选中的清晰度（用户最常下的那个）
        notes, ok = [], True
        for fid, _size in opt.streams:
            good, msg = probe_stream(core, ydl, v.info, fid)
            ok &= good
            notes.append(('' if good else '下载失败 ') + msg)
        if not ok:
            failed += 1
        rows.append(('✓' if ok else '✗', v.platform, _short(v.title, 30),
                     f'{len(v.options)} 个清晰度，默认 {opt.label} · ' + ' / '.join(notes), secs))

    for mark, plat, title, note, secs in rows:
        print(f'{mark} [{plat}] {title}  ({secs:.1f}s)\n    {note}')
    print()
    print('全部通过' if not failed else f'{failed} 个失败：先试试「更新内核.bat」；抖音 / 快手是自研解析，要改 app/extractors.py')
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        code = main()
    except KeyboardInterrupt:
        code = 130
    sys.stdout.flush()
    os._exit(code)          # 解析线程池里可能还有卡住的请求，不等它们
