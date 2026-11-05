"""B站弹幕 XML → ASS 字幕（滚动 / 顶部 / 底部弹幕，自动分配轨道避免重叠）。

生成的 .ass 与视频同名放在一起，PotPlayer、MPC-HC、VLC、mpv 等播放器会自动加载，离线观看也有弹幕。
"""
import re
import xml.etree.ElementTree as ET

SCROLL_SECONDS = 8.0      # 滚动弹幕从右到左用时
FIXED_SECONDS = 4.0       # 顶部/底部弹幕停留时间
SCROLL_AREA = 0.75        # 滚动弹幕只占屏幕上方 3/4，留出字幕区域
OPACITY = 0.82


def _ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'


def _text_width(text, size):
    wide = sum(1 for ch in text if ord(ch) > 0x2E80)
    return size * (wide + (len(text) - wide) * 0.56)


def _escape(text):
    text = text.replace('\\', '＼').replace('{', '｛').replace('}', '｝')
    return re.sub(r'\s*[\r\n]+\s*', r'\\N', text.strip())


def parse_xml(data):
    """返回 [(出现时间秒, 模式, 字号, 颜色RGB, 文本)]，按时间排序。"""
    if isinstance(data, bytes):
        data = data.decode('utf-8', 'replace')
    data = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', data)   # 去掉 XML 非法控制字符
    root = ET.fromstring(data)
    items = []
    for d in root.iter('d'):
        p = (d.get('p') or '').split(',')
        text = (d.text or '').strip()
        if len(p) < 4 or not text:
            continue
        try:
            t, mode, size, color = float(p[0]), int(p[1]), int(p[2]), int(p[3])
        except ValueError:
            continue
        if mode in (7, 8, 9):      # 高级弹幕 / 代码弹幕，普通播放器无法还原
            continue
        items.append((t, mode, size, color, text))
    items.sort(key=lambda x: x[0])
    return items


