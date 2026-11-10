"""历史：数据存在数据目录（%APPDATA%\\VideoDownloader）的 history.db（手机版在 App 私有目录），电脑版和手机版共用这份代码。

两种记录：
- downloads 下载记录：下载好的视频。用于「已下载过」提示、侧边栏「最近下载」和视频分区。
- parses 解析记录：解析过的链接（视频和网盘分享都记），同一个视频 / 分享只留一条，再解析时更新。
  用于「历史记录」页和网盘分区——网盘下载下来的文件常常会被移走、解压，所以网盘只记分享链接，点一下重新解析。
分区分两大类：视频分区（B站 / X / YouTube / 抖音 / 快手 + 自建）放下载好的视频；
网盘分区（百度网盘 / 夸克网盘 / 123云盘 / Mega + 自建）放网盘分享的解析记录。都按平台自动归入，可以移到同一类的其它分区。
重命名：只改软件里显示的名字（custom_title），不动文件本身。
pan_batches：网盘下载的批次（一批对应网盘引擎里的多个任务），给「下载」页用。
"""
import json
import os
import sqlite3
import threading
import time

from .config import DATA

DB_PATH = os.path.join(DATA, 'history.db')
SCHEMA = 3
VIDEO, PAN = 'video', 'pan'

# 内置分区：(平台键, 名称, 图标, 类别)；记录里的平台名 → 平台键
BUILTIN_SECTIONS = [('bilibili', 'B站', 'brand_bilibili', VIDEO), ('x', 'X', 'brand_x', VIDEO),
                    ('youtube', 'YouTube', 'brand_youtube', VIDEO), ('douyin', '抖音', 'brand_douyin', VIDEO),
                    ('kuaishou', '快手', 'brand_kuaishou', VIDEO),
                    ('baidu', '百度网盘', 'brand_baidupan', PAN), ('quark', '夸克网盘', 'brand_quark', PAN),
                    ('pan123', '123云盘', 'brand_123pan', PAN), ('mega', 'Mega', 'brand_mega', PAN)]
PLATFORM_KEYS = {'B站': 'bilibili', 'B站番剧': 'bilibili', 'X': 'x', 'YouTube': 'youtube', '抖音': 'douyin', '快手': 'kuaishou',
                 '百度网盘': 'baidu', '夸克网盘': 'quark', '123云盘': 'pan123', 'Mega': 'mega'}

_COLS = ('id', 'title', 'platform', 'uploader', 'url', 'quality', 'filepath', 'size', 'ts', 'section_id', 'orig_title',
         'kind', 'count')
_BATCH_COLS = ('id', 'provider', 'share_id', 'title', 'url', 'save_dir', 'task_ids', 'ts', 'state', 'record_id')
_PARSE_COLS = ('id', 'kind', 'pkey', 'title', 'orig_title', 'platform', 'uploader', 'url', 'info', 'count', 'size', 'ts',
               'section_id')


