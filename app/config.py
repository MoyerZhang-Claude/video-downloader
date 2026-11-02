import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields


def app_root():
    """程序所在目录：源码运行时为项目根目录，打包后为 exe 所在目录。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = app_root()
CONFIG_PATH = os.path.join(ROOT, 'config.json')
DEFAULT_DOWNLOAD_DIR = r'D:\Claude\vedio download\Download'

FILENAME_TEMPLATES = {
    'title_quality': ('标题 [清晰度]', '%(title).80s [{quality}].%(ext)s'),
    'title': ('标题', '%(title).100s.%(ext)s'),
    'title_id': ('标题 [视频ID]', '%(title).80s [%(id)s].%(ext)s'),
    'uploader_title': ('作者 - 标题', '%(uploader).30s - %(title).80s.%(ext)s'),
}

CODEC_PREFERENCES = {
    'compat': '兼容优先（H.264，任何播放器都能放）',
    'quality': '体积优先（AV1/VP9/H.265，同画质文件更小）',
}

COOKIE_MODES = {
    'none': '不使用',
    'browser': '从浏览器读取',
    'file': 'cookies.txt 文件',
}

SUBFOLDER_MODES = {
    'none': '不分文件夹（全部放在下载目录）',
    'platform': '按平台（如 Download\\B站\\）',
    'platform_uploader': '按平台 + 作者（如 Download\\B站\\UP主名\\）',
}

SUB_LANG_MODES = {
    'zh_orig': '中文 + 原语言（推荐，外语视频同时得到中文和原文字幕）',
    'zh': '只要中文（没有中文时用原语言）',
    'all': '全部人工字幕',
}

BROWSERS = ['firefox', 'edge', 'chrome', 'brave', 'opera', 'vivaldi', 'chromium']


@dataclass
class Settings:
    download_dir: str = DEFAULT_DOWNLOAD_DIR
    max_concurrent: int = 3
    fragment_threads: int = 4
    filename_template: str = 'title_quality'
    codec_preference: str = 'compat'
    proxy: str = ''               # 空 = 跟随系统代理
    cookie_mode: str = 'none'
    cookie_browser: str = 'firefox'
    cookie_file: str = ''
    auto_parse_clipboard: bool = True
    save_cover: bool = False      # 下载视频时同时保存封面
    embed_cover: bool = True      # 把封面写进视频/音频文件（资源管理器、播放器里显示封面）
    subfolder: str = 'none'       # 自动分文件夹
    notify_done: bool = True      # 下载完成时系统通知
    notify_sound: bool = True     # 全部下载完成时提示音
    open_dir_when_done: bool = False
    save_danmaku: bool = True     # B站弹幕转 ASS
    burn_danmaku: bool = True     # 另外生成弹幕画进视频的高帧率「弹幕版」，任何播放器都流畅
    danmaku_fps: int = 60
    keep_original: bool = False   # 生成弹幕版后是否保留原视频和外挂弹幕
    close_to_tray: bool = True    # 关闭窗口时缩小到托盘，下载继续
    tray_hint_shown: bool = False
    hotkey: str = 'Ctrl+Alt+D'    # 全局快捷键：解析剪贴板里的链接
    check_core_update: bool = True
    save_subs: bool = True        # 字幕（B站 CC / YouTube 等）
    sub_langs: str = 'zh_orig'
    theme: str = 'system'         # 外观：system 跟随系统 / light / dark
    nickname: str = ''            # 首页问候语里的称呼
    sidebar_collapsed: bool = False
    preset: str = 'pick'          # 输入框下方的清晰度预设
    window_geometry: str = ''
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls):
        data = {}
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            pass
        names = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in names})
        if s.filename_template not in FILENAME_TEMPLATES:
            s.filename_template = 'title_quality'
        if s.codec_preference not in CODEC_PREFERENCES:
            s.codec_preference = 'compat'
        if s.sub_langs not in SUB_LANG_MODES:
            s.sub_langs = 'zh_orig'
        if s.subfolder not in SUBFOLDER_MODES:
            s.subfolder = 'none'
        if s.cookie_mode not in COOKIE_MODES:
            s.cookie_mode = 'none'
        if s.theme not in ('system', 'light', 'dark'):
            s.theme = 'system'
        s.max_concurrent = min(max(int(s.max_concurrent or 3), 1), 10)
        s.fragment_threads = min(max(int(s.fragment_threads or 4), 1), 16)
        return s

    def save(self):
        tmp = CONFIG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def ffmpeg_dir():
    for d in (os.path.join(ROOT, 'ffmpeg'), os.path.join(getattr(sys, '_MEIPASS', ROOT), 'ffmpeg')):
        if os.path.isfile(os.path.join(d, 'ffmpeg.exe')):
            return d
    return None
