"""yt-dlp 内置提取器不支持（或需要 cookies）的平台：抖音、快手。

作为普通的 yt-dlp InfoExtractor 实现，下载、进度、重试等流程与其它站点完全一致。
"""
import re
import struct

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import (
    ExtractorError,
    int_or_none,
    traverse_obj,
    url_or_none,
)

MOBILE_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 '
             '(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1')
ANDROID_UA = ('Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36')
DESKTOP_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')

_CODEC_NAMES = {b'avc1': 'h264', b'avc3': 'h264', b'hvc1': 'h265', b'hev1': 'h265',
                b'av01': 'av1', b'vp09': 'vp9'}


def probe_mp4_header(data):
    """从 MP4 文件开头的若干字节中解析视频轨的宽、高和编码（moov 前置时有效）。"""
    width = height = codec = None
    pos = 0
    while True:
        pos = data.find(b'tkhd', pos)
        if pos < 4 or pos + 92 > len(data):
            break
        base = pos - 4  # box 起点
        version = data[base + 8]
        off = base + 8 + 4 + (32 if version == 1 else 20) + 16
        if off + 44 > len(data):
            break
        matrix_a, matrix_b = struct.unpack('>ii', data[off:off + 8])
        w, h = struct.unpack('>II', data[off + 36:off + 44])
        w, h = w >> 16, h >> 16
        if w and h:
            if matrix_a == 0 and matrix_b != 0:  # 旋转 90/270 度
                w, h = h, w
            width, height = w, h
            break
        pos += 4
    pos = 0
    while True:
        pos = data.find(b'stsd', pos)
        if pos < 4 or pos + 20 > len(data):
            break
        codec = _CODEC_NAMES.get(data[pos + 16:pos + 20])
        if codec:
            break
        pos += 4
    return width, height, codec


