"""检查软件本身有没有新版本（电脑版和安卓版共用）。

看 GitHub 上最新的发布（Release）。电脑版和安卓版不一定同时更新（比如 v2.1.1 只有安卓版变了），
所以不看发布的标签，而是看附件文件名里的版本号：
  LinkDownloader-2.1.1-android.apk / LinkDownloader-2.1.0-windows-setup.exe / LinkDownloader-2.1.0-windows.zip
"""
import json
import re
import urllib.request

from . import APP_VERSION

REPO = 'MoyerZhang-Claude/video-downloader'
RELEASES_PAGE = f'https://github.com/{REPO}/releases/latest'
_API = f'https://api.github.com/repos/{REPO}/releases/latest'

_ASSET = {
    'windows': re.compile(r'^LinkDownloader-(\d+(?:\.\d+)*)-windows(?:-setup\.exe|\.zip)$'),
    'android': re.compile(r'^LinkDownloader-(\d+(?:\.\d+)*)-android\.apk$'),
}


def _norm(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or ''))


def parse_release(data, platform, current=APP_VERSION):
    """从 GitHub API 返回的发布信息里找这个平台比 current 新的版本。
    返回 {'version', 'page', 'download', 'notes'}；没有新版返回 None。"""
    rx = _ASSET[platform]
    best, url = None, None
    for a in data.get('assets') or []:
        m = rx.match(a.get('name') or '')
        if not m:
            continue
        v = m.group(1)
        # 同一版本有安装包和压缩包时，优先给安装包
        if best is None or _norm(v) > _norm(best) or (v == best and a['name'].endswith('-setup.exe')):
            best, url = v, a.get('browser_download_url')
    if not best or _norm(best) <= _norm(current):
        return None
    return {'version': best, 'page': data.get('html_url') or RELEASES_PAGE, 'download': url or '',
            'notes': (data.get('body') or '')[:4000]}


def check(platform, current=APP_VERSION, timeout=10):
    """联网检查。有新版返回 dict（见 parse_release），没有返回 None；网络错误会抛异常，调用方自己决定要不要提示。"""
    req = urllib.request.Request(_API, headers={'User-Agent': 'LinkDownloader-update-check',
                                                'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return parse_release(data, platform, current)
