import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields


def app_root():
    """程序所在目录：源码运行时为项目根目录，打包后为 exe 所在目录。只放程序文件（ffmpeg、网盘引擎等），不写数据。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_root():
    """用户数据目录 %APPDATA%\\VideoDownloader：设置、下载历史、未完成的任务、网站登录信息、头像、日志、内核更新。
    装在 Program Files 里也能写；卸载 / 升级软件不会丢。B站登录和网盘账号本来就在这里。"""
    return os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'VideoDownloader')


ROOT = app_root()
DATA = data_root()
CONFIG_PATH = os.path.join(DATA, 'config.json')
DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', '链接解析下载器')

# 2.1 之前这些数据放在程序文件夹里
LEGACY_ITEMS = ('config.json', 'history.db', 'history.db-wal', 'history.db-shm', 'tasks.json', 'cookies', 'avatar')


def has_legacy_data(folder):
    return any(os.path.isfile(os.path.join(folder, n)) for n in ('config.json', 'history.db'))


PENDING = os.path.join(DATA, 'import_pending')


def import_legacy(src, overwrite=True, dest=DATA):
    """把旧版程序文件夹里的数据复制到数据目录（原文件保留）。返回复制了哪些。"""
    os.makedirs(dest, exist_ok=True)
    done = []
    for name in LEGACY_ITEMS:
        s, d = os.path.join(src, name), os.path.join(dest, name)
        if not os.path.exists(s) or (os.path.exists(d) and not overwrite):
            continue
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
        done.append(name)
    if 'history.db' in done:
        # 旧文件夹里没有 -wal / -shm 时，数据目录里残留的旧 -wal 会和新库对不上
        for ext in ('-wal', '-shm'):
            if not os.path.exists(os.path.join(src, 'history.db' + ext)):
                try:
                    os.remove(os.path.join(dest, 'history.db' + ext))
                except OSError:
                    pass
    return done


def _auto_migrate():
    """第一次用新版：数据目录里还没有设置和历史，就从旧版的程序文件夹搬过来（取最近用过的那份，原文件保留）。"""
    if has_legacy_data(DATA):
        return
    cands = [ROOT]
    if not getattr(sys, 'frozen', False):
        cands += [os.path.join(ROOT, 'dist', '链接解析下载器'), os.path.join(ROOT, 'dist', '视频解析下载器')]
    cands = [c for c in cands if has_legacy_data(c)]
    if not cands:
        return

    def mtime(c):
        return max((os.path.getmtime(os.path.join(c, n)) for n in ('history.db', 'config.json')
                    if os.path.isfile(os.path.join(c, n))), default=0)
    src = max(cands, key=mtime)
    import_legacy(src, overwrite=False)
    try:
        with open(os.path.join(DATA, 'migrated_from.txt'), 'w', encoding='utf-8') as f:
            f.write(src)
    except OSError:
        pass


def _apply_pending():
    """设置里「导入旧版数据」放进来的文件：启动时（还没打开数据库）换上。"""
    if not os.path.isdir(PENDING):
        return
    import_legacy(PENDING, overwrite=True)
    shutil.rmtree(PENDING, ignore_errors=True)


try:
    os.makedirs(DATA, exist_ok=True)
    _apply_pending()
    _auto_migrate()
except OSError:
    pass

FILENAME_TEMPLATES = {
    'title_quality': ('标题 [清晰度]', '%(title).80s [{quality}].%(ext)s'),
    'title': ('标题', '%(title).100s.%(ext)s'),
    'title_id': ('标题 [视频ID]', '%(title).80s [%(id)s].%(ext)s'),
    'uploader_title': ('作者 - 标题', '%(uploader).30s - %(title).80s.%(ext)s'),
}

CODEC_PREFERENCES = {
    'compat': '兼容优先（H.264，任何播放器都能放）',
    'quality': '体积优先（AV1/VP9/H.265，同画质文件更小）',
    'best': '画质优先（同一清晰度选码率最高的，文件最大）',
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
    check_app_update: bool = True   # 启动时检查软件新版本（GitHub 发布页）
    skip_app_version: str = ''      # 用户选了「跳过这个版本」的版本号
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
