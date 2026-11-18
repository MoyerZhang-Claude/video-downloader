"""评论存档网页（评论.html）的模板。

一个文件就是整个页面：样式、脚本、评论数据都在里面；图片、头像、表情放在旁边的文件夹里（相对路径），
整个文件夹拷到别的电脑、手机上也能打开，不需要联网（没下载到的图片会退回 B站原图地址）。
外观跟软件一致：暖白 / 深灰两套配色、衬线标题、星芒标志；自带的英文字体（VDSans / VDSerif，SIL OFL 开源授权）
直接嵌在网页里，中文用系统字体。
"""
import base64
import html
import os
import sys


def _font_b64(name):
    base = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.join(base, 'fonts'), os.path.join(getattr(sys, '_MEIPASS', base), 'app', 'fonts')):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            with open(p, 'rb') as f:
                return base64.b64encode(f.read()).decode()
    try:                                     # 手机版（Chaquopy）：字体打包在 App 里，按包资源读
        import pkgutil
        data = pkgutil.get_data(__package__ or 'app', 'fonts/' + name)
        return base64.b64encode(data).decode() if data else ''
    except Exception:  # noqa: BLE001 - 没有字体就用系统字体
        return ''


def _font_faces():
    out = []
    for fam, file, weight in (('VDSans', 'VDSans-Regular.ttf', 400), ('VDSans', 'VDSans-SemiBold.ttf', 600),
                              ('VDSerif', 'VDSerif-Regular.ttf', 400), ('VDSerif', 'VDSerif-SemiBold.ttf', 600)):
        b = _font_b64(file)
        if b:
            out.append(f"@font-face{{font-family:'{fam}';font-weight:{weight};font-style:normal;font-display:swap;"
                       f"src:url(data:font/ttf;base64,{b}) format('truetype');}}")
    return '\n'.join(out)


def render(title, data_json, logo_svg='', media_json='{}'):
    """media_json：{相对路径: data: 网址}，图片嵌在网页里时用（手机版）；空 = 图片在旁边的文件夹里"""
    return (_TEMPLATE.replace('%%TITLE%%', html.escape(f'{title} · 评论存档'))
            .replace('%%FONTS%%', _font_faces())
            .replace('%%LOGO%%', logo_svg or _FALLBACK_LOGO)
            .replace('%%MEDIA%%', media_json or '{}')
            .replace('%%DATA%%', data_json))


# 软件标志「星芒落点」（由 comment_ui.spark_svg() 从界面里的图形生成；手机版没有 Qt，所以直接写在这里）
_FALLBACK_LOGO = (
    '<svg viewBox="0 -4 100 100" xmlns="http://www.w3.org/2000/svg" fill="currentColor"><path d="M45.40 41.50 L46.6'
    '0 14.90 C46.60 13.02 48.12 11.50 50.00 11.50 C51.88 11.50 53.40 13.02 53.40 14.90 L54.60 41.50 L45.40 41.50Z"/'
    '><path d="M46.28 38.80 L57.83 24.94 C58.93 23.42 61.06 23.08 62.58 24.19 C64.10 25.29 64.43 27.42 63.33 28.94 '
    'L53.72 44.20 L46.28 38.80Z"/><path d="M48.58 37.13 L71.39 30.97 C73.18 30.39 75.10 31.37 75.68 33.16 C76.26 34'
    '.94 75.28 36.86 73.50 37.44 L51.42 45.87 L48.58 37.13Z"/><path d="M51.42 37.13 L67.41 43.58 C69.19 44.16 70.17'
    ' 46.08 69.59 47.87 C69.01 49.65 67.09 50.63 65.31 50.05 L48.58 45.87 L51.42 37.13Z"/><path d="M53.72 38.80 L61'
    '.80 51.96 C62.91 53.48 62.57 55.61 61.05 56.71 C59.53 57.81 57.40 57.48 56.30 55.96 L46.28 44.20 L53.72 38.80Z'
    '"/><path d="M53.72 44.20 L44.17 55.31 C43.07 56.83 40.94 57.17 39.42 56.06 C37.90 54.96 37.56 52.83 38.67 51.3'
    '1 L46.28 38.80 L53.72 44.20Z"/><path d="M51.42 45.87 L28.23 52.15 C26.44 52.73 24.52 51.75 23.94 49.97 C23.36 '
    '48.18 24.34 46.26 26.12 45.68 L48.58 37.13 L51.42 45.87Z"/><path d="M48.58 45.87 L32.97 39.54 C31.19 38.96 30.'
    '21 37.04 30.79 35.26 C31.37 33.47 33.29 32.49 35.07 33.07 L51.42 37.13 L48.58 45.87Z"/><path d="M46.28 44.20 L'
    '32.32 22.95 C31.22 21.43 31.55 19.30 33.07 18.20 C34.59 17.10 36.72 17.43 37.82 18.95 L53.72 38.80 L46.28 44.2'
    '0Z"/><path d="M59.40 41.50 C59.40 46.69 55.19 50.90 50.00 50.90 C44.81 50.90 40.60 46.69 40.60 41.50 C40.60 36'
    '.31 44.81 32.10 50.00 32.10 C55.19 32.10 59.40 36.31 59.40 41.50Z"/><path d="M46.00 45.50 C46.00 43.29 47.79 4'
    '1.50 50.00 41.50 C52.21 41.50 54.00 43.29 54.00 45.50 L54.00 61.70 C54.00 63.91 52.21 65.70 50.00 65.70 C47.79'
    ' 65.70 46.00 63.91 46.00 61.70 L46.00 45.50Z"/><path d="M42.88 68.07 C40.16 65.16 40.79 63.70 44.78 63.70 L55.'
    '22 63.70 C59.21 63.70 59.84 65.16 57.12 68.07 L51.90 73.66 C50.63 75.02 49.37 75.02 48.10 73.66 L42.88 68.07Z"'
    '/></svg>')

_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="链接解析下载器">
<title>%%TITLE%%</title>
<style>
/* 字体：VDSans / VDSerif（SIL Open Font License 1.1） */
%%FONTS%%
:root{
  --bg:#fcfcfb; --surface:#ffffff; --subtle:#f3f3f0; --thread:#f7f6f3; --chip:#f1f0ec; --selected:#efeeea;
  --text:#131313; --text2:#383835; --text3:#7b7974; --text4:#a5a49a;
  --border:rgba(30,30,29,.10); --border2:rgba(30,30,29,.18);
  --brand:#d97757; --accent:#c6613f; --tint:rgba(198,97,63,.10); --tint2:rgba(198,97,63,.16);
  --vip:#c0487a; --ok:#3f7a2e; --warn:#9a6a12; --tint-warn:rgba(196,140,30,.12);
  --mark:rgba(232,177,62,.38); --flash:rgba(217,119,87,.18); --shadow:0 1px 2px rgba(0,0,0,.04),0 6px 24px rgba(0,0,0,.06);
  --lv0:#b5b3aa; --lv2:#6fa784; --lv3:#5f93c4; --lv4:#d2a03f; --lv5:#e07f45; --lv6:#d24c48;
  --sans:'VDSans','Microsoft YaHei UI','Microsoft YaHei','PingFang SC','Hiragino Sans GB','Noto Sans CJK SC',system-ui,sans-serif;
  --serif:'VDSerif','Microsoft YaHei UI','PingFang SC','Noto Serif CJK SC',Georgia,serif;
  --bar-h:64px;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#191919; --surface:#1f1f1f; --subtle:#242424; --thread:#1d1d1d; --chip:#2a2a2a; --selected:#404040;
  --text:#f9f9f7; --text2:#c3c2b7; --text3:#97958d; --text4:#6d6b67;
  --border:rgba(255,255,255,.09); --border2:rgba(255,255,255,.16);
  --tint:rgba(217,119,87,.14); --tint2:rgba(217,119,87,.22); --accent:#d9704c;
  --vip:#e48aac; --ok:#8cc26a; --warn:#dcaa4f; --tint-warn:rgba(220,170,79,.12);
  --mark:rgba(232,177,62,.30); --flash:rgba(217,119,87,.22); --shadow:0 1px 2px rgba(0,0,0,.3),0 8px 28px rgba(0,0,0,.35);
  --lv0:#77756f; --lv2:#7fb892; --lv3:#78a9d6; --lv4:#dcb35f; --lv5:#e8935f; --lv6:#e46a65;
  color-scheme:dark;
}}
:root[data-theme="dark"]{
  --bg:#191919; --surface:#1f1f1f; --subtle:#242424; --thread:#1d1d1d; --chip:#2a2a2a; --selected:#404040;
  --text:#f9f9f7; --text2:#c3c2b7; --text3:#97958d; --text4:#6d6b67;
  --border:rgba(255,255,255,.09); --border2:rgba(255,255,255,.16);
  --tint:rgba(217,119,87,.14); --tint2:rgba(217,119,87,.22); --accent:#d9704c;
  --vip:#e48aac; --ok:#8cc26a; --warn:#dcaa4f; --tint-warn:rgba(220,170,79,.12);
  --mark:rgba(232,177,62,.30); --flash:rgba(217,119,87,.22); --shadow:0 1px 2px rgba(0,0,0,.3),0 8px 28px rgba(0,0,0,.35);
  --lv0:#77756f; --lv2:#7fb892; --lv3:#78a9d6; --lv4:#dcb35f; --lv5:#e8935f; --lv6:#e46a65;
  color-scheme:dark;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 var(--sans);-webkit-font-smoothing:antialiased}
