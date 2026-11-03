"""下载历史：记录下载过的视频，用于「已下载过」提示、历史记录页、侧边栏的「分区」和「最近下载」。
数据存在程序目录的 history.db（手机版在 App 私有目录），电脑版和手机版共用这份代码。

分区：内置 B站 / X / YouTube / 抖音 / 快手 五个，下载时按平台自动归入；用户也可以新建分区、把视频移过去。
重命名：只改软件里显示的名字（custom_title），不动文件本身。
"""
import os
import sqlite3
import threading
import time

from .config import ROOT

DB_PATH = os.path.join(ROOT, 'history.db')
SCHEMA = 1

# 内置分区：(平台键, 名称, 图标)；core.platform_name() 给出的平台名 → 平台键
BUILTIN_SECTIONS = [('bilibili', 'B站', 'brand_bilibili'), ('x', 'X', 'brand_x'), ('youtube', 'YouTube', 'brand_youtube'),
                    ('douyin', '抖音', 'brand_douyin'), ('kuaishou', '快手', 'brand_kuaishou')]
PLATFORM_KEYS = {'B站': 'bilibili', 'B站番剧': 'bilibili', 'X': 'x', 'YouTube': 'youtube', '抖音': 'douyin', '快手': 'kuaishou'}

_COLS = ('id', 'title', 'platform', 'uploader', 'url', 'quality', 'filepath', 'size', 'ts', 'section_id', 'orig_title')


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
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        ver = self.conn.execute('PRAGMA user_version').fetchone()[0]
        if ver >= SCHEMA:
            return
        cols = {r[1] for r in self.conn.execute('PRAGMA table_info(downloads)')}
        if 'section_id' not in cols:
            self.conn.execute('ALTER TABLE downloads ADD COLUMN section_id INTEGER')
        if 'custom_title' not in cols:
            self.conn.execute('ALTER TABLE downloads ADD COLUMN custom_title TEXT')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, icon TEXT, platform TEXT,
            builtin INTEGER DEFAULT 0, sort INTEGER DEFAULT 0, ts REAL)''')
        have = {r[0] for r in self.conn.execute('SELECT platform FROM sections WHERE builtin=1')}
        for i, (key, name, icon) in enumerate(BUILTIN_SECTIONS):
            if key not in have:
                self.conn.execute('INSERT INTO sections (name, icon, platform, builtin, sort, ts) VALUES (?,?,?,1,?,?)',
                                  (name, icon, key, i, time.time()))
        # 以前的记录按平台归入内置分区（只在升级时做一次，之后用户「移出分区」的不会被放回去）
        for plat, key in PLATFORM_KEYS.items():
            self.conn.execute('UPDATE downloads SET section_id=(SELECT id FROM sections WHERE builtin=1 AND platform=?) '
                              'WHERE section_id IS NULL AND platform=?', (key, plat))
        self.conn.execute(f'PRAGMA user_version={SCHEMA}')

    # ------------------------------------------------------------ 记录
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
        """写一条下载记录（手机版的 filepath 是系统分配的 content:// 地址）。
        同一个视频之前放进过哪个分区、改过名字，新记录沿用；否则按平台放进内置分区。"""
        with self._lock:
            prev = self.conn.execute('SELECT section_id, custom_title FROM downloads WHERE vkey=? ORDER BY ts DESC LIMIT 1',
                                     (vkey,)).fetchone()
            if prev:
                section, custom = prev
            else:
                section, custom = self._platform_section(platform), None
            # 同一视频同一清晰度只留最新一条
            self.conn.execute('DELETE FROM downloads WHERE vkey=? AND quality=?', (vkey, quality))
            self.conn.execute(
                'INSERT INTO downloads (vkey, title, platform, uploader, url, quality, filepath, size, ts, section_id, '
                'custom_title) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (vkey, title, platform, uploader, url, quality, filepath, size, time.time(), section, custom))
            self.conn.commit()

    def _platform_section(self, platform):
        key = PLATFORM_KEYS.get(platform or '')
        if not key:
            return None
        row = self.conn.execute('SELECT id FROM sections WHERE builtin=1 AND platform=?', (key,)).fetchone()
        return row[0] if row else None

    def lookup(self, info):
        """这个视频下载过的记录（最新在前）：[{quality, filepath, ts, exists}]"""
        with self._lock:
            rows = self.conn.execute(
                'SELECT quality, filepath, ts FROM downloads WHERE vkey=? ORDER BY ts DESC',
                (video_key(info),)).fetchall()
        return [{'quality': q, 'filepath': p, 'ts': ts, 'exists': bool(p) and os.path.exists(p)} for q, p, ts in rows]

    def recent(self, search='', limit=500, section=None):
        """最近的下载记录。title 是显示用的名字（改过名就是新名字），orig_title 是视频原标题。
        section：None = 全部，0 = 没有分区的，其它 = 某个分区。"""
        sql = ('SELECT id, COALESCE(NULLIF(custom_title, \'\'), title), platform, uploader, url, quality, filepath, size, ts, '
               'section_id, title FROM downloads')
        where, args = [], []
        if search:
            where.append('(title LIKE ? OR custom_title LIKE ? OR uploader LIKE ? OR platform LIKE ?)')
            args += [f'%{search}%'] * 4
        if section == 0:
            where.append('section_id IS NULL')
        elif section:
            where.append('section_id=?')
            args.append(int(section))
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY ts DESC LIMIT ?'
        args.append(limit)
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [dict(zip(_COLS, r)) for r in rows]

    def count(self, section=None):
        sql, args = 'SELECT COUNT(*) FROM downloads', []
        if section:
            sql, args = sql + ' WHERE section_id=?', [int(section)]
        with self._lock:
            return self.conn.execute(sql, args).fetchone()[0]

    def rename(self, row_id, title):
        """改显示的名字；留空 = 恢复成视频原标题。同一个视频的其它清晰度一起改。"""
        title = (title or '').strip() or None
        with self._lock:
            row = self.conn.execute('SELECT vkey FROM downloads WHERE id=?', (int(row_id),)).fetchone()
            if row:
                self.conn.execute('UPDATE downloads SET custom_title=? WHERE vkey=?', (title, row[0]))
                self.conn.commit()

    def move(self, row_ids, section_id):
        """把记录移进某个分区（section_id 为 None = 移出分区）。同一个视频的其它清晰度一起移。"""
        ids = [int(i) for i in row_ids]
        if not ids:
            return
        with self._lock:
            marks = ','.join('?' * len(ids))
            self.conn.execute(f'UPDATE downloads SET section_id=? WHERE vkey IN '
                              f'(SELECT vkey FROM downloads WHERE id IN ({marks}))', [section_id, *ids])
            self.conn.commit()

    def delete(self, row_id):
        with self._lock:
            self.conn.execute('DELETE FROM downloads WHERE id=?', (row_id,))
            self.conn.commit()

    def clear(self):
        with self._lock:
            self.conn.execute('DELETE FROM downloads')
            self.conn.commit()

    # ------------------------------------------------------------ 分区
    def sections(self):
        """[{id, name, icon, platform, builtin, count}]，内置分区在前，自建的按创建先后。"""
        with self._lock:
            rows = self.conn.execute(
                'SELECT s.id, s.name, s.icon, s.platform, s.builtin, '
                '(SELECT COUNT(*) FROM downloads d WHERE d.section_id=s.id) FROM sections s '
                'ORDER BY s.builtin DESC, s.sort, s.id').fetchall()
        keys = ('id', 'name', 'icon', 'platform', 'builtin', 'count')
        return [dict(zip(keys, r)) for r in rows]

    def create_section(self, name, icon='folder'):
        with self._lock:
            sort = self.conn.execute('SELECT COALESCE(MAX(sort), 0) + 1 FROM sections').fetchone()[0]
            cur = self.conn.execute('INSERT INTO sections (name, icon, platform, builtin, sort, ts) VALUES (?,?,NULL,0,?,?)',
                                    (name.strip() or '新分区', icon or 'folder', sort, time.time()))
            self.conn.commit()
            return cur.lastrowid

    def update_section(self, section_id, name=None, icon=None):
        with self._lock:
            if name is not None and name.strip():
                self.conn.execute('UPDATE sections SET name=? WHERE id=?', (name.strip(), int(section_id)))
            if icon:
                self.conn.execute('UPDATE sections SET icon=? WHERE id=? AND builtin=0', (icon, int(section_id)))
            self.conn.commit()

    def delete_section(self, section_id):
        """删除自建分区：里面的视频回到所属平台的内置分区（其它网站的就不在分区里），记录和文件都还在。内置分区不能删。"""
        with self._lock:
            row = self.conn.execute('SELECT builtin FROM sections WHERE id=?', (int(section_id),)).fetchone()
            if not row or row[0]:
                return False
            rows = self.conn.execute('SELECT id, platform FROM downloads WHERE section_id=?', (int(section_id),)).fetchall()
            for rid, plat in rows:
                self.conn.execute('UPDATE downloads SET section_id=? WHERE id=?', (self._platform_section(plat), rid))
            self.conn.execute('DELETE FROM sections WHERE id=?', (int(section_id),))
            self.conn.commit()
            return True
