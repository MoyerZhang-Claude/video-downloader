"""app_update 的单元测试：用伪造的 GitHub 发布信息检查版本比较、安装包选择和发布说明的分组。

运行：.venv\\Scripts\\python.exe tools\\test_app_update.py
"""
import hashlib
import http.server
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app_update  # noqa: E402

NOTES = '''一段开头的话。

## 更新内容

### 修复
- 修了 **一个** 问题
- [安卓版] 只有手机上才有的修复
### 新功能
- 自动准备 YouTube 组件（`deno.exe`）
- [电脑版] 内置浏览器登录
  这是上一条的续行
- 【安卓版】网页登录
### 改进
* 滚动更顺了

## 下载
- LinkDownloader-9.9.9-windows-setup.exe
'''


def release(body=NOTES, version='9.9.9', digest='sha256:' + 'ab' * 32):
    def asset(name, size):
        return {'name': name, 'size': size, 'browser_download_url': f'https://example.invalid/{name}', 'digest': digest}
    return {'html_url': 'https://example.invalid/release', 'published_at': '2026-11-10T08:00:00Z', 'body': body,
            'assets': [asset(f'LinkDownloader-{version}-windows.zip', 200), asset(f'LinkDownloader-{version}-windows-setup.exe', 100),
                       asset('LinkDownloader-2.1.1-android.apk', 50), asset('notes.txt', 1)]}


class ParseRelease(unittest.TestCase):
    def test_newer_windows_prefers_setup(self):
        rel = app_update.parse_release(release(), 'windows', '2.4.1')
        self.assertEqual(rel['version'], '9.9.9')
        self.assertTrue(rel['download'].endswith('-windows-setup.exe'))
        self.assertEqual((rel['size'], rel['digest'], rel['published']), (100, 'ab' * 32, '2026-11-10T08:00:00Z'))

    def test_not_newer(self):
        self.assertIsNone(app_update.parse_release(release(), 'windows', '9.9.9'))
        self.assertIsNone(app_update.parse_release(release(), 'windows', '10.0'))
        self.assertIsNone(app_update.parse_release(release(), 'android', '2.1.1'))     # 这次发布里安卓版没变

    def test_each_platform_has_own_version(self):
        self.assertEqual(app_update.parse_release(release(), 'android', '2.0.0')['version'], '2.1.1')

    def test_version_compare_is_numeric(self):
        self.assertIsNotNone(app_update.parse_release(release(version='2.10.0'), 'windows', '2.9.0'))
        self.assertIsNone(app_update.parse_release(release(version='2.5.0'), 'windows', '2.5.0'))

    def test_no_digest(self):
        self.assertEqual(app_update.parse_release(release(digest=None), 'windows', '1.0')['digest'], '')


class ParseNotes(unittest.TestCase):
    def test_groups_in_fixed_order_and_platform_filter(self):
        got = app_update.parse_notes(NOTES, 'windows')
        self.assertEqual([g['title'] for g in got], ['新功能', '改进', '修复'])
        self.assertEqual(got[0]['items'], ['自动准备 YouTube 组件（`deno.exe`）', '内置浏览器登录 这是上一条的续行'])
        self.assertEqual(got[1]['items'], ['滚动更顺了'])
        self.assertEqual(got[2]['items'], ['修了 **一个** 问题'])

    def test_android_sees_its_own(self):
        got = app_update.parse_notes(NOTES, 'android')
        self.assertEqual(got[0]['items'], ['自动准备 YouTube 组件（`deno.exe`）', '网页登录'])
        self.assertEqual(got[2]['items'], ['修了 **一个** 问题', '只有手机上才有的修复'])

    def test_download_section_is_not_included(self):
        text = str(app_update.parse_notes(NOTES, 'windows'))
        self.assertNotIn('setup.exe', text)
        self.assertNotIn('开头的话', text)

    def test_fallback_plain_list(self):
        got = app_update.parse_notes('## 更新内容\n- 第一条\n- 第二条\n\n## 别的\n- 不要', 'windows')
        self.assertEqual(got, [{'title': '', 'items': ['第一条', '第二条']}])

    def test_fallback_without_heading(self):
        got = app_update.parse_notes('修了几个问题\n- 甲\n- 乙', 'android')
        self.assertEqual(got, [{'title': '', 'items': ['修了几个问题', '甲', '乙']}])

    def test_empty(self):
        self.assertEqual(app_update.parse_notes('', 'windows'), [])
        self.assertEqual(app_update.parse_notes(None, 'windows'), [])

    def test_group_only_for_other_platform_is_dropped(self):
        got = app_update.parse_notes('## 更新内容\n### 新功能\n- [安卓版] 手机的\n### 修复\n- 通用的', 'windows')
        self.assertEqual(got, [{'title': '修复', 'items': ['通用的']}])


class DownloadInstaller(unittest.TestCase):
    """应用内更新：校验通过才留下文件；校验不通过、没有校验值都不留文件（也就不会被运行）。"""

    @classmethod
    def setUpClass(cls):
        cls.payload = b'MZ fake installer ' * 1000

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(len(cls.payload)))
                self.end_headers()
                self.wfile.write(cls.payload)

            def log_message(self, *a):
                pass
        cls.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.srv.server_port}/LinkDownloader-9.9.9-windows-setup.exe'

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def rel(self, digest):
        return {'download': self.url, 'name': 'LinkDownloader-9.9.9-windows-setup.exe', 'digest': digest, 'size': len(self.payload)}

    def test_good_digest(self):
        with tempfile.TemporaryDirectory() as d:
            seen = []
            path = app_update.download_installer(self.rel(hashlib.sha256(self.payload).hexdigest()), d, seen.append)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(open(path, 'rb').read(), self.payload)
            self.assertEqual(seen[-1], 1.0)

    def test_bad_digest_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                app_update.download_installer(self.rel('00' * 32), d)
            self.assertEqual(os.listdir(d), [])

    def test_missing_digest_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                app_update.download_installer(self.rel(''), d)
            self.assertEqual(os.listdir(d), [])

    def test_only_setup_exe(self):
        with tempfile.TemporaryDirectory() as d:
            rel = dict(self.rel(hashlib.sha256(self.payload).hexdigest()), name='LinkDownloader-9.9.9-windows.zip')
            with self.assertRaises(RuntimeError):
                app_update.download_installer(rel, d)


if __name__ == '__main__':
    unittest.main(verbosity=1)