body.noscroll{overflow:hidden}
a{color:inherit}
button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer}
.i{display:inline-flex;width:1em;height:1em;flex:none}
.i svg{width:100%;height:100%}
.wrap{max-width:820px;margin:0 auto;padding:0 16px}

/* ---------- 页眉 ---------- */
.top{padding:34px 0 8px}
.brandline{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text3);letter-spacing:.02em}
.brandline .logo{width:20px;height:20px;color:var(--brand);display:inline-flex}
.brandline .logo svg{width:100%;height:100%}
.brandline b{color:var(--text2);font-weight:600}
.hero{display:flex;gap:18px;align-items:flex-start;margin-top:22px}
.hero .cover{width:168px;aspect-ratio:16/10;border-radius:12px;object-fit:cover;flex:none;background:var(--subtle);
  border:1px solid var(--border)}
.hero h1{font:600 26px/1.3 var(--serif);margin:0 0 10px;letter-spacing:-.005em;word-break:break-word}
.hero .meta{color:var(--text3);font-size:13.5px;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center}
.hero .meta b{color:var(--text2);font-weight:600}
.hero .meta a{color:var(--accent);text-decoration:none}
.hero .meta a:hover{text-decoration:underline}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:22px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:12px 14px}
.tile .num{font:600 22px/1.2 var(--serif);font-variant-numeric:tabular-nums}
.tile .lab{font-size:12.5px;color:var(--text3);margin-top:2px}
.fine{font-size:12.5px;color:var(--text3);margin-top:12px}
.notes{margin-top:12px;display:flex;flex-direction:column;gap:6px}
.note{font-size:13px;color:var(--warn);background:var(--tint-warn);border-radius:10px;padding:8px 12px}

