"""下载历史：记录下载过的视频，用于「已下载过」提示和历史记录页。数据存在程序目录的 history.db。"""
import os
import sqlite3
import threading
import time

from .config import ROOT

DB_PATH = os.path.join(ROOT, 'history.db')


def video_key(info):
    """同一个视频在不同链接（短链、带参数的分享链接）下也得到同一个键。"""
    return f'{info.get("extractor_key") or info.get("extractor") or ""}:{info.get("id") or info.get("webpage_url")}'


class History:
    def __init__(self, path=DB_PATH):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute('''CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vkey TEXT NOT NULL, title TEXT, platform TEXT, uploader TEXT, url TEXT,
            quality TEXT, filepath TEXT, size INTEGER, ts REAL)''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_vkey ON downloads(vkey)')
        self.conn.commit()

    def add(self, task):
        v = task.video
        size = 0
        try:
            size = os.path.getsize(task.filepath) if task.filepath else 0
        except OSError:
            pass
        self.add_record(video_key(v.info), v.title, v.platform, v.uploader, v.info.get('webpage_url') or v.url,
                        task.tag, task.filepath, size)

    def add_record(self, vkey, title, platform, uploader, url, quality, filepath, size):
        """写一条下载记录（手机版的 filepath 是系统分配的 content:// 地址）。"""
        with self._lock:
            # 同一视频同一清晰度只留最新一条
            self.conn.execute('DELETE FROM downloads WHERE vkey=? AND quality=?', (vkey, quality))
            self.conn.execute(
                'INSERT INTO downloads (vkey, title, platform, uploader, url, quality, filepath, size, ts) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (vkey, title, platform, uploader, url, quality, filepath, size, time.time()))
            self.conn.commit()

    def lookup(self, info):
        """这个视频下载过的记录（最新在前）：[{quality, filepath, ts, exists}]"""
        with self._lock:
            rows = self.conn.execute(
                'SELECT quality, filepath, ts FROM downloads WHERE vkey=? ORDER BY ts DESC',
                (video_key(info),)).fetchall()
        return [{'quality': q, 'filepath': p, 'ts': ts, 'exists': bool(p) and os.path.exists(p)} for q, p, ts in rows]

    def recent(self, search='', limit=500):
        sql = 'SELECT id, title, platform, uploader, url, quality, filepath, size, ts FROM downloads'
        args = []
        if search:
            sql += ' WHERE title LIKE ? OR uploader LIKE ? OR platform LIKE ?'
            args = [f'%{search}%'] * 3
        sql += ' ORDER BY ts DESC LIMIT ?'
        args.append(limit)
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        keys = ('id', 'title', 'platform', 'uploader', 'url', 'quality', 'filepath', 'size', 'ts')
        return [dict(zip(keys, r)) for r in rows]

    def count(self):
        with self._lock:
            return self.conn.execute('SELECT COUNT(*) FROM downloads').fetchone()[0]

    def delete(self, row_id):
        with self._lock:
            self.conn.execute('DELETE FROM downloads WHERE id=?', (row_id,))
            self.conn.commit()

    def clear(self):
        with self._lock:
            self.conn.execute('DELETE FROM downloads')
            self.conn.commit()
