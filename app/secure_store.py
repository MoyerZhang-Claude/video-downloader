"""登录信息加密保存（电脑版和安卓版共用）。

B站登录、网站 Cookie 这类能直接当成账号用的东西不落明文：
- Windows：用系统的 DPAPI 加密，只有当前 Windows 用户能解开（和谷歌云盘令牌、网盘账号同一种做法）。
- 安卓：android_bridge 启动时用 set_backend() 装上 Kotlin 端的加解密（密钥在 Android Keystore 里，导不出来）。
文件格式：4 字节标记 + 密文。读出来只放在内存里，不写临时文件。
"""
import ctypes
import http.cookiejar
import json
import os
import threading

MAGIC = b'VDS1'             # 加密的
MAGIC_PLAIN = b'VDS0'       # 这个平台没有可用的加密（只在开发环境里出现）

_backend = None             # (加密函数, 解密函数)：bytes -> bytes
_lock = threading.Lock()


def set_backend(protect_fn, unprotect_fn):
    """安卓：换成 Keystore 的加解密。"""
    global _backend
    _backend = (protect_fn, unprotect_fn)


# ---------------------------------------------------------------- DPAPI（只有当前 Windows 用户能解开）
class _Blob(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_uint32), ('pbData', ctypes.POINTER(ctypes.c_char))]


def dpapi(data, protect):
    """原始的 DPAPI 加解密（谷歌云盘令牌直接用它，文件格式保持不变）。不是 Windows 时原样返回。"""
    if os.name != 'nt':
        return data
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):    # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError('登录信息加密 / 解密失败')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(data):
    if _backend:
        return MAGIC + _backend[0](data)
    if os.name == 'nt':
        return MAGIC + dpapi(data, True)
    return MAGIC_PLAIN + data


def unprotect(blob):
    head, body = blob[:4], blob[4:]
    if head == MAGIC_PLAIN:
        return body
    if head != MAGIC:
        raise ValueError('不是加密的登录信息文件')
    if _backend:
        return _backend[1](body)
    if os.name == 'nt':
        return dpapi(body, False)
    raise OSError('这台设备解不开这个文件')


# ---------------------------------------------------------------- 文件
def write(path, data):
    """加密后写入（先写临时文件再替换，写到一半断电也不会坏）。"""
    blob = protect(data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with _lock:
        with open(tmp, 'wb') as f:
            f.write(blob)
        os.replace(tmp, path)


def read(path):
    """读出并解密；文件不存在、解不开（换了电脑 / 换了系统用户）返回 None。"""
    try:
        with open(path, 'rb') as f:
            blob = f.read()
        return unprotect(blob)
    except (OSError, ValueError):
        return None


def write_json(path, obj):
    write(path, json.dumps(obj, ensure_ascii=False).encode('utf-8'))


def read_json(path, default=None):
    data = read(path)
    if data is None:
        return default
    try:
        return json.loads(data.decode('utf-8'))
    except ValueError:
        return default


def remove(path):
    for p in (path, path + '.tmp'):
        try:
            os.remove(p)
        except OSError:
            pass


# ---------------------------------------------------------------- Cookie（Netscape 格式的文本 <-> Cookie 对象）
_HEADER = '# Netscape HTTP Cookie File\n'


def parse_cookies(text):
    """Netscape 格式（浏览器扩展导出的 cookies.txt）→ Cookie 列表。
    兼容没有文件头、Windows 换行、UTF-8 BOM、#HttpOnly_ 前缀；格式不对的行跳过。"""
    out = []
    for line in text.lstrip(chr(0xFEFF)).replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        http_only = line.startswith('#HttpOnly_')
        if http_only:
            line = line[len('#HttpOnly_'):]
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) == 6:             # 值为空的 cookie：名字在最后一列
            parts.append('')
        if len(parts) != 7:
            continue
        domain, domain_specified, path, secure, expires, name, value = parts
        if not name:                    # 旧浏览器的写法：只有值没有名字
            name, value = value, None
        try:
            exp = int(float(expires)) if expires.strip() else None
        except ValueError:
            exp = None
        out.append(http.cookiejar.Cookie(
            0, name, value, None, False, domain, domain_specified.upper() == 'TRUE', domain.startswith('.'),
            path, True, secure.upper() == 'TRUE', exp or None, not exp, None, None,
            {'HttpOnly': None} if http_only else {}))
    return out


def dump_cookies(cookies):
    lines = [_HEADER, '# 由链接解析下载器保存\n\n']
    for c in cookies:
        name, value = (c.name, c.value) if c.value is not None else ('', c.name)
        prefix = '#HttpOnly_' if c.has_nonstandard_attr('HttpOnly') else ''
        lines.append('\t'.join((
            prefix + c.domain, 'TRUE' if c.domain.startswith('.') else 'FALSE', c.path or '/',
            'TRUE' if c.secure else 'FALSE', str(int(c.expires)) if c.expires else '0', name, value or '')) + '\n')
    return ''.join(lines)


_cookie_cache = {}          # 路径 -> (修改时间, Cookie 列表)


def load_cookies(path):
    """读加密保存的 Cookie（按文件修改时间缓存，避免每次解析 / 下载都解密一遍）。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _cookie_cache.pop(path, None)
        return []
    cached = _cookie_cache.get(path)
    if not cached or cached[0] != mtime:
        data = read(path)
        cached = (mtime, parse_cookies(data.decode('utf-8', 'replace')) if data else [])
        _cookie_cache[path] = cached
    return list(cached[1])


def save_cookies(path, cookies):
    write(path, dump_cookies(cookies).encode('utf-8'))
    _cookie_cache.pop(path, None)


def forget(path):
    _cookie_cache.pop(path, None)


def import_plain_cookies(txt_path, bin_path):
    """把明文的 cookies.txt 加密保存成 bin_path，然后删掉明文文件。返回导入了多少条。
    读不出来返回 -1、里面没有 cookie 返回 0，这两种情况文件都留着不动；加密失败抛 OSError（明文也留着）。"""
    try:
        with open(txt_path, 'rb') as f:
            cookies = parse_cookies(f.read().decode('utf-8-sig', 'replace'))
    except OSError:
        return -1
    if not cookies:
        return 0
    save_cookies(bin_path, cookies)
    try:
        os.remove(txt_path)
    except OSError:
        pass
    return len(cookies)