class DouyinShareIE(InfoExtractor):
    IE_NAME = 'douyin:share'
    IE_DESC = '抖音（分享页解析，免 cookies）'
    _VALID_URL = r'''(?x)https?://(?:
        v\.douyin\.com/[\w-]+|
        (?:www\.|m\.)?(?:ies)?douyin\.com/(?:share/)?(?:video|note|slides)/\d+|
        (?:www\.)?douyin\.com/[^?#]*\?(?:[^#]*&)?modal_id=\d+
    )'''
    _HEADERS = {'User-Agent': MOBILE_UA, 'Referer': 'https://www.douyin.com/'}
    # 由低到高探测；ratio=default 返回作者上传的原始文件
    _RATIOS = ('540p', '720p', '1080p', 'default')

    def _real_extract(self, url):
        aweme_id = self._search_regex(
            (r'/(?:video|note|slides)/(\d+)', r'[?&]modal_id=(\d+)'), url, 'id', default=None)
        if not aweme_id:
            urlh = self._request_webpage(url, None, '解析短链接', headers=self._HEADERS)
            aweme_id = self._search_regex(
                (r'/(?:video|note|slides)/(\d+)', r'[?&]modal_id=(\d+)'), urlh.url, 'id')

        item = self._fetch_item(aweme_id)
        title = (item.get('desc') or '').strip() or f'抖音_{aweme_id}'
        info = {
            'id': aweme_id,
            'title': title,
            'description': item.get('desc'),
            'uploader': traverse_obj(item, ('author', 'nickname')),
            'uploader_id': traverse_obj(item, ('author', ('unique_id', 'short_id')), get_all=False),
            'timestamp': int_or_none(item.get('create_time')),
            'like_count': int_or_none(traverse_obj(item, ('statistics', 'digg_count'))),
            'comment_count': int_or_none(traverse_obj(item, ('statistics', 'comment_count'))),
            'webpage_url': f'https://www.douyin.com/video/{aweme_id}',
        }

        images = item.get('images')
        if images:
            entries = []
            for idx, img in enumerate(images, 1):
                img_url = traverse_obj(img, ('url_list', -1, {url_or_none}))
                if img_url:
                    ext = 'webp' if '.webp' in img_url else 'jpg'
                    entries.append({
                        **info, 'id': f'{aweme_id}_{idx}', 'title': f'{title}_{idx}',
                        'url': img_url, 'ext': ext, 'vcodec': 'none', 'acodec': 'none',
                        'format_note': '图片', 'http_headers': self._HEADERS,
                        'width': int_or_none(img.get('width')), 'height': int_or_none(img.get('height')),
                    })
            if not entries:
                raise ExtractorError('图文作品中没有找到图片', expected=True)
            return self.playlist_result(entries, aweme_id, title)

        video = item.get('video') or {}
        info.update({
            'duration': (int_or_none(video.get('duration')) or 0) / 1000 or None,
            'thumbnail': traverse_obj(video, ('cover', 'url_list', 0, {url_or_none})),
            'formats': self._extract_formats(aweme_id, video),
        })
        return info

    def _fetch_item(self, aweme_id):
        page_url = f'https://www.iesdouyin.com/share/video/{aweme_id}/'
        # 分享页的反爬脚本会写入 gfkadpd，并依赖服务端下发的 ttwid
        self._set_cookie('.iesdouyin.com', 'gfkadpd', '1243,16720')
        last_res = None
        for attempt in range(3):
            webpage = self._download_webpage(
                page_url, aweme_id, f'下载分享页 ({attempt + 1})', headers=self._HEADERS)
            data = self._search_json(
                r'window\._ROUTER_DATA\s*=', webpage, 'router data', aweme_id, default={})
            res = traverse_obj(data, ('loaderData', ..., 'videoInfoRes', {dict}), get_all=False)
            if res:
                last_res = res
                item = traverse_obj(res, ('item_list', 0, {dict}))
                if item:
                    return item
                break
        reason = traverse_obj(last_res, ('filter_list', 0, ('detail_msg', 'filter_reason')), get_all=False)
        raise ExtractorError(f'抖音未返回作品数据{"：" + reason if reason else "（可能已删除或设为私密）"}',
                             expected=True)

    def _extract_formats(self, aweme_id, video):
        uri = traverse_obj(video, ('play_addr', 'uri'))
        src_w, src_h = int_or_none(video.get('width')), int_or_none(video.get('height'))
        if not uri:
            play = traverse_obj(video, ('play_addr', 'url_list', 0, {url_or_none}))
            if not play:
                raise ExtractorError('没有找到视频地址', expected=True)
            return [{'url': play.replace('/playwm/', '/play/'), 'format_id': 'default', 'ext': 'mp4',
                     'width': src_w, 'height': src_h, 'http_headers': self._HEADERS}]

        formats, seen_sizes = [], {}
        for ratio in self._RATIOS:
            play_url = f'https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio={ratio}&line=0'
            urlh = self._request_webpage(
                play_url, aweme_id, f'探测清晰度 {ratio}', fatal=False,
                headers={**self._HEADERS, 'Range': 'bytes=0-65535'})
            if not urlh:
                continue
            size = int_or_none(self._search_regex(
                r'/(\d+)$', urlh.headers.get('Content-Range') or '', 'size', default=None))
            head = urlh.read()
            width, height, vcodec = probe_mp4_header(head)
            if size and size in seen_sizes:
                # 同一个文件（请求的清晰度不存在时服务器会回落到别的档位），原画优先标注
                if ratio == 'default':
                    seen_sizes[size]['format_note'] = '原画'
                    seen_sizes[size]['format_id'] = 'original'
                continue
            if ratio == 'default' and not (width and height):
                width, height = src_w, src_h
            fmt = {
                'url': play_url,
                'format_id': 'original' if ratio == 'default' else ratio,
                'format_note': '原画' if ratio == 'default' else None,
                'ext': 'mp4',
                'width': width,
                'height': height,
                'vcodec': vcodec,
                'acodec': 'aac',
                'filesize': size,
                'http_headers': self._HEADERS,
            }
            formats.append(fmt)
            if size:
                seen_sizes[size] = fmt
        if not formats:
            raise ExtractorError('无法获取视频地址', expected=True)
        for f in formats:
            f['quality'] = (min(f['width'], f['height']) if f.get('width') and f.get('height') else 0)
        return formats