def to_ass(items, width=1920, height=1080, font='Microsoft YaHei', base_size=None):
    width, height = int(width or 1920), int(height or 1080)
    base = base_size or _base_size(width, height)
    alpha = f'{round((1 - OPACITY) * 255):02X}'
    header = f"""[Script Info]
; 从 B站弹幕生成
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Danmaku,{font},{base},&H{alpha}FFFFFF,&H{alpha}FFFFFF,&H{alpha}000000,&H00000000,1,0,0,0,100,100,0,0,1,1.6,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    row_h = base + 6
    lines = []
    for t, kind, fs, color, raw, w, row in _place(items, width, height, base):
        text = _escape(raw)
        color_tag = '' if color == 0xFFFFFF else \
            f'\\c&H{color & 0xFF:02X}{(color >> 8) & 0xFF:02X}{(color >> 16) & 0xFF:02X}&'
        size_tag = '' if fs == base else f'\\fs{fs}'
        if kind in ('top', 'bottom'):
            if kind == 'top':
                pos = f'\\an8\\pos({width // 2},{row * row_h})'
            else:
                pos = f'\\an2\\pos({width // 2},{height - row * row_h})'
            lines.append(f'Dialogue: 2,{_ass_time(t)},{_ass_time(t + FIXED_SECONDS)},Danmaku,,0,0,0,,'
                         f'{{{pos}{color_tag}{size_tag}}}{text}')
            continue
        y = row * row_h
        if kind == 'rscroll':       # 逆向弹幕：从左往右
            move = f'\\move({-w:.0f},{y},{width},{y})'
        else:
            move = f'\\move({width},{y},{-w:.0f},{y})'
        lines.append(f'Dialogue: 1,{_ass_time(t)},{_ass_time(t + SCROLL_SECONDS)},Danmaku,,0,0,0,,'
                     f'{{{move}{color_tag}{size_tag}}}{text}')
    return header + '\n'.join(lines) + '\n'


def _base_size(width, height):
    return round(min(width, height) / 1080 * 46)


def _place(items, width, height, base):
    """给每条弹幕分配轨道（自动避让、满屏丢弃）。to_ass 和手机端的 layout 共用这套规则。

    产出 (出现时间, 类型, 字号, 颜色, 原文, 估算宽度, 行号)；类型：scroll / rscroll(逆向) / top / bottom
    """
    row_h = base + 6
    scroll_rows = max(1, int(height * SCROLL_AREA // row_h))
    fixed_rows = max(1, int(height // row_h))
    scroll_lane = [None] * scroll_rows       # (开始时间, 宽度, 速度)
    top_lane = [0.0] * fixed_rows            # 占用到的时间
    bottom_lane = [0.0] * fixed_rows
    for t, mode, size, color, raw in items:
        fs = max(12, round(base * size / 25))
        w = _text_width(raw, fs)
        if mode in (4, 5):
            lane = top_lane if mode == 5 else bottom_lane
            row = next((i for i, busy in enumerate(lane) if busy <= t), None)
            if row is None:
                continue                     # 满屏时丢弃，避免糊成一片
            lane[row] = t + FIXED_SECONDS
            yield t, ('top' if mode == 5 else 'bottom'), fs, color, raw, w, row
            continue
        speed = (width + w) / SCROLL_SECONDS
        row = None
        for i, prev in enumerate(scroll_lane):
            if prev is None:
                row = i
                break
            p_start, p_w, p_speed = prev
            entered = t >= p_start + p_w / p_speed                         # 前一条尾巴已经进屏
            no_catch = t + width / speed >= p_start + SCROLL_SECONDS       # 追不上前一条
            if entered and no_catch:
                row = i
                break
        if row is None:
            continue
        scroll_lane[row] = (t, w, speed)
        yield t, ('rscroll' if mode == 6 else 'scroll'), fs, color, raw, w, row


def layout(xml_data, video_width, video_height):
    """手机端烧录弹幕用：排好位置的弹幕（坐标基于 canvas 画布，绘制时再按视频实际大小缩放）。

    items 每项：[出现时间, 类型(0 滚动 1 逆向 2 顶部 3 底部), 字号, 颜色, 估算宽度, 行号, 文本]
    """
    width, height = canvas_size(video_width, video_height)
    base = _base_size(width, height)
    kinds = {'scroll': 0, 'rscroll': 1, 'top': 2, 'bottom': 3}
    placed = [[round(t, 3), kinds[k], fs, color, round(w, 1), row, raw]
              for t, k, fs, color, raw, w, row in _place(parse_xml(xml_data), width, height, base)]
    return {'canvas': [width, height], 'base': base, 'row_h': base + 6, 'scroll_seconds': SCROLL_SECONDS,
            'fixed_seconds': FIXED_SECONDS, 'opacity': OPACITY, 'items': placed}


def convert_file(xml_path, ass_path, width=1920, height=1080, section=None):
    with open(xml_path, 'rb') as f:
        items = parse_xml(f.read())
    if section:   # 片段：只保留这段时间内的弹幕，并把时间平移到从 0 开始
        start, end = section
        items = [(t - start, *rest) for t, *rest in items if start <= t < end]
    with open(ass_path, 'w', encoding='utf-8-sig') as f:
        f.write(to_ass(items, *canvas_size(width, height)))
    return len(items)


def write_xml_section(src, dst, section):
    """把弹幕 XML 截取到片段时间内，并把时间平移到从 0 开始（保留 B站原始格式，播放器可直接读取）。"""
    with open(src, 'rb') as f:
        data = f.read().decode('utf-8', 'replace')
    data = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', data)
    root = ET.fromstring(data)
    start, end = section
    for d in list(root.iter('d')):
        p = (d.get('p') or '').split(',')
        try:
            t = float(p[0])
        except (ValueError, IndexError):
            continue
        if not start <= t < end:
            root.remove(d)
            continue
        p[0] = f'{t - start:.5f}'
        d.set('p', ','.join(p))
    ET.ElementTree(root).write(dst, encoding='utf-8', xml_declaration=True)


def canvas_size(width, height):
    """弹幕画布：至少 1080 高。小分辨率视频（如 360P）也用高精度画布，移动步长更细、更平滑。"""
    width, height = int(width or 1920), int(height or 1080)
    if width <= 0 or height <= 0:
        return 1920, 1080
    if width >= height:
        return round(1080 * width / height / 2) * 2, 1080
    return 1080, round(1080 * height / width / 2) * 2     # 竖屏：画布也竖着，弹幕不会被拉伸


# ---------------------------------------------------------------- 弹幕版视频（烧录 + 高帧率）
#
# 外挂弹幕只能跟着视频帧刷新（30 帧视频每秒只动 30 次，看起来一跳一跳、有残影）。
# 把弹幕按 60/120 帧直接画进视频，任何播放器里都和网页版一样顺滑。

import os            # noqa: E402
import shutil        # noqa: E402
import subprocess    # noqa: E402
import tempfile      # noqa: E402
import threading     # noqa: E402

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# 实测 x264 比 Intel 硬件编码更快、文件更小，且任何电脑都可用。
# 满屏滚动文字很费码率：不限的话 60 帧版本会是原视频的 10 倍大，这里按原码率的 3.5 倍封顶，文字依然清晰。


def _encoder_args(ffprobe, video, fps):
    kbps = 0
    try:
        out = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                              'stream=bit_rate:format=bit_rate', '-of', 'default=nw=1:nk=1', video],
                             capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW).stdout
        kbps = max((int(x) for x in out.split() if x.isdigit()), default=0) // 1000
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    cap = min(16000, max(3000, int((kbps or 1700) * 3.5 * (1.4 if fps > 60 else 1.0))))
    return ['-c:v:0', 'libx264', '-preset', 'faster', '-crf', '23', '-maxrate', f'{cap}k', '-bufsize', f'{cap * 2}k',
            '-pix_fmt:v:0', 'yuv420p']


def _has_cover(ffprobe, video):
    try:
        out = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v', '-show_entries',
                              'stream_disposition=attached_pic', '-of', 'csv=p=0', video],
                             capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW).stdout
        return '1' in out.split()
    except (OSError, subprocess.SubprocessError):
        return False


def burn(ffmpeg, video, ass_file, out_path, fps=60, duration=None, on_progress=None, should_stop=None):
    """把 ASS 弹幕按 fps 帧率画进视频；被取消时抛 InterruptedError。"""
    work = tempfile.mkdtemp(prefix='danmaku_')
    try:
        # ass 滤镜对路径里的盘符冒号、方括号等很敏感：复制成简单文件名，并在临时目录里运行
        shutil.copyfile(ass_file, os.path.join(work, 'dm.ass'))
        tmp_out = os.path.join(work, 'out.mp4')
        has_cover = _has_cover(os.path.join(os.path.dirname(ffmpeg), 'ffprobe.exe'), os.path.abspath(video))
        # 弹幕只画到主画面上；原视频里嵌的封面（attached_pic）原样带过去，资源管理器里照样显示封面
        maps = ['-map', '0:v:0', '-map', '0:a?'] + (
            ['-map', '0:v:1', '-c:v:1', 'copy', '-disposition:v:1', 'attached_pic'] if has_cover else [])
        cmd = [ffmpeg, '-hide_banner', '-v', 'error', '-y', '-i', os.path.abspath(video), *maps,
               '-filter:v:0', f'fps={fps},ass=dm.ass',
               *_encoder_args(os.path.join(os.path.dirname(ffmpeg), 'ffprobe.exe'), os.path.abspath(video), fps),
               '-c:a', 'copy', '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', tmp_out]
        proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=_NO_WINDOW, text=True, encoding='utf-8', errors='replace')
        err_lines = []
        threading.Thread(target=lambda: err_lines.extend(proc.stderr), daemon=True).start()
        for line in proc.stdout:
            if should_stop and should_stop():
                proc.kill()
                proc.wait()
                raise InterruptedError('cancelled')
            if line.startswith('out_time_us=') and duration and on_progress:
                try:
                    on_progress(min(1.0, int(line.split('=', 1)[1]) / 1e6 / duration))
                except ValueError:
                    pass
        proc.wait()
        if proc.returncode != 0 or not os.path.exists(tmp_out):
            raise RuntimeError(''.join(err_lines[-5:]).strip() or f'ffmpeg 退出码 {proc.returncode}')
        shutil.move(tmp_out, out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