def like_arg(search):
    """搜索词 → LIKE 的参数：% 和 _ 是 LIKE 的通配符，按字面搜要转义（SQL 里配 ESCAPE '\\'）。"""
    return '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


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
        for col, typ in (('section_id', 'INTEGER'), ('custom_title', 'TEXT'), ('kind', "TEXT DEFAULT 'video'"),
                         ('count', 'INTEGER DEFAULT 0')):
            if col not in cols:
                self.conn.execute(f'ALTER TABLE downloads ADD COLUMN {col} {typ}')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, icon TEXT, platform TEXT,
            builtin INTEGER DEFAULT 0, sort INTEGER DEFAULT 0, ts REAL)''')
        if 'kind' not in {r[1] for r in self.conn.execute('PRAGMA table_info(sections)')}:
            self.conn.execute("ALTER TABLE sections ADD COLUMN kind TEXT DEFAULT 'video'")
        self.conn.execute('''CREATE TABLE IF NOT EXISTS pan_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, share_id TEXT, title TEXT, url TEXT, save_dir TEXT,
            task_ids TEXT, ts REAL, state TEXT DEFAULT 'active', record_id INTEGER)''')
        have = {r[0] for r in self.conn.execute('SELECT platform FROM sections WHERE builtin=1')}
        for i, (key, name, icon, kind) in enumerate(BUILTIN_SECTIONS):
            if key not in have:
                self.conn.execute('INSERT INTO sections (name, icon, platform, builtin, sort, ts, kind) VALUES (?,?,?,1,?,?,?)',
                                  (name, icon, key, i, time.time(), kind))
        if ver < 1:
            # 以前的记录按平台归入内置分区（只在升级时做一次，之后用户「移出分区」的不会被放回去）
            for plat, key in PLATFORM_KEYS.items():
                self.conn.execute('UPDATE downloads SET section_id=(SELECT id FROM sections WHERE builtin=1 AND platform=?) '
                                  'WHERE section_id IS NULL AND platform=?', (key, plat))
        self.conn.execute('''CREATE TABLE IF NOT EXISTS parses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, pkey TEXT, title TEXT, platform TEXT, uploader TEXT, url TEXT,
            info TEXT, count INTEGER DEFAULT 0, size INTEGER DEFAULT 0, ts REAL, custom_title TEXT, section_id INTEGER)''')
        self.conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_parse_key ON parses(pkey)')
        if ver < 3:
            # 2.0 测试版里网盘记的是「下载记录」，改成网盘分享的解析记录
            for (vkey, title, plat, url, ts, sec, custom, count, size) in self.conn.execute(
                    "SELECT vkey, title, platform, url, ts, section_id, custom_title, count, size FROM downloads "
                    "WHERE kind='pan' ORDER BY ts").fetchall():
                self.conn.execute('INSERT OR REPLACE INTO parses (kind, pkey, title, platform, uploader, url, info, count, '
                                  'size, ts, custom_title, section_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (PAN, vkey, title, plat, '', url, '', count or 0, size or 0, ts, custom, sec))
            self.conn.execute("DELETE FROM downloads WHERE kind='pan'")
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

    def add_record(self, vkey, title, platform, uploader, url, quality, filepath, size, kind=VIDEO, count=0):
        """写一条下载记录，返回记录 ID（手机版的 filepath 是系统分配的 content:// 地址）。
        同一个视频 / 分享之前放进过哪个分区、改过名字，新记录沿用；否则按平台放进内置分区。"""
        with self._lock:
            prev = self.conn.execute('SELECT section_id, custom_title FROM downloads WHERE vkey=? ORDER BY ts DESC LIMIT 1',
                                     (vkey,)).fetchone()
            if prev:
                section, custom = prev
            else:
                section, custom = self._platform_section(platform), None
            # 同一视频同一清晰度只留最新一条
            self.conn.execute('DELETE FROM downloads WHERE vkey=? AND quality=?', (vkey, quality))
            cur = self.conn.execute(
                'INSERT INTO downloads (vkey, title, platform, uploader, url, quality, filepath, size, ts, section_id, '
                'custom_title, kind, count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (vkey, title, platform, uploader, url, quality, filepath, size, time.time(), section, custom, kind, count))
            self.conn.commit()
            return cur.lastrowid

    def update_record(self, row_id, **fields):
        """改一条记录（网盘批次继续下完更多文件时更新文件数、大小）。记录已被删掉时返回 False。"""
        allowed = {k: v for k, v in fields.items() if k in ('quality', 'filepath', 'size', 'count', 'title')}
        if not allowed:
            return False
        with self._lock:
            cur = self.conn.execute(f'UPDATE downloads SET {", ".join(k + "=?" for k in allowed)} WHERE id=?',
                                    [*allowed.values(), int(row_id)])
            self.conn.commit()
            return cur.rowcount > 0

    def lookup_key(self, vkey):
        """按键查下载过的记录（网盘分享用 pan:平台:分享ID）：[{id, quality, filepath, ts, exists}]"""
        with self._lock:
            rows = self.conn.execute('SELECT id, quality, filepath, ts FROM downloads WHERE vkey=? ORDER BY ts DESC',
                                     (vkey,)).fetchall()
        return [{'id': i, 'quality': q, 'filepath': p, 'ts': ts, 'exists': bool(p) and os.path.exists(p)}
                for i, q, p, ts in rows]

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

    def recent(self, search='', limit=500, section=None, kind=None):
        """最近的下载记录。title 是显示用的名字（改过名就是新名字），orig_title 是原标题。
        section：None = 全部，0 = 没有分区的，其它 = 某个分区；kind：None = 两类都要，'video' / 'pan' = 只要一类。"""
        sql = ('SELECT id, COALESCE(NULLIF(custom_title, \'\'), title), platform, uploader, url, quality, filepath, size, ts, '
               "section_id, title, COALESCE(kind, 'video'), COALESCE(count, 0) FROM downloads")
        where, args = [], []
        if kind:
            where.append("COALESCE(kind, 'video')=?")
            args.append(kind)
        if search:
            where.append("(title LIKE ? ESCAPE '\\' OR custom_title LIKE ? ESCAPE '\\' OR uploader LIKE ? ESCAPE '\\' "
                         "OR platform LIKE ? ESCAPE '\\')")
            args += [like_arg(search)] * 4
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

    def count(self, section=None, kind=None):
        sql, args = 'SELECT COUNT(*) FROM downloads', []
        if section:
            sql, args = sql + ' WHERE section_id=?', [int(section)]
        elif kind:
            sql, args = sql + " WHERE COALESCE(kind, 'video')=?", [kind]
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
    def sections(self, kind=None):
        """[{id, name, icon, platform, builtin, count, kind}]，内置分区在前，自建的按创建先后。kind 为空 = 两类都要。
        count：视频分区是下载好的视频数，网盘分区是网盘分享的解析记录数。"""
        sql = ('SELECT s.id, s.name, s.icon, s.platform, s.builtin, '
               '(SELECT COUNT(*) FROM downloads d WHERE d.section_id=s.id) + '
               "(SELECT COUNT(*) FROM parses p WHERE p.section_id=s.id), COALESCE(s.kind, 'video') FROM sections s")
        args = []
        if kind:
            sql += " WHERE COALESCE(s.kind, 'video')=?"
            args.append(kind)
        with self._lock:
            rows = self.conn.execute(sql + ' ORDER BY s.builtin DESC, s.sort, s.id', args).fetchall()
        keys = ('id', 'name', 'icon', 'platform', 'builtin', 'count', 'kind')
        return [dict(zip(keys, r)) for r in rows]

    def create_section(self, name, icon='folder', kind=VIDEO):
        with self._lock:
            sort = self.conn.execute('SELECT COALESCE(MAX(sort), 0) + 1 FROM sections').fetchone()[0]
            cur = self.conn.execute('INSERT INTO sections (name, icon, platform, builtin, sort, ts, kind) '
                                    'VALUES (?,?,NULL,0,?,?,?)',
                                    (name.strip() or '新分区', icon or 'folder', sort, time.time(), kind))
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
            for table in ('downloads', 'parses'):
                rows = self.conn.execute(f'SELECT id, platform FROM {table} WHERE section_id=?', (int(section_id),)).fetchall()
                for rid, plat in rows:
                    self.conn.execute(f'UPDATE {table} SET section_id=? WHERE id=?', (self._platform_section(plat), rid))
            self.conn.execute('DELETE FROM sections WHERE id=?', (int(section_id),))
            self.conn.commit()
            return True

    # ------------------------------------------------------------ 解析记录
    def add_parse(self, kind, pkey, title, platform, uploader, url, info='', count=0, size=0):
        """记一条解析记录，返回 ID。同一个视频 / 分享只留一条：再解析时更新时间和信息，改过的名字和所在分区保留。
        网盘分享第一次解析时按平台放进内置的网盘分区。"""
        with self._lock:
            row = self.conn.execute('SELECT id FROM parses WHERE pkey=?', (pkey,)).fetchone()
            if row:
                self.conn.execute('UPDATE parses SET kind=?, title=?, platform=?, uploader=?, url=?, info=?, count=?, size=?, '
                                  'ts=? WHERE id=?', (kind, title, platform, uploader, url, info, count, size, time.time(),
                                                      row[0]))
                rid = row[0]
            else:
                section = self._platform_section(platform) if kind == PAN else None
                rid = self.conn.execute(
                    'INSERT INTO parses (kind, pkey, title, platform, uploader, url, info, count, size, ts, section_id) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (kind, pkey, title, platform, uploader, url, info, count, size, time.time(), section)).lastrowid
            self.conn.commit()
            return rid

    def parses(self, search='', limit=500, kind=None, section=None):
        """解析记录（最近的在前）。title 是显示用的名字，orig_title 是原标题。section：None = 不限，0 = 不在分区里的。"""
        sql = ("SELECT id, kind, pkey, COALESCE(NULLIF(custom_title, ''), title), title, platform, uploader, url, info, "
               'count, size, ts, section_id FROM parses')
        where, args = [], []
        if search:
            where.append("(title LIKE ? ESCAPE '\\' OR custom_title LIKE ? ESCAPE '\\' OR uploader LIKE ? ESCAPE '\\' "
                         "OR platform LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\')")
            args += [like_arg(search)] * 5
        if kind:
            where.append('kind=?')
            args.append(kind)
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
        return [dict(zip(_PARSE_COLS, r)) for r in rows]

    def parse_count(self, kind=None, section=None):
        sql, where, args = 'SELECT COUNT(*) FROM parses', [], []
        if kind:
            where.append('kind=?')
            args.append(kind)
        if section:
            where.append('section_id=?')
            args.append(int(section))
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        with self._lock:
            return self.conn.execute(sql, args).fetchone()[0]

    def rename_parse(self, row_id, title):
        """改解析记录显示的名字；留空 = 恢复原标题。"""
        with self._lock:
            self.conn.execute('UPDATE parses SET custom_title=? WHERE id=?', ((title or '').strip() or None, int(row_id)))
            self.conn.commit()

    def move_parses(self, row_ids, section_id):
        ids = [int(i) for i in row_ids]
        if ids:
            with self._lock:
                self.conn.execute(f'UPDATE parses SET section_id=? WHERE id IN ({",".join("?" * len(ids))})',
                                  [section_id, *ids])
                self.conn.commit()

    def delete_parse(self, row_id):
        with self._lock:
            self.conn.execute('DELETE FROM parses WHERE id=?', (int(row_id),))
            self.conn.commit()

    def clear_parses(self, kind=None):
        with self._lock:
            if kind:
                self.conn.execute('DELETE FROM parses WHERE kind=?', (kind,))
            else:
                self.conn.execute('DELETE FROM parses')
            self.conn.commit()

    # ------------------------------------------------------------ 网盘批次
    def add_batch(self, provider, share_id, title, url, save_dir, task_ids):
        with self._lock:
            cur = self.conn.execute(
                'INSERT INTO pan_batches (provider, share_id, title, url, save_dir, task_ids, ts, state) '
                "VALUES (?,?,?,?,?,?,?,'active')",
                (provider, share_id, title, url, save_dir, json.dumps(list(task_ids)), time.time()))
            self.conn.commit()
            return cur.lastrowid

    def batches(self, states=('active', 'done')):
        marks = ','.join('?' * len(states))
        with self._lock:
            rows = self.conn.execute(f'SELECT {", ".join(_BATCH_COLS)} FROM pan_batches WHERE state IN ({marks}) '
                                     'ORDER BY ts', list(states)).fetchall()
        out = []
        for r in rows:
            d = dict(zip(_BATCH_COLS, r))
            try:
                d['task_ids'] = json.loads(d['task_ids'] or '[]')
            except ValueError:
                d['task_ids'] = []
            out.append(d)
        return out

    def update_batch(self, batch_id, **fields):
        allowed = {k: (json.dumps(list(v)) if k == 'task_ids' else v) for k, v in fields.items()
                   if k in ('state', 'record_id', 'task_ids', 'title')}
        if allowed:
            with self._lock:
                self.conn.execute(f'UPDATE pan_batches SET {", ".join(k + "=?" for k in allowed)} WHERE id=?',
                                  [*allowed.values(), int(batch_id)])
                self.conn.commit()
