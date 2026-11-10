"""写日志前把敏感内容打码（A6）。

解析失败时的原始报错会整段写进日志页和 logs\\app.log，里面可能有带签名参数的下载地址、Cookie；
日志页的「复制全部」又是专门让用户发给别人反馈问题用的。所以写之前：
- 常见的签名 / 令牌 / 登录 Cookie 的值换成 ***（参数名留着，方便看是哪一类地址）；
- 请求头里的 Cookie、Authorization 整行打码；
- 用户文件夹（C:\\Users\\名字）换成 %USERPROFILE%，不暴露 Windows 用户名。
"""
import logging
import re

# 网址参数、Cookie 里常见的敏感名字（不分大小写，要求是完整的名字：design= 不会被当成 sign=）
_NAMES = (
    'sign', 'signature', 'sig', 'lsig', 'upsig', 'ssig', 'token', 'access_token', 'refresh_token', 'id_token',
    'auth_key', 'authkey', 'auth_token', 'auth', 'key', 'wts', 'w_rid', 'hdnts', 'hdnea', 'policy', 'key-pair-id',
    'x-amz-signature', 'x-amz-credential', 'x-amz-security-token', 'x-goog-signature', 'x-oss-signature',
    'pwd', 'passwd', 'password', 'passcode', 'code', 'csrf', 'csrf_token',
    'sessdata', 'bili_jct', 'dedeuserid', 'dedeuserid__ckmd5', 'buvid3', 'buvid4', 'sid',
    'sessionid', 'sessionid_ss', 'sid_tt', 'sid_guard', 'ttwid', 'passport_csrf_token', 'ct0', 'kuaishou.web.cp.api_st',
    'sapisid', 'apisid', 'hsid', 'ssid', 'login_info', 'bduss', 'bduss_bfess', 'stoken', '__puus', '__pus',
)
_PARAM = re.compile(r'(?i)(?<![\w.\-])(' + '|'.join(re.escape(n) for n in sorted(_NAMES, key=len, reverse=True))
                    + r'|__secure-[\w\-]+)=([^&;\s\'"<>#]+)')
_HEADER = re.compile(r'(?i)\b((?:set-)?cookie|authorization)\b(["\']?\s*[:=]\s*)[^\r\n]+')
_HOME = re.compile(r'(?i)\b[a-z]:[\\/]+users[\\/]+[^\\/\s:*?"<>|]+')


def redact(text):
    if not text:
        return text
    if '=' in text:
        text = _PARAM.sub(lambda m: f'{m.group(1)}=***', text)
    if 'ookie' in text or 'uthorization' in text or 'OOKIE' in text or 'UTHORIZATION' in text:
        text = _HEADER.sub(lambda m: f'{m.group(1)}{m.group(2)}***', text)
    if ':' in text:
        text = _HOME.sub('%USERPROFILE%', text)
    return text


class Formatter(logging.Formatter):
    """日志文件用的格式化器：整条记录（包括异常的调用栈）写出去之前打一遍码。"""

    def format(self, record):
        return redact(super().format(record))