class KuaishouShareIE(InfoExtractor):
    IE_NAME = 'kuaishou:share'
    IE_DESC = '快手'
    _VALID_URL = r'''(?x)https?://(?:
        (?:v\.kuaishou\.com|v\.kuaishouapp\.com)/[\w-]+|
        (?:www\.)?kuaishou\.com/(?:f/[\w-]+|short-video/\w+)|
        (?:v\.m\.chenzhongtech\.com|m\.gifshow\.com|c\.kuaishou\.com|v\.m\.kuaishou\.com)/fw/photo/\w+
    )'''
    _HEADERS = {'User-Agent': DESKTOP_UA, 'Referer': 'https://www.kuaishou.com/'}

    def _real_extract(self, url):
        page_url = url
        if '/short-video/' not in url:
            urlh = self._request_webpage(url, None, '解析短链接', headers=self._HEADERS)
            page_url = urlh.url
        photo_id = self._search_regex(
            (r'/short-video/(\w+)', r'/photo/(\w+)', r'[?&]photoId=(\w+)'), page_url, 'photo id')
        if '/short-video/' not in page_url:
            page_url = f'https://www.kuaishou.com/short-video/{photo_id}'

        webpage = self._download_webpage(page_url, photo_id, '下载视频页', headers=self._HEADERS, fatal=False) or ''
        state = self._search_json(
            r'window\.__APOLLO_STATE__\s*=', webpage, 'apollo state', photo_id, default={})
        client = state.get('defaultClient') or {}
        photo = client.get(f'VisionVideoDetailPhoto:{photo_id}') or next(
            (v for k, v in client.items() if k.startswith('VisionVideoDetailPhoto:')), None)
        if not photo:
            # 电脑网页版经常拒绝访问（页面只返回 {"result":2}）：改用手机分享页，数据是完整的
            return self._from_mobile(photo_id)

        formats = []
        resources = traverse_obj(photo, ('videoResource', 'json', {dict})) or {}
        for codec_key, vcodec in (('h264', 'h264'), ('hevc', 'h265')):
            for rep in traverse_obj(resources, (codec_key, 'adaptationSet', ..., 'representation', ...)):
                rep_url = url_or_none(rep.get('url'))
                if not rep_url:
                    continue
                formats.append({
                    'url': rep_url,
                    'format_id': f'{codec_key}-{rep.get("qualityType") or rep.get("id")}',
                    'format_note': rep.get('qualityLabel'),
                    'ext': 'mp4',
                    'width': int_or_none(rep.get('width')),
                    'height': int_or_none(rep.get('height')),
                    'fps': int_or_none(rep.get('frameRate')),
                    'tbr': int_or_none(rep.get('avgBitrate')),
                    'filesize': int_or_none(rep.get('fileSize')),
                    'vcodec': vcodec,
                    'acodec': 'aac',
                    'http_headers': self._HEADERS,
                })
        if not formats:
            for key, vcodec in (('photoUrl', 'h264'), ('photoH265Url', 'h265')):
                if u := url_or_none(photo.get(key)):
                    formats.append({'url': u, 'format_id': key, 'ext': 'mp4', 'vcodec': vcodec,
                                    'acodec': 'aac', 'http_headers': self._HEADERS})
        if not formats:
            raise ExtractorError('没有找到视频地址', expected=True)

        author = next((v for k, v in client.items() if k.startswith('VisionVideoDetailAuthor:')), {})
        caption = (photo.get('caption') or '').strip()
        return {
            'id': photo_id,
            'title': caption or f'快手_{photo_id}',
            'description': caption,
            'uploader': author.get('name'),
            'uploader_id': author.get('id'),
            'duration': (int_or_none(photo.get('duration')) or 0) / 1000 or None,
            'timestamp': (int_or_none(photo.get('timestamp')) or 0) // 1000 or None,
            'thumbnail': url_or_none(photo.get('coverUrl')),
            'like_count': int_or_none(photo.get('realLikeCount')),
            'webpage_url': f'https://www.kuaishou.com/short-video/{photo_id}',
            'formats': formats,
        }


    _MOBILE_HEADERS = {'User-Agent': ANDROID_UA, 'Referer': 'https://m.gifshow.com/'}

    def _from_mobile(self, photo_id):
        webpage = self._download_webpage(f'https://m.gifshow.com/fw/photo/{photo_id}', photo_id,
                                         '下载手机分享页', headers=self._MOBILE_HEADERS)
        state = self._search_json(r'window\.INIT_STATE\s*=', webpage, 'init state', photo_id, default={})
        photo = next((v['photo'] for v in state.values() if isinstance(v, dict)
                      and isinstance(v.get('photo'), dict) and v['photo'].get('photoId')), None)
        if not photo:
            if 'captcha' in webpage.lower():
                raise ExtractorError('快手要求人机验证，请稍后重试', expected=True)
            raise ExtractorError('快手未返回视频数据（可能已删除或设为私密）', expected=True)
        if photo.get('photoType') not in (None, 'VIDEO'):
            raise ExtractorError('这是快手图集作品，暂不支持下载图片', expected=True)
        formats = []
        for rep in traverse_obj(photo, ('manifest', 'adaptationSet', ..., 'representation', ...)):
            rep_url = url_or_none(rep.get('url'))
            if not rep_url:
                continue
            codec = {'avc': 'h264', 'hevc': 'h265'}.get(rep.get('videoCodec'), rep.get('videoCodec'))
            formats.append({
                'url': rep_url,
                'format_id': f'{rep.get("videoCodec") or "v"}-{rep.get("qualityType") or rep.get("id")}',
                'format_note': rep.get('qualityLabel'),
                'ext': 'mp4',
                'width': int_or_none(rep.get('width')),
                'height': int_or_none(rep.get('height')),
                'fps': int_or_none(rep.get('frameRate')),
                'tbr': int_or_none(rep.get('avgBitrate')),
                'filesize': int_or_none(rep.get('fileSize')),
                'vcodec': codec,
                'acodec': 'aac',
                'http_headers': self._MOBILE_HEADERS,
            })
        if not formats:
            u = traverse_obj(photo, ('mainMvUrls', 0, 'url', {url_or_none}))
            if u:
                formats.append({'url': u, 'format_id': 'main', 'ext': 'mp4', 'width': int_or_none(photo.get('width')),
                                'height': int_or_none(photo.get('height')), 'acodec': 'aac',
                                'http_headers': self._MOBILE_HEADERS})
        if not formats:
            raise ExtractorError('没有找到视频地址', expected=True)
        caption = (photo.get('caption') or '').strip()
        return {
            'id': photo_id,
            'title': caption or f'快手_{photo_id}',
            'description': caption,
            'uploader': photo.get('userName'),
            'uploader_id': str(photo.get('userId') or '') or None,
            'duration': (int_or_none(photo.get('duration')) or 0) / 1000 or None,
            'timestamp': (int_or_none(photo.get('timestamp')) or 0) // 1000 or None,
            'thumbnail': traverse_obj(photo, ('coverUrls', 0, 'url', {url_or_none})),
            'like_count': int_or_none(photo.get('likeCount')),
            'view_count': int_or_none(photo.get('viewCount')),
            'webpage_url': f'https://www.kuaishou.com/short-video/{photo_id}',
            'formats': formats,
        }