/* ---------- 概览 ---------- */
.sec-t{font:600 13px/1 var(--sans);color:var(--text3);letter-spacing:.04em;margin:30px 0 12px;display:flex;align-items:center;gap:8px}
.sec-t::after{content:"";flex:1;height:1px;background:var(--border)}
.ov{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:16px 18px}
.card.full{grid-column:1/-1}
.card-h{font-size:13px;color:var(--text3);margin-bottom:12px;display:flex;justify-content:space-between;gap:8px}
.quotes{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.quote{text-align:left;display:flex;flex-direction:column;gap:8px;padding:2px 0;border-radius:10px;min-width:0}
.quote:hover .qt{color:var(--text)}
.quote .qm{font:600 34px/0.6 var(--serif);color:var(--brand);height:16px}
.quote .qt .emo{width:1.4em;height:1.4em;vertical-align:-.34em}
.quote .qt{font:15px/1.65 var(--serif);color:var(--text2);display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;
  overflow:hidden;word-break:break-word;white-space:pre-wrap}
.quote .qa{font-size:12.5px;color:var(--text3);display:flex;gap:4px 8px;align-items:center;margin-top:auto;flex-wrap:wrap}
.quote .qa .i{color:var(--accent)}
.bars{display:flex;flex-direction:column;gap:7px}
.bar-row{display:grid;grid-template-columns:4.2em 1fr 4.6em;gap:10px;align-items:center;font-size:13px}
.bar-row .n{color:var(--text2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-row .track{height:8px;border-radius:4px;background:var(--subtle);overflow:hidden}
.bar-row .fill{height:100%;border-radius:4px;background:var(--brand);opacity:.82}
.bar-row .v{color:var(--text3);text-align:right;font-variant-numeric:tabular-nums}
.chart{display:flex;align-items:flex-end;gap:2px;height:112px;padding-top:6px}
.chart .b{flex:1;min-width:1px;background:var(--brand);opacity:.55;border-radius:2px 2px 0 0;min-height:1px}
.chart .b:hover{opacity:1}
.axis{display:flex;justify-content:space-between;font-size:11.5px;color:var(--text4);margin-top:6px}
.peak{font-size:12.5px;color:var(--text3)}
.empty-s{font-size:13px;color:var(--text4)}

/* ---------- 工具栏 ---------- */
.bar{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 88%,transparent);
  backdrop-filter:saturate(1.4) blur(12px);-webkit-backdrop-filter:saturate(1.4) blur(12px);border-bottom:1px solid var(--border);
  margin-top:26px}
.bar-in{display:flex;align-items:center;gap:10px;padding:10px 16px;max-width:820px;margin:0 auto;flex-wrap:wrap}
.search{flex:1 1 200px;display:flex;align-items:center;gap:8px;height:38px;padding:0 12px;border-radius:12px;
  background:var(--surface);border:1px solid var(--border2);min-width:0}
.search:focus-within{border-color:var(--text3)}
.search .i{color:var(--text3);width:17px;height:17px}
.search input{flex:1;min-width:0;border:0;outline:0;background:none;color:var(--text);font:inherit;font-size:14px}
.search input::placeholder{color:var(--text4)}
.search kbd{font:11px var(--sans);color:var(--text4);border:1px solid var(--border2);border-radius:5px;padding:0 5px}
.seg{display:inline-flex;background:var(--subtle);border-radius:11px;padding:3px;gap:2px;max-width:100%;overflow-x:auto;
  scrollbar-width:none}
.seg::-webkit-scrollbar{display:none}
.seg button{height:30px;padding:0 11px;border-radius:8px;font-size:13px;color:var(--text3);white-space:nowrap}
.seg button:hover{color:var(--text)}
.seg button.on{background:var(--surface);color:var(--text);box-shadow:0 1px 2px rgba(0,0,0,.08);font-weight:600}
.tools{display:flex;align-items:center;gap:10px;min-width:0}
.chipb{height:32px;padding:0 12px;border-radius:999px;border:1px solid var(--border2);font-size:13px;color:var(--text2);
  display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.chipb:hover{background:var(--subtle)}
.chipb.on{background:var(--tint);border-color:transparent;color:var(--accent)}
.list-h{display:flex;gap:8px;align-items:baseline;padding:16px 0 0;font-size:13px;color:var(--text3)}
.list-h #count{color:var(--text2);font-weight:600}
.brandline .iconb{margin-left:auto}
.iconb{width:34px;height:34px;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;color:var(--text3)}
.iconb:hover{background:var(--subtle);color:var(--text)}
.iconb .i{width:18px;height:18px}

/* ---------- 评论 ---------- */
#list{padding:6px 0 20px}
.c{display:flex;gap:14px;padding:20px 0;border-bottom:1px solid var(--border);scroll-margin-top:calc(var(--bar-h) + 12px)}
.c.pinned{background:linear-gradient(90deg,var(--tint) 0,transparent 70%);margin:0 -16px;padding:20px 16px;border-radius:0}
.av{border-radius:50%;flex:none;object-fit:cover;background:var(--subtle)}
.av.s40{width:40px;height:40px}
.av.s28{width:28px;height:28px}
.av.letter{display:inline-flex;align-items:center;justify-content:center;font-weight:600;
  background:hsl(var(--h) 34% 88%);color:hsl(var(--h) 34% 30%)}
:root[data-theme="dark"] .av.letter{background:hsl(var(--h) 16% 26%);color:hsl(var(--h) 40% 80%)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .av.letter{background:hsl(var(--h) 16% 26%);color:hsl(var(--h) 40% 80%)}}
.av.s40.letter{font-size:16px}.av.s28.letter{font-size:12px}
.body{flex:1;min-width:0}
.hd{display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-height:22px}
.name{font-weight:600;font-size:14px;color:var(--text2)}
.name.vip{color:var(--vip)}
.vf{font-size:11px;font-weight:700;color:var(--text3);margin-left:-2px}
.bar-row.who{grid-template-columns:9em 1fr 3.4em}
.lv{font:700 9.5px/1 var(--sans);padding:2px 4px 2px;border-radius:4px;border:1px solid currentColor;letter-spacing:.02em}
.lv0,.lv1{color:var(--lv0)}.lv2{color:var(--lv2)}.lv3{color:var(--lv3)}.lv4{color:var(--lv4)}.lv5{color:var(--lv5)}.lv6{color:var(--lv6)}
.badge{font-size:11px;line-height:1;padding:3px 6px;border-radius:5px;font-weight:600}
.badge.up{background:var(--accent);color:#fff}
.badge.pin{background:var(--tint2);color:var(--accent)}
.badge.op{background:var(--chip);color:var(--text3);font-weight:500}
.floor{margin-left:auto;font-size:12px;color:var(--text4);font-variant-numeric:tabular-nums}
.to{font-size:13px;color:var(--text3);display:inline-flex;align-items:center;gap:3px}
.to .i{width:13px;height:13px}
.to a{color:var(--accent);text-decoration:none;cursor:pointer}
.to a:hover{text-decoration:underline}
.to a.gone{color:var(--text3);cursor:default;text-decoration:none}
.tx{margin-top:5px;font-size:15px;line-height:1.75;white-space:pre-wrap;word-break:break-word;color:var(--text)}
.tx a{color:var(--accent);word-break:break-all}
.tx .emo{width:1.45em;height:1.45em;vertical-align:-.36em;margin:0 1px}
mark{background:var(--mark);color:inherit;border-radius:3px;padding:0 1px}
.ft{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:12.5px;color:var(--text3)}
.ft .like{display:inline-flex;align-items:center;gap:5px;font-variant-numeric:tabular-nums}
.ft .like .i{width:14px;height:14px}
.ft .like.hot{color:var(--accent)}
.tag{font-size:11.5px;padding:2px 8px;border-radius:999px;background:var(--tint);color:var(--accent);display:inline-flex;
  align-items:center;gap:4px}
.tag .i{width:12px;height:12px}
.linkb{color:var(--text3);font-size:12.5px;display:inline-flex;align-items:center;gap:4px}
.linkb:hover{color:var(--accent)}
.linkb .i{width:13px;height:13px}

.pics{display:grid;gap:6px;margin-top:10px;max-width:420px}
.pics.n1{grid-template-columns:minmax(0,300px)}
.pics.n2{grid-template-columns:repeat(2,1fr);max-width:300px}
.pics.n3{grid-template-columns:repeat(3,1fr)}
.pic{position:relative;display:block;border-radius:10px;overflow:hidden;background:var(--subtle);border:1px solid var(--border);
  aspect-ratio:1;padding:0;cursor:zoom-in}
.pics.n1 .pic{aspect-ratio:auto;max-height:420px}
.pic img{display:block;width:100%;height:100%;object-fit:cover;object-position:top}
.pics.n1 .pic img{height:auto;max-height:420px}
.pic .ptag{position:absolute;right:6px;bottom:6px;font-size:10.5px;padding:1px 6px;border-radius:5px;
  background:rgba(0,0,0,.55);color:#fff}
.pic.gif{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;text-decoration:none;
  color:var(--text3);font-size:12px;cursor:pointer;aspect-ratio:1;padding:6px;text-align:center}
.pic.gif .i{width:20px;height:20px}
.pic.gif small{font-size:10.5px;color:var(--text4)}

/* ---------- 楼中楼 ---------- */
.thread{margin-top:14px;background:var(--thread);border-radius:14px;position:relative}
.th-head{display:flex;align-items:center;gap:10px;padding:9px 12px 9px 14px;font-size:12.5px;color:var(--text3);
  border-radius:14px 14px 0 0;flex-wrap:wrap}
.th-head.stick{position:sticky;top:var(--bar-h);z-index:5;background:color-mix(in srgb,var(--thread) 92%,transparent);
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--border)}
.th-head .t{display:inline-flex;align-items:center;gap:6px;min-width:0}
.th-head .t .i{width:14px;height:14px}
.th-head .t b{color:var(--text2);font-weight:600}
.th-head .who{color:var(--text3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:16em}
.th-head .sp{flex:1}
.mini{display:inline-flex;background:var(--chip);border-radius:8px;padding:2px;gap:2px}
.mini button{height:24px;padding:0 9px;border-radius:6px;font-size:12px;color:var(--text3)}
.mini button.on{background:var(--surface);color:var(--text);box-shadow:0 1px 2px rgba(0,0,0,.08)}
.fold{height:26px;padding:0 10px;border-radius:8px;font-size:12px;color:var(--text2);background:var(--surface);
  border:1px solid var(--border2);display:inline-flex;align-items:center;gap:4px}
.fold:hover{border-color:var(--text3)}
.fold .i{width:13px;height:13px}
.rlist{position:relative;padding:2px 12px 4px 14px}
.rlist::before{content:"";position:absolute;left:27px;top:6px;bottom:14px;width:1.5px;background:var(--border2);border-radius:1px}
.r{display:flex;gap:10px;padding:10px 0;position:relative;scroll-margin-top:calc(var(--bar-h) + 56px);border-radius:10px}
.r .av{position:relative;z-index:1;box-shadow:0 0 0 3px var(--thread)}
.r .tx{font-size:14.5px;margin-top:3px;line-height:1.7}
.r .name{font-size:13.5px}
.r .ft{margin-top:5px;gap:12px}
.r+.r{border-top:1px dashed var(--border)}
.more{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:4px 14px 12px 52px}
.more button{font-size:13px;color:var(--accent);padding:4px 0}
.more button:hover{text-decoration:underline}
.more .dim{font-size:12.5px;color:var(--text4)}
.more .gap{width:1px;height:12px;background:var(--border2)}
.th-none{padding:10px 14px;font-size:12.5px;color:var(--text4)}
.search-note{padding:0 14px 6px 14px;font-size:12px;color:var(--text3)}
.search-note button{color:var(--accent)}
.flash{animation:flash 1.8s ease-out}
@keyframes flash{0%,30%{background:var(--flash)}100%{background:transparent}}

.sentinel{height:1px}
.empty{padding:60px 0;text-align:center;color:var(--text3)}
.empty .i{width:28px;height:28px;color:var(--text4);margin-bottom:8px}
footer{border-top:1px solid var(--border);margin-top:10px;padding:26px 0 48px;font-size:12.5px;color:var(--text4);line-height:1.8}
footer .logo{width:16px;height:16px;color:var(--brand);display:inline-flex;vertical-align:-3px;margin-right:6px}
footer .logo svg{width:100%;height:100%}

/* ---------- 对话 / 大图 ---------- */
.modal{position:fixed;inset:0;z-index:50;background:rgba(10,10,10,.45);display:flex;align-items:center;justify-content:center;
  padding:16px;animation:fade .15s}
.modal[hidden]{display:none}
@keyframes fade{from{opacity:0}}
.sheet{background:var(--bg);border-radius:20px;width:min(640px,100%);max-height:min(82vh,900px);display:flex;flex-direction:column;
  box-shadow:var(--shadow);border:1px solid var(--border);overflow:hidden}
.sheet-h{display:flex;align-items:center;gap:10px;padding:14px 14px 12px 20px;border-bottom:1px solid var(--border)}
.sheet-h .t{font:600 16px/1.3 var(--serif);flex:1;min-width:0}
.sheet-h .s{font-size:12.5px;color:var(--text3);font-family:var(--sans);font-weight:400;display:block;margin-top:2px}
.sheet-b{overflow:auto;padding:6px 20px 16px}
.ctx{opacity:.75;border-bottom:1px solid var(--border);padding-bottom:4px;margin-bottom:4px}
.ctx .c{border:0;padding:14px 0}
.dlg .r{padding:12px 0}
.lb{background:rgba(8,8,8,.92);flex-direction:column;gap:10px;padding:16px 56px}
.lb img{max-width:100%;max-height:calc(100vh - 110px);object-fit:contain;border-radius:6px;background:#222}
.lb .lb-bar{display:flex;gap:16px;align-items:center;color:#ddd;font-size:13px}
.lb .lb-bar a{color:#ddd}
.lb .nav{position:absolute;top:50%;transform:translateY(-50%);width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.12);
  color:#fff;display:flex;align-items:center;justify-content:center}
.lb .nav:hover{background:rgba(255,255,255,.22)}
.lb .nav .i{width:22px;height:22px}
.lb .prev{left:10px}.lb .next{right:10px}
.lb .close{position:absolute;top:12px;right:12px;color:#fff}
.lb .close:hover{background:rgba(255,255,255,.12)}
.lb-scroll{max-height:calc(100vh - 110px);overflow:auto;border-radius:6px}
.lb-scroll img{max-height:none}

.totop{position:fixed;right:max(16px,env(safe-area-inset-right));bottom:calc(22px + env(safe-area-inset-bottom));z-index:30;
  width:46px;height:46px;border-radius:50%;background:var(--surface);border:1px solid var(--border2);color:var(--text2);
  box-shadow:var(--shadow);display:flex;align-items:center;justify-content:center;
  opacity:0;transform:translateY(10px) scale(.94);pointer-events:none;transition:opacity .22s,transform .22s}
.totop.show{opacity:1;transform:none;pointer-events:auto}
.totop:hover{color:var(--text);border-color:var(--text3)}
.totop .i{width:20px;height:20px}
@media (max-width:720px){
  .top{padding-top:22px}
  .hero{flex-direction:column;gap:14px}
  .hero .cover{width:100%;max-width:360px}
  .hero h1{font-size:22px}
  .tiles{grid-template-columns:repeat(2,1fr)}
  .ov{grid-template-columns:1fr}
  .quotes{grid-template-columns:1fr}
  .c{gap:10px}
  .av.s40{width:34px;height:34px}
  .rlist{padding-left:8px;padding-right:8px}
  .rlist::before{left:21px}
  .more{padding-left:44px}
  .lb{padding:16px 8px}
  .lb .nav{display:none}
  .search kbd{display:none}
  .bar-in{padding:8px 12px;gap:8px}
  .search{flex:1 1 100%;height:36px;border-radius:11px}
  .tools{flex:1 1 100%;gap:8px;overflow-x:auto;scrollbar-width:none;margin:0 -12px;padding:0 12px}
  .tools::-webkit-scrollbar{display:none}
  .tools .seg{flex:none;overflow:visible;max-width:none}
  .seg button{height:28px;padding:0 10px}
  .chipb{height:30px;flex:none;padding:0 11px}
}
@media print{.bar,.more,.fold,.mini{display:none!important}.th-head.stick{position:static}}
</style>
</head>
<body>
<div class="wrap">
  <header class="top" id="top"></header>
  <div id="overview"></div>
</div>
<div class="bar" id="bar"><div class="bar-in">
  <label class="search"><span class="i" data-i="search"></span><input id="q" type="search" placeholder="搜索评论或用户名" autocomplete="off"><kbd>/</kbd></label>
  <div class="tools">
    <div class="seg" id="sort"></div>
    <button class="chipb" id="f-pics"><span class="i" data-i="image"></span>有图</button>
    <button class="chipb" id="f-up"><span class="i" data-i="star"></span>UP 主参与</button>
  </div>
</div></div>
<div class="wrap">
  <div class="list-h"><span id="count"></span><span id="count-sub"></span></div>
  <main id="list"></main>
  <div class="sentinel" id="sentinel"></div>
  <div class="empty" id="empty" hidden><span class="i" data-i="search"></span><div>没有符合条件的评论</div></div>
  <footer id="foot"></footer>
</div>
<button class="totop" id="totop" title="回到顶部" aria-label="回到顶部"><span class="i" data-i="totop"></span></button>
<div class="modal" id="dlg" hidden></div>
<div class="modal lb" id="lb" hidden></div>
<template id="logo">%%LOGO%%</template>
<script type="application/json" id="media">%%MEDIA%%</script>
<script type="application/json" id="data">%%DATA%%</script>
<script>
(function () {
'use strict';
var D = JSON.parse(document.getElementById('data').textContent);
var PF = (D.meta && D.meta.platform) || 'B站', YT = PF === 'YouTube';
var UPN = YT ? '作者' : 'UP 主';                       // 视频作者的叫法
var APPROX = !!(D.meta && D.meta.time_approx);       // 时间是按「几天前」估算的（YouTube 内置方式）
var MEDIA = {};
try { MEDIA = JSON.parse(document.getElementById('media').textContent) || {}; } catch (e) {}
function M(p) { return (p && MEDIA[p]) || p; }       // 图片嵌在网页里时（手机版）按路径取
var LOGO = document.getElementById('logo').innerHTML;
var ICON = {
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.7"/><path d="M20.5 15.5l-4.5-4.5-8.5 8.5"/>',
  star: '<path d="M12 4l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.4 7.2 18.9l.9-5.4-3.9-3.8 5.4-.8z"/>',
  heart: '<path d="M12 19.5s-7.5-4.4-7.5-9.6A4.2 4.2 0 0 1 12 7.6a4.2 4.2 0 0 1 7.5 2.3c0 5.2-7.5 9.6-7.5 9.6z"/>',
  heartf: '<path fill="currentColor" d="M12 19.5s-7.5-4.4-7.5-9.6A4.2 4.2 0 0 1 12 7.6a4.2 4.2 0 0 1 7.5 2.3c0 5.2-7.5 9.6-7.5 9.6z"/>',
  reply: '<path d="M9.5 7.5L5 12l4.5 4.5"/><path d="M5.5 12H14a5 5 0 0 1 5 5v1"/>',
  chat: '<path d="M5 6.5h14a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5h-7l-4.5 3v-3H5A1.5 1.5 0 0 1 3.5 16V8A1.5 1.5 0 0 1 5 6.5z"/>',
  up: '<path d="M7 14l5-5 5 5"/>', down: '<path d="M7 10l5 5 5-5"/>',
  totop: '<path d="M5.5 4.5h13"/><path d="M12 20V9M6.8 14.2L12 9l5.2 5.2"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>', left: '<path d="M14.5 6l-6 6 6 6"/>', right: '<path d="M9.5 6l6 6-6 6"/>',
  sun: '<circle cx="12" cy="12" r="3.8"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/>',
  monitor: '<rect x="3.5" y="4.5" width="17" height="12" rx="2"/><path d="M9 20h6M12 16.5V20"/>',
  ext: '<path d="M13.5 5.5h5v5M18.5 5.5l-7 7M17 14v3.5A1.5 1.5 0 0 1 15.5 19h-9A1.5 1.5 0 0 1 5 17.5v-9A1.5 1.5 0 0 1 6.5 7H10"/>'
};
function svg(name) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" ' +
    'stroke-linejoin="round">' + (ICON[name] || '') + '</svg>';
}
function ic(name) { var s = document.createElement('span'); s.className = 'i'; s.innerHTML = svg(name); return s; }
document.querySelectorAll('[data-i]').forEach(function (e) { e.innerHTML = svg(e.getAttribute('data-i')); });
function h(tag, attrs) {
  var e = document.createElement(tag);
  if (attrs) for (var k in attrs) {
    var v = attrs[k];
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k === 'style') e.style.cssText = v;
    else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (var i = 2; i < arguments.length; i++) add(e, arguments[i]);
  return e;
}
function add(e, k) {
  if (k == null || k === false) return;
  if (Array.isArray(k)) { k.forEach(function (x) { add(e, x); }); return; }
  e.append(k instanceof Node ? k : String(k));
}
function num(n) {
  n = n || 0;
  if (n >= 100000000) return (n / 100000000).toFixed(1).replace(/\.0$/, '') + '亿';
  if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + '万';
  return String(n);
}
function pad(n) { return n < 10 ? '0' + n : '' + n; }
function date(ts, withTime) {
  if (!ts) return '';
  var d = new Date(ts * 1000);
  var s = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  return withTime === false ? s : s + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}
function hash(s) { var x = 0; for (var i = 0; i < s.length; i++) x = (x * 31 + s.charCodeAt(i)) | 0; return Math.abs(x); }

// ---------------- 数据索引
var ALL = [], BY = {};
D.comments.forEach(function (c, i) {
  c.floor = i + 1; c.main = true; BY[c.id] = c; ALL.push(c);
  c.replies.forEach(function (r, j) { r.host = c; r.main = false; r.n = j + 1; BY[r.id] = r; ALL.push(r); });
});
var nSub = ALL.length - D.comments.length;
var nPics = 0; ALL.forEach(function (x) { x.pics.forEach(function (p) { if (p.src) nPics++; }); });
var nPeople = 0, seenPeople = {};
ALL.forEach(function (x) { var k = x.uid || x.name; if (k && !seenPeople[k]) { seenPeople[k] = 1; nPeople++; } });

// ---------------- 文字（表情、链接、搜索高亮），全部用文本节点，不拼 HTML
var TOKEN = /(\[[^\[\]\n]{1,24}\])|(https?:\/\/[^\s<>"'，。！？）】]+)/g;
function textNode(text, x) {
  var box = h('div', {class: 'tx'});
  var last = 0, m;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(text))) {
    if (m.index > last) plain(box, text.slice(last, m.index));
    if (m[1]) {
      var src = M(x.emotes && x.emotes[m[1]]);
      if (src) box.append(h('img', {class: 'emo', src: src, alt: m[1], title: m[1], loading: 'lazy'}));
      else plain(box, m[1]);
    } else {
      box.append(h('a', {href: m[2], target: '_blank', rel: 'noopener noreferrer'}, m[2]));
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) plain(box, text.slice(last));
  return box;
}
function plain(box, s) {
  if (!Q) { box.append(s); return; }
  var low = s.toLowerCase(), i = 0, j;
  while ((j = low.indexOf(Q, i)) >= 0) {
    if (j > i) box.append(s.slice(i, j));
    box.append(h('mark', null, s.slice(j, j + Q.length)));
    i = j + Q.length;
  }
  if (i < s.length) box.append(s.slice(i));
}
function nameNode(x) {
  var n = h('span', {class: 'name' + (x.vip ? ' vip' : '')});
  plain(n, x.name || '（已注销）');
  return n;
}

// ---------------- 头像、图片
function avatar(x, size) {
  var letter = function () {
    var ch = Array.from((x.name || '?').replace(/^@/, '').trim())[0] || '?';
    return h('span', {class: 'av letter s' + size, style: '--h:' + (hash(x.name || '') % 360), 'aria-hidden': 'true'}, ch);
  };
  if (!x.avatar) return letter();
  var img = h('img', {class: 'av s' + size, src: M(x.avatar), alt: '', loading: 'lazy', referrerpolicy: 'no-referrer'});
  img.addEventListener('error', function () { img.replaceWith(letter()); });
  return img;
}
function picsNode(x) {
  if (!x.pics.length) return null;
  var ps = x.pics, view = ps.filter(function (p) { return !p.gif; });
  var grid = h('div', {class: 'pics n' + Math.min(ps.length, 3)});
  ps.forEach(function (p) {
    if (p.gif) {
      grid.append(h('a', {class: 'pic gif', href: p.url, target: '_blank', rel: 'noopener noreferrer', title: '动图没有保存（体积大）'},
        ic('image'), h('span', null, '动图'), h('small', null, '点开在浏览器看')));
      return;
    }
    var long = p.w && p.h && p.h / p.w > 2.4;
    var b = h('button', {class: 'pic', title: '点击查看大图', onclick: function () { lightbox(view, view.indexOf(p)); }},
      h('img', {src: M(p.src) || p.url, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer'}),
      long ? h('span', {class: 'ptag'}, '长图') : null);
    if (ps.length === 1 && p.w && p.h) b.style.aspectRatio = p.w + ' / ' + Math.min(p.h, p.w * 1.4);
    grid.append(b);
  });
  return grid;
}

// ---------------- 一条评论
function head(x, host) {
  var hd = h('div', {class: 'hd'}, nameNode(x),
    x.verified ? h('span', {class: 'vf', title: '已认证'}, '✓') : null,
    x.level != null ? h('span', {class: 'lv lv' + Math.min(6, x.level || 0)}, 'LV' + (x.level || 0)) : null);
  if (x.up) hd.append(h('span', {class: 'badge up', title: '视频作者'}, YT ? '作者' : 'UP'));
  if (host && x.uid && x.uid === host.uid && !x.up) hd.append(h('span', {class: 'badge op', title: '这一层的楼主'}, '层主'));
  if (x.pinned) hd.append(h('span', {class: 'badge pin'}, '置顶'));
  if (host && x.reply_to) {
    var target = x.parent && x.parent !== host.id ? BY[x.parent] : null;
    var a = h('a', {class: target ? '' : 'gone',
      title: target ? target.name + '：' + target.text.slice(0, 90) : '被回复的这条没有保存'}, '@' + x.reply_to);
    if (target) a.addEventListener('click', function () { closeDialog(); jump(target.id); });
    hd.append(h('span', {class: 'to'}, ic('reply'), '回复 ', a));
  }
  if (!host) hd.append(h('span', {class: 'floor'}, '#' + x.floor));
  return hd;
}
function foot(x, host) {
  var ft = h('div', {class: 'ft'},
    h('span', {class: 'like' + (x.like >= 100 ? ' hot' : ''), title: x.like + ' 个赞'}, ic(x.like >= 100 ? 'heartf' : 'heart'), num(x.like)),
    APPROX ? h('span', {title: '按「' + (x.ago || '几天前') + '」估算，大约是 ' + date(x.time, false)}, x.ago || '约 ' + date(x.time, false))
      : h('span', {title: date(x.time)}, date(x.time)),
    x.ip ? h('span', null, x.ip) : null);
  if (x.up_like) ft.append(h('span', {class: 'tag'}, ic('heartf'), YT ? '作者点了爱心' : 'UP 主觉得很赞'));
  if (!host && x.up_reply) ft.append(h('span', {class: 'tag'}, ic('chat'), UPN + '回复了'));
  if (host && x.dialog) {
    var group = host.replies.filter(function (r) { return r.dialog === x.dialog; });
    if (group.length >= 2) {
      ft.append(h('button', {class: 'linkb', onclick: function () { openDialog(host, group); }}, ic('chat'), '查看对话 · ' + group.length));
    }
  }
  return ft;
}
function mainNode(c) {
  var body = h('div', {class: 'body'}, head(c, null), textNode(c.text, c), picsNode(c), foot(c, null));
  if (c.replies.length || c.rcount) body.append(threadNode(c));
  return h('article', {class: 'c' + (c.pinned ? ' pinned' : ''), id: 'c' + c.id}, avatar(c, 40), body);
}
function replyNode(r, host, inDialog) {
  return h('div', {class: 'r', id: inDialog ? null : 'c' + r.id}, avatar(r, 28),
    h('div', {class: 'body'}, head(r, host), textNode(r.text, r), picsNode(r), inDialog ? footLite(r) : foot(r, host)));
}
function footLite(x) {
  return h('div', {class: 'ft'}, h('span', {class: 'like'}, ic('heart'), num(x.like)), h('span', null, date(x.time)),
    x.ip ? h('span', null, x.ip) : null);
}

// ---------------- 楼中楼：默认 3 条，可以一段一段展开；展开后标题条吸顶，随时能收起
var TS = {};
function ts(c) { return TS[c.id] || (TS[c.id] = {shown: 3, sort: 'time', all: false}); }
function threadNode(c) { var w = h('div', {class: 'thread', id: 't' + c.id}); drawThread(c, w); return w; }
function sortedReplies(c) {
  var reps = c.replies.slice();
  if (ts(c).sort === 'like') reps.sort(function (a, b) { return b.like - a.like || a.time - b.time; });
  return reps;
}
function drawThread(c, w) {
  w = w || document.getElementById('t' + c.id);
  if (!w) return;
  w.textContent = '';
  var s = ts(c);
  if (!c.replies.length) {
    w.append(h('div', {class: 'th-none'}, c.rcount + ' 条回复（这次没有保存楼中楼）'));
    return;
  }
  var reps = sortedReplies(c), note = null;
  if (Q && !s.all) {
    var m = reps.filter(hit);
    if (m.length && m.length < reps.length) {
      note = h('div', {class: 'search-note'}, '只显示和搜索词有关的 ' + m.length + ' 条回复 · ',
        h('button', {onclick: function () { s.all = true; drawThread(c); }}, '显示全部 ' + reps.length + ' 条'));
      reps = m;
    }
  }
  var n = reps.length, shown = Math.min(n, s.shown), expanded = shown > 3;
  var hd = h('div', {class: 'th-head' + (expanded ? ' stick' : '')},
    h('span', {class: 't'}, ic('chat'), h('b', null, num(c.rcount) + ' 条回复'),
      c.replies.length < c.rcount ? h('span', null, '· 已保存 ' + c.replies.length) : null),
    expanded ? h('span', {class: 'who'}, '· ' + (c.name || '') + ' 的楼') : null,
    h('span', {class: 'sp'}));
  if (c.replies.length > 3) {
    var mini = h('div', {class: 'mini'});
    [['time', '按时间'], ['like', '最多赞']].forEach(function (o) {
      mini.append(h('button', {class: s.sort === o[0] ? 'on' : '', onclick: function () { s.sort = o[0]; drawThread(c); }}, o[1]));
    });
    hd.append(mini);
  }
  if (expanded) hd.append(h('button', {class: 'fold', onclick: function () { collapse(c); }}, ic('up'), '收起'));
  w.append(hd);
  if (note) w.append(note);
  var list = h('div', {class: 'rlist'});
  reps.slice(0, shown).forEach(function (r) { list.append(replyNode(r, c)); });
  w.append(list);
  if (shown < n || expanded) {
    var more = h('div', {class: 'more'});
    if (shown < n) {
      var left = n - shown, step = Math.min(20, left);
      if (left <= 20) {
        more.append(h('button', {onclick: function () { s.shown = n; drawThread(c); }}, '展开剩下 ' + left + ' 条'));
      } else {
        more.append(h('button', {onclick: function () { s.shown = shown + step; drawThread(c); }}, '再展开 ' + step + ' 条'));
        more.append(h('span', {class: 'gap'}));
        more.append(h('button', {onclick: function () { s.shown = n; drawThread(c); }}, '全部展开'));
        more.append(h('span', {class: 'dim'}, '还有 ' + left + ' 条'));
      }
    }
    if (expanded) {
      if (shown < n) more.append(h('span', {class: 'gap'}));
      more.append(h('button', {onclick: function () { collapse(c); }}, '收起'));
    }
    w.append(more);
  }
}
function collapse(c) {
  ts(c).shown = 3;
  drawThread(c);
  var art = document.getElementById('c' + c.id);
  if (art && art.getBoundingClientRect().top < 0) art.scrollIntoView({block: 'start'});
}

// ---------------- 列表：筛选、排序、分批画（几千条也不卡）
var Q = '', SORT = 'default', F_PICS = false, F_UP = false, LIST = [], DRAWN = 0, BATCH = 30;
var listEl = document.getElementById('list'), sentinel = document.getElementById('sentinel');
function hit(x) { return !Q || (x.text || '').toLowerCase().indexOf(Q) >= 0 || (x.name || '').toLowerCase().indexOf(Q) >= 0; }
function hasPics(x) { return x.pics.length > 0; }
function upIn(x) { return x.up || x.up_like || x.up_reply; }
function visible() {
  var arr = D.comments.filter(function (c) {
    return (hit(c) || c.replies.some(hit)) && (!F_PICS || hasPics(c) || c.replies.some(hasPics)) &&
      (!F_UP || upIn(c) || c.replies.some(function (r) { return r.up; }));
  });
  var cmp = {like: function (a, b) { return b.like - a.like; }, reply: function (a, b) { return b.rcount - a.rcount; },
    'new': function (a, b) { return b.time - a.time; }, old: function (a, b) { return a.time - b.time; }}[SORT];
  return cmp ? arr.slice().sort(cmp) : arr;
}
function redraw() {
  LIST = visible(); DRAWN = 0; listEl.textContent = '';
  drawMore();
  document.getElementById('empty').hidden = LIST.length > 0;
  var filtered = Q || F_PICS || F_UP;
  document.getElementById('count').textContent = filtered ? '找到 ' + LIST.length + ' 层' : D.comments.length + ' 条主评论';
  document.getElementById('count-sub').textContent = filtered ? '（共 ' + D.comments.length + ' 层）' :
    nSub ? '· ' + nSub + ' 条楼中楼' : '';
}
function drawMore() {
  var end = Math.min(LIST.length, DRAWN + BATCH), frag = document.createDocumentFragment();
  for (var i = DRAWN; i < end; i++) frag.append(mainNode(LIST[i]));
  listEl.append(frag);
  DRAWN = end;
  sentinel.hidden = DRAWN >= LIST.length;
}
function fill() {
  if (DRAWN < LIST.length && sentinel.getBoundingClientRect().top < innerHeight + 1200) { drawMore(); requestAnimationFrame(fill); }
}
if ('IntersectionObserver' in window) {
  new IntersectionObserver(function (es) { if (es[0].isIntersecting) fill(); }, {rootMargin: '1200px'}).observe(sentinel);
} else addEventListener('scroll', fill, {passive: true});

function jump(id) {
  var x = BY[id];
  if (!x) return;
  var c = x.main ? x : x.host;
  var idx = LIST.indexOf(c);
  if (idx < 0) {
    Q = ''; F_PICS = F_UP = false;
    document.getElementById('q').value = '';
    syncChips();
    redraw();
    idx = LIST.indexOf(c);
  }
  while (DRAWN <= idx) drawMore();
  if (!x.main) {
    var s = ts(c);
    s.all = true;
    var i = sortedReplies(c).indexOf(x);
    if (i >= s.shown) s.shown = i + 1;
    drawThread(c);
  }
  var el = document.getElementById('c' + id);
  if (!el) return;
  el.scrollIntoView({block: 'center', behavior: 'smooth'});
  el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
}

// ---------------- 对话、大图
var dlg = document.getElementById('dlg');
function openDialog(c, group) {
  var names = [];
  group.forEach(function (r) { if (names.indexOf(r.name) < 0) names.push(r.name); });
  var body = h('div', {class: 'sheet-b dlg'},
    h('div', {class: 'ctx'}, h('div', {class: 'c'}, avatar(c, 40), h('div', {class: 'body'}, head(c, null), textNode(c.text, c)))));
  group.slice().sort(function (a, b) { return a.time - b.time; }).forEach(function (r) { body.append(replyNode(r, c, true)); });
  dlg.textContent = '';
  dlg.append(h('div', {class: 'sheet', role: 'dialog', 'aria-modal': 'true'},
    h('div', {class: 'sheet-h'}, h('div', {class: 't'}, '对话',
      h('span', {class: 's'}, names.slice(0, 3).join('、') + (names.length > 3 ? ' 等 ' + names.length + ' 人' : '') + ' · ' + group.length + ' 条')),
      h('button', {class: 'iconb', title: '关闭（Esc）', onclick: closeDialog}, ic('x'))),
    body));
  dlg.hidden = false;
  document.body.classList.add('noscroll');
}
function closeDialog() { if (!dlg.hidden) { dlg.hidden = true; dlg.textContent = ''; document.body.classList.remove('noscroll'); } }
dlg.addEventListener('click', function (e) { if (e.target === dlg) closeDialog(); });

var lb = document.getElementById('lb'), LBP = [], LBI = 0;
function lightbox(pics, i) { LBP = pics; LBI = Math.max(0, i); drawLB(); lb.hidden = false; document.body.classList.add('noscroll'); }
function drawLB() {
  var p = LBP[LBI];
  lb.textContent = '';
  var img = h('img', {src: M(p.src) || p.url, alt: '', referrerpolicy: 'no-referrer'});
  var long = p.w && p.h && p.h / p.w > 2.4;
  lb.append(long ? h('div', {class: 'lb-scroll'}, img) : img,
    h('div', {class: 'lb-bar'}, LBP.length > 1 ? h('span', null, (LBI + 1) + ' / ' + LBP.length) : null,
      p.w ? h('span', null, p.w + ' × ' + p.h) : null,
      h('a', {href: p.url, target: '_blank', rel: 'noopener noreferrer'}, '在浏览器打开原图')),
    h('button', {class: 'iconb close', title: '关闭（Esc）', onclick: closeLB}, ic('x')));
  if (LBP.length > 1) {
    lb.append(h('button', {class: 'nav prev', title: '上一张（←）', onclick: function (e) { e.stopPropagation(); stepLB(-1); }}, ic('left')),
      h('button', {class: 'nav next', title: '下一张（→）', onclick: function (e) { e.stopPropagation(); stepLB(1); }}, ic('right')));
  }
}
function stepLB(d) { LBI = (LBI + d + LBP.length) % LBP.length; drawLB(); }
function closeLB() { if (!lb.hidden) { lb.hidden = true; lb.textContent = ''; document.body.classList.remove('noscroll'); } }
lb.addEventListener('click', function (e) { if (e.target === lb) closeLB(); });
window.vdBack = function () {           // 手机版 App 里按返回键：先关大图、对话窗口，关了返回 true
  if (!lb.hidden) { closeLB(); return true; }
  if (!dlg.hidden) { closeDialog(); return true; }
  return false;
};
addEventListener('keydown', function (e) {
  if (e.key === 'Escape') { closeLB(); closeDialog(); }
  else if (!lb.hidden && e.key === 'ArrowLeft') stepLB(-1);
  else if (!lb.hidden && e.key === 'ArrowRight') stepLB(1);
  else if (e.key === '/' && document.activeElement !== qEl) { e.preventDefault(); qEl.focus(); }
});

// ---------------- 页眉、概览
function drawTop() {
  var m = D.meta, o = D.options;
  var top = document.getElementById('top');
  top.append(h('div', {class: 'brandline'}, h('span', {class: 'logo'}), h('b', null, '评论存档'), '· 链接解析下载器',
    h('button', {class: 'iconb', id: 'theme'})));
  top.querySelector('.logo').innerHTML = LOGO;
  var info = h('div', null, h('h1', null, m.title),
    h('div', {class: 'meta'}, m.owner ? h('span', null, UPN + ' ', h('b', null, m.owner)) : null,
      m.pubdate ? h('span', null, '发布于 ' + date(m.pubdate, false)) : null,
      (m.video_id || m.bvid) ? h('span', null, m.video_id || m.bvid) : null,
      h('a', {href: m.url, target: '_blank', rel: 'noopener noreferrer'}, '在 ' + PF + ' 打开 ↗')));
  top.append(h('div', {class: 'hero'}, m.cover ? h('img', {class: 'cover', src: M(m.cover), alt: ''}) : null, info));
  var tiles = h('div', {class: 'tiles'});
  [[m.reply_total ? num(m.reply_total) : '—', PF + ' 显示的评论数'], [num(D.comments.length), '已保存主评论'], [num(nSub), '已保存楼中楼'],
   YT ? [num(nPeople), '参与的人'] : [num(nPics), '已保存图片']].forEach(function (t) {
    tiles.append(h('div', {class: 'tile'}, h('div', {class: 'num'}, t[0]), h('div', {class: 'lab'}, t[1])));
  });
  top.append(tiles);
  var sub = {all: '每层全部', limit: '每层最多 ' + o.sub_limit + ' 条', none: '不含楼中楼'}[o.sub_mode] || '';
  top.append(h('div', {class: 'fine'}, '保存于 ' + date(D.fetched_at) + ' · ' + (o.sort === 'hot' ? '按热度' : '按最新') +
    ' · 主评论' + (o.max_main ? '前 ' + o.max_main + ' 条' : '全部') + ' · 楼中楼' + sub));
  var notes = (D.stopped ? ['没有抓完：' + D.stopped + '（下面是已经保存的部分）'] : []).concat(D.notes || []);
  if (notes.length) {
    var box = h('div', {class: 'notes'});
    notes.forEach(function (t) { box.append(h('div', {class: 'note'}, t)); });
    top.append(box);
  }
}
function drawOverview() {
  var ov = document.getElementById('overview');
  if (ALL.length < 3) return;
  ov.append(h('div', {class: 'sec-t'}, '概览'));
  var grid = h('div', {class: 'ov'});
  // 最多赞
  var best = ALL.filter(function (x) { return x.like > 0 && x.text.trim(); })
    .sort(function (a, b) { return b.like - a.like; }).slice(0, 3);
  if (best.length) {
    var qs = h('div', {class: 'quotes'});
    best.forEach(function (x) {
      qs.append(h('button', {class: 'quote', title: '跳到这条评论', onclick: function () { jump(x.id); }},
        h('span', {class: 'qm'}, '“'), h('span', {class: 'qt'}, Array.from(textNode(x.text.length > 160 ?
          x.text.slice(0, 160) + '…' : x.text, x).childNodes)),
        h('span', {class: 'qa'}, ic('heartf'), num(x.like) + ' 赞', h('span', null, '· ' + (x.name || '')),
          x.main ? null : h('span', null, '· 楼中楼'))));
    });
    grid.append(h('div', {class: 'card full'}, h('div', {class: 'card-h'}, h('span', null, '最多赞的评论'),
      h('span', null, '点一下跳过去')), qs));
  }
  // IP 属地
  var ips = {}, withIp = 0;
  ALL.forEach(function (x) { if (x.ip) { ips[x.ip] = (ips[x.ip] || 0) + 1; withIp++; } });
  var top = Object.keys(ips).map(function (k) { return [k, ips[k]]; }).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 10);
  var card = h('div', {class: 'card'}, h('div', {class: 'card-h'}, h('span', null, 'IP 属地'),
    h('span', null, num(withIp) + ' 条带属地')));
  if (top.length) {
    var bars = h('div', {class: 'bars'}), mx = top[0][1];
    top.forEach(function (t) {
      bars.append(h('div', {class: 'bar-row', title: t[0] + '：' + t[1] + ' 条'}, h('span', {class: 'n'}, t[0]),
        h('span', {class: 'track'}, h('span', {class: 'fill', style: 'display:block;width:' + Math.max(2, t[1] / mx * 100) + '%'})),
        h('span', {class: 'v'}, num(t[1]) + ' · ' + Math.round(t[1] / withIp * 100) + '%')));
    });
    card.append(bars);
  } else card = whoCard();
  grid.append(card);
  grid.append(timeline());
  ov.append(grid);
}
function whoCard() {
  var cnt = {};
  ALL.forEach(function (x) { if (x.name) cnt[x.name] = (cnt[x.name] || 0) + 1; });
  var top = Object.keys(cnt).map(function (k) { return [k, cnt[k]]; }).filter(function (t) { return t[1] > 1; })
    .sort(function (a, b) { return b[1] - a[1]; }).slice(0, 10);
  var card = h('div', {class: 'card'}, h('div', {class: 'card-h'}, h('span', null, '发言最多的人'),
    h('span', null, '主评论 + 回复')));
  if (!top.length) { card.append(h('div', {class: 'empty-s'}, '每个人都只说了一句')); return card; }
  var bars = h('div', {class: 'bars'}), mx = top[0][1];
  top.forEach(function (t) {
    bars.append(h('div', {class: 'bar-row who', title: t[0] + '：' + t[1] + ' 条'}, h('span', {class: 'n'}, t[0]),
      h('span', {class: 'track'}, h('span', {class: 'fill', style: 'display:block;width:' + Math.max(2, t[1] / mx * 100) + '%'})),
      h('span', {class: 'v'}, t[1] + ' 条')));
  });
  card.append(bars);
  return card;
}
function timeline() {
  var card = h('div', {class: 'card'});
  var ts = ALL.map(function (x) { return x.time; }).filter(Boolean);
  if (ts.length < 3) { card.append(h('div', {class: 'card-h'}, '评论时间'), h('div', {class: 'empty-s'}, '评论太少')); return card; }
  var lo = Infinity, hi = -Infinity;
  ts.forEach(function (t) { if (t < lo) lo = t; if (t > hi) hi = t; });
  var span = hi - lo, unit = span <= 3 * 86400 ? 'hour' : span <= 120 * 86400 ? 'day' : 'month';
  function floor(t) {
    var d = new Date(t * 1000);
    if (unit === 'hour') d.setMinutes(0, 0, 0);
    else if (unit === 'day') d.setHours(0, 0, 0, 0);
    else { d.setDate(1); d.setHours(0, 0, 0, 0); }
    return d;
  }
  function next(d) {
    var n = new Date(d.getTime());
    if (unit === 'hour') n.setHours(n.getHours() + 1); else if (unit === 'day') n.setDate(n.getDate() + 1); else n.setMonth(n.getMonth() + 1);
    return n;
  }
  function label(d) {
    var md = (d.getMonth() + 1) + '月' + d.getDate() + '日';
    return unit === 'hour' ? md + ' ' + d.getHours() + '时' : unit === 'day' ? md : d.getFullYear() + '年' + (d.getMonth() + 1) + '月';
  }
  var bins = [], keys = {};
  for (var d = floor(lo); d.getTime() <= hi * 1000 && bins.length < 400; d = next(d)) {
    keys[d.getTime()] = bins.length; bins.push({d: d, n: 0});
  }
  ts.forEach(function (t) { var k = keys[floor(t).getTime()]; if (k != null) bins[k].n++; });
  var mx = 0, peak = bins[0];
  bins.forEach(function (b) { if (b.n > mx) { mx = b.n; peak = b; } });
  var chart = h('div', {class: 'chart'});
  bins.forEach(function (b) {
    chart.append(h('span', {class: 'b', title: label(b.d) + '：' + b.n + ' 条', style: 'height:' + (b.n ? Math.max(3, b.n / mx * 100) : 0) + '%'}));
  });
  card.append(h('div', {class: 'card-h'}, h('span', null, '评论时间（每' + {hour: '小时', day: '天', month: '月'}[unit] +
      (APPROX ? '，大约' : '') + '）'),
    h('span', {class: 'peak'}, '最多：' + label(peak.d) + ' ' + peak.n + ' 条')), chart,
    h('div', {class: 'axis'}, h('span', null, label(bins[0].d)), h('span', null, label(bins[bins.length - 1].d))));
  return card;
}

// ---------------- 工具栏
var qEl = document.getElementById('q'), timer = 0;
qEl.addEventListener('input', function () {
  clearTimeout(timer);
  timer = setTimeout(function () { Q = qEl.value.trim().toLowerCase(); TS = {}; redraw(); }, 180);
});
var sortEl = document.getElementById('sort');
[['default', D.options.sort === 'hot' ? '热度' : '最新'], ['like', '最多赞'], ['reply', '最多回复'], ['new', '最新'], ['old', '最早']]
  .forEach(function (o, i) {
    if (i === 3 && D.options.sort !== 'hot') return;      // 本来就是按最新抓的：不重复
    sortEl.append(h('button', {'data-k': o[0], class: SORT === o[0] ? 'on' : '', onclick: function () {
      SORT = o[0];
      sortEl.querySelectorAll('button').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-k') === SORT); });
      redraw();
      var bar = document.getElementById('bar');
      if (bar.getBoundingClientRect().top <= 0) scrollTo({top: bar.offsetTop, behavior: 'smooth'});
    }}, o[1]));
  });
function syncChips() {
  document.getElementById('f-pics').classList.toggle('on', F_PICS);
  document.getElementById('f-up').classList.toggle('on', F_UP);
}
document.getElementById('f-pics').addEventListener('click', function () { F_PICS = !F_PICS; syncChips(); redraw(); });
document.getElementById('f-up').lastChild.textContent = UPN + '参与';
document.getElementById('f-up').addEventListener('click', function () { F_UP = !F_UP; syncChips(); redraw(); });

drawTop();
var root = document.documentElement, themeBtn = document.getElementById('theme'), MODE = 'system';
try { MODE = localStorage.getItem('vd-comments-theme') || 'system'; } catch (e) {}
var hashTheme = /theme=(light|dark)/.exec(location.hash || '');
if (hashTheme) MODE = hashTheme[1];
window.vdSetTheme = function (m) { MODE = m; applyTheme(); };
function applyTheme() {
  if (MODE === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', MODE);
  themeBtn.innerHTML = svg({system: 'monitor', light: 'sun', dark: 'moon'}[MODE]);
  themeBtn.title = {system: '跟随系统（点一下换成浅色）', light: '浅色（点一下换成深色）', dark: '深色（点一下跟随系统）'}[MODE];
}
themeBtn.addEventListener('click', function () {
  MODE = {system: 'light', light: 'dark', dark: 'system'}[MODE];
  try { localStorage.setItem('vd-comments-theme', MODE); } catch (e) {}
  applyTheme();
});
applyTheme();

var toTop = document.getElementById('totop'), toTopTimer = 0;
function hideTopLater() {
  clearTimeout(toTopTimer);
  toTopTimer = setTimeout(function () { if (!toTop.matches(':hover')) toTop.classList.remove('show'); }, 1000);
}
addEventListener('scroll', function () {
  if (scrollY < innerHeight * 0.6) { toTop.classList.remove('show'); return; }    // 在页面最上面时不用出现
  toTop.classList.add('show');
  hideTopLater();
}, {passive: true});
toTop.addEventListener('mouseleave', hideTopLater);
toTop.addEventListener('click', function () {
  scrollTo({top: 0, behavior: 'smooth'});
  toTop.classList.remove('show');
});

function barH() {
  var bar = document.getElementById('bar');
  root.style.setProperty('--bar-h', (getComputedStyle(bar).position === 'sticky' ? bar.offsetHeight : 0) + 'px');
}
addEventListener('resize', barH);

document.getElementById('foot').append(h('span', {class: 'logo'}), '这份评论存档由「链接解析下载器」生成，数据来自 ' + PF + ' 公开的评论区。',
  h('br'), '评论的内容和图片属于各自的作者，请只用于个人存档、阅读和研究。动图没有保存，点开会在浏览器里打开原图。');
document.querySelector('#foot .logo').innerHTML = LOGO;
drawOverview();
redraw();
barH();
requestAnimationFrame(fill);
})();
</script>
</body>
</html>
"""
