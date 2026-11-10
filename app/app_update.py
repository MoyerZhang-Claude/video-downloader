"""检查软件本身有没有新版本（电脑版和安卓版共用）。

看 GitHub 上最新的发布（Release）。电脑版和安卓版不一定同时更新（比如 v2.1.1 只有安卓版变了），
所以不看发布的标签，而是看附件文件名里的版本号：
  LinkDownloader-2.1.1-android.apk / LinkDownloader-2.1.0-windows-setup.exe / LinkDownloader-2.1.0-windows.zip

发布说明的格式约定（README「发布流程」里也写了）：

    ## 更新内容
    ### 新功能
    - 一条一行，可以用 **粗体** 和 `行内代码`
    - [电脑版] 只适用于电脑版的条目加这个前缀
    - [安卓版] 只适用于安卓版的
    ### 改进
    - …
    ### 修复
    - …

更新窗口按「新功能 / 改进 / 修复」分组显示，各端只显示自己的和通用的条目。格式不符时退回显示普通列表。
"""
import hashlib
import json
import os
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
_PLATFORM_TAGS = {'windows': ('电脑版', 'Windows', 'PC', '桌面版'), 'android': ('安卓版', 'Android', '手机版')}
GROUPS = ('新功能', '改进', '修复')


def _norm(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or ''))


def parse_notes(body, platform):
    """发布说明 → [{'title': '新功能', 'items': ['…', …]}, …]，只留这个平台的和通用的条目。
    没有按约定分组时返回一组没有标题的普通列表；什么都没有返回 []。"""
    lines = [line.rstrip() for line in (body or '').replace('\r\n', '\n').split('\n')]
    # 只看「## 更新内容」这一节（没有这一节就看全文）
    start = next((i for i, line in enumerate(lines) if re.match(r'##\s*更新内容', line.strip())), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if re.match(r'##\s+\S', lines[i].strip())
                    and not lines[i].strip().startswith('###')), len(lines))
        lines = lines[start + 1:end]
    mine = _PLATFORM_TAGS.get(platform, ())
    others = [t for p, tags in _PLATFORM_TAGS.items() if p != platform for t in tags]
    groups, plain = [], []
    cur = None
    skipping = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'###\s*(.+?)\s*#*$', line)
        if m:
            cur = {'title': m.group(1).strip(), 'items': []}
            groups.append(cur)
            skipping = False
            continue
        if re.match(r'#{1,2}\s', line):
            continue
        m = re.match(r'(?:[-*+•]|\d+[.、])\s+(.*)$', line)
        bullet = bool(m)
        text = (m.group(1) if m else line).strip()
        tag = re.match(r'[\[【(（]\s*([^\]】)）]{1,12})\s*[\]】)）]\s*[:：]?\s*(.*)$', text)
        if not bullet and skipping:
            continue                                # 被跳过的那一条的续行
        skipping = bool(tag and tag.group(1).strip() in others)
        if skipping:
            continue                                # 只适用于另一端的
        if tag and tag.group(1).strip() in mine:
            text = tag.group(2).strip()
        if not text:
            continue
        target = cur['items'] if cur is not None else plain
        if bullet or not target:
            target.append(text)
        else:
            target[-1] += ' ' + text                # 上一条的续行
    groups = [g for g in groups if g['items']]
    if groups:
        order = {t: i for i, t in enumerate(GROUPS)}
        groups.sort(key=lambda g: order.get(g['title'], len(order)))
        if plain:
            groups.insert(0, {'title': '', 'items': plain})
        return groups
    return [{'title': '', 'items': plain}] if plain else []


def parse_release(data, platform, current=APP_VERSION):
    """从 GitHub API 返回的发布信息里找这个平台比 current 新的版本。没有新版返回 None。
    返回 {'version', 'page', 'download', 'notes', 'sections', 'published', 'size', 'digest', 'name'}：
    sections 是分好组的更新内容（见 parse_notes），published 发布时间（ISO 格式），
    size 安装包字节数，digest 安装包的 sha256（GitHub 给的，没有就是空），name 安装包文件名。"""
    rx = _ASSET[platform]
    best, asset = None, None
    for a in data.get('assets') or []:
        m = rx.match(a.get('name') or '')
        if not m:
            continue
        v = m.group(1)
        # 同一版本有安装包和压缩包时，优先给安装包
        if best is None or _norm(v) > _norm(best) or (v == best and a['name'].endswith('-setup.exe')):
            best, asset = v, a
    if not best or _norm(best) <= _norm(current):
        return None
    digest = asset.get('digest') or ''
    body = data.get('body') or ''
    return {'version': best, 'page': data.get('html_url') or RELEASES_PAGE,
            'download': asset.get('browser_download_url') or '', 'notes': body[:4000],
            'sections': parse_notes(body, platform), 'published': data.get('published_at') or '',
            'size': int(asset.get('size') or 0), 'digest': digest[7:].lower() if digest.startswith('sha256:') else '',
            'name': asset.get('name') or ''}


def check(platform, current=APP_VERSION, timeout=10):
    """联网检查。有新版返回 dict（见 parse_release），没有返回 None；网络错误会抛异常，调用方自己决定要不要提示。"""
    req = urllib.request.Request(_API, headers={'User-Agent': 'LinkDownloader-update-check',
                                                'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return parse_release(data, platform, current)


def download_installer(rel, folder, progress=None, cancelled=None, timeout=60):
    """电脑版应用内更新：把安装包下载到 folder，核对 GitHub 给出的 sha256，返回文件路径。
    没有校验值、或者校验不通过，一律抛异常并删掉文件——来路说不清的安装包绝不运行。"""
    if not rel.get('download') or not rel.get('name', '').endswith('-setup.exe'):
        raise RuntimeError('这个版本没有可以直接安装的安装包，请到发布页下载')
    if not rel.get('digest'):
        raise RuntimeError('GitHub 没有给出这个安装包的校验值，为安全起见不自动安装，请到发布页下载')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, re.sub(r'[^\w.\-]', '_', rel['name']))
    h = hashlib.sha256()
    req = urllib.request.Request(rel['download'], headers={'User-Agent': 'LinkDownloader-update'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(path, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0) or rel.get('size') or 0
            done = 0
            while chunk := r.read(256 * 1024):
                if cancelled and cancelled():
                    raise InterruptedError('已取消')
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
        if h.hexdigest().lower() != rel['digest']:
            raise RuntimeError('下载的安装包校验不通过（可能下载不完整或被改动过），没有运行它。请重试，或到发布页下载')
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path