CUSTOM_EXTRACTORS = (DouyinShareIE, KuaishouShareIE)


def register(ydl):
    """把自定义提取器放到 yt-dlp 内置提取器之前（需以 auto_init=False 创建 YoutubeDL）。"""
    _patch_bilibili()
    for ie in CUSTOM_EXTRACTORS:
        ydl.add_info_extractor(ie())
    ydl.add_default_info_extractors()


# ---------------------------------------------------------------- B站 下载节点
#
# B站 给每条视频/音频流一个主地址 + 几个备用地址。主地址大多是 PCDN（借用别人家宽带的 P2P 节点，
# 如 xy*.mcdn.bilivideo.cn:8082、*.mountaintoys.cn:4483），速度很不稳定，有时只有几十 KB/s；
# 备用地址里的官方 CDN（upos-sz-*.bilivideo.com、cn-*.bilivideo.com）通常快十几倍。
# yt-dlp 只用主地址，这里把全部地址收集到 format['url_candidates']，官方节点排前面，
# 下载前再由 fastdl.rank_urls 实测挑最快的。

_PCDN_HOST = re.compile(r'\.mcdn\.bilivideo\.(?:cn|com)$|\.szbdyd\.com$|mountaintoys\.cn$')
# 同一个 upos 地址换成这些官方镜像域名也能下（签名不绑定域名），给测速多几个选择
_BILI_MIRRORS = ('upos-sz-mirrorali.bilivideo.com', 'upos-sz-mirrorhw.bilivideo.com',
                 'upos-sz-mirrorcos.bilivideo.com')


def _is_pcdn(url):
    from urllib.parse import urlparse
    p = urlparse(url)
    return bool(_PCDN_HOST.search(p.hostname or '')) or p.port not in (None, 80, 443)


def bili_url_candidates(base, backups):
    """官方节点（B站 给的备用地址 → 镜像域名）在前，PCDN 节点在后。"""
    from urllib.parse import urlparse
    urls = [u for u in [base, *backups] if u]
    official = [u for u in urls if not _is_pcdn(u)]
    pcdn = [u for u in urls if _is_pcdn(u)]
    src = next((u for u in urls if urlparse(u).path.startswith('/upgcxcode/')), None)
    mirrors = []
    if src:
        p = urlparse(src)
        mirrors = [p._replace(scheme='https', netloc=h).geturl() for h in _BILI_MIRRORS]
    return list(dict.fromkeys(official + mirrors + pcdn))


def _patch_bilibili():
    from yt_dlp.extractor import bilibili as bili
    orig = bili.BilibiliBaseIE.extract_formats
    if getattr(orig, '_vd_patched', False):
        return

    def extract_formats(self, play_info):
        formats = orig(self, play_info)
        backups = {}
        streams = list(traverse_obj(play_info, ('dash', (None, 'dolby'), ('video', 'audio'), ..., {dict})) or [])
        flac = traverse_obj(play_info, ('dash', 'flac', 'audio', {dict}))
        if flac:
            streams.append(flac)
        for s in streams:
            base = traverse_obj(s, 'baseUrl', 'base_url', 'url')
            if base:
                backups[base] = list(traverse_obj(s, 'backupUrl', 'backup_url', default=None) or [])
        for d in play_info.get('durl') or []:
            if d.get('url'):
                backups[d['url']] = list(d.get('backup_url') or [])
        for f in formats:
            if f.get('url') in backups and not f.get('fragments'):
                cands = bili_url_candidates(f['url'], backups[f['url']])
                if len(cands) > 1:
                    f['url_candidates'] = cands
                    f['url'] = cands[0]          # 不测速时也至少避开 PCDN
        return formats

    extract_formats._vd_patched = True
    bili.BilibiliBaseIE.extract_formats = extract_formats
