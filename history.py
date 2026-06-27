"""
PaperCore · 分析历史本地存储（local-first / 隐私优先 · SQLite 版）

为什么从「JSON 文件」换成 SQLite
--------------------------------
旧版用 index.json + 每条一个 <id>.json：列表能秒开，但「索引」和「详情」
是两套文件，一致性全靠应用层手写的「临时文件 + os.replace」硬扛——记录一多，
两边写偏、半条损坏这类 bug 就得自己一个个堵。换成 SQLite 后，原子性交给
数据库自己的单文件事务（配 WAL）来保证，少一类自己造的麻烦，也给以后做
检索 / 分页 / 统计留了余地。

仍然没变的两件事（隐私叙事的根）
- 数据库就是本地一个文件 data/history.db，不上云、不入版本库（.gitignore 排除 data/），
  和 PaperCore「数据不出本机」一致。
- 「列表只读摘要、详情按需读」的两层观感保留：summary_json 很小，列表查询
  根本不碰大字段；data_json 存整条结果，回看时原样塞回 /result 重渲染。

对外 API 与旧版逐字一致（app.py 一行没改）：
    add_record / list_records / get_record / delete_record / clear_records

首次运行会把旧的 data/history/*.json 自动迁移进库——一次性、幂等、且**不删旧文件**
（留给你当备份）。迁过一次后由 meta 表打标记，之后启动不再重复扫。
"""

import os
import json
import time
import uuid
import sqlite3
import threading

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'history.db')

# 旧版文件存储的位置，仅用于一次性迁移（迁完保留，不删）
_LEGACY_DIR = os.path.join(DATA_DIR, 'history')
_LEGACY_INDEX = os.path.join(_LEGACY_DIR, 'index.json')

_lock = threading.Lock()       # 只用来保护「一次性初始化」，写并发交给 SQLite 自己
_initialized = False


# ---------------------------------------------------------------- 连接 / 初始化

def _connect():
    """每次操作开一条短连接。WAL 让「多读 + 单写」并发顺滑，busy_timeout 兜住瞬时锁。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')      # 崩溃安全 + 读写不互相阻塞
    conn.execute('PRAGMA synchronous=NORMAL')    # 配 WAL 足够耐崩，省一截 fsync
    conn.execute('PRAGMA busy_timeout=5000')     # 撞锁就等，不要立刻抛
    return conn


def _init_db():
    """建表（幂等）+ 首次迁移旧历史。双检锁保证多线程下只跑一次；失败不抛给业务。"""
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        try:
            conn = _connect()
            try:
                with conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS records (
                            id           TEXT PRIMARY KEY,
                            timestamp    INTEGER NOT NULL,
                            summary_json TEXT NOT NULL,   -- 列表页要的轻量摘要
                            data_json    TEXT NOT NULL    -- 整条分析结果，回看用
                        )
                    """)
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS meta (
                            key   TEXT PRIMARY KEY,
                            value TEXT
                        )
                    """)
                    conn.execute(
                        'CREATE INDEX IF NOT EXISTS idx_records_ts '
                        'ON records(timestamp DESC)')
                    _migrate_legacy(conn)
            finally:
                conn.close()
            _initialized = True
        except Exception as e:
            # 初始化失败也不能拖垮 app：历史功能降级，下次调用再试
            print(f"[history] 初始化失败（历史功能本次降级）：{e}")


def _ts_from_id(rec_id):
    """旧 id 形如 '1781679293_27d172'，前半段就是 epoch 秒。"""
    try:
        return int(str(rec_id).split('_', 1)[0])
    except (ValueError, IndexError):
        return int(time.time())


def _migrate_legacy(conn):
    """把旧 JSON 历史搬进库。幂等：meta 打过标记就跳过；逐条 INSERT OR IGNORE 防重。"""
    row = conn.execute(
        "SELECT value FROM meta WHERE key='migrated_filestore'").fetchone()
    if row and row['value'] == '1':
        return

    migrated = 0
    try:
        if os.path.isfile(_LEGACY_INDEX):
            with open(_LEGACY_INDEX, encoding='utf-8') as fp:
                idx = json.load(fp)
            for summ in (idx if isinstance(idx, list) else []):
                rec_id = summ.get('id')
                if not rec_id:
                    continue
                detail_path = os.path.join(_LEGACY_DIR, f"{rec_id}.json")
                if not os.path.isfile(detail_path):
                    continue  # 详情丢了就跳过——没 data 的记录点开会 404，不如不迁
                try:
                    with open(detail_path, encoding='utf-8') as fp:
                        data = json.load(fp)
                except (json.JSONDecodeError, OSError):
                    continue
                ts = int(summ.get('timestamp') or 0) or _ts_from_id(rec_id)
                summary = _summary_from(rec_id, ts, data)  # 重算，口径和新记录统一
                conn.execute(
                    "INSERT OR IGNORE INTO records "
                    "(id, timestamp, summary_json, data_json) VALUES (?,?,?,?)",
                    (rec_id, ts,
                     json.dumps(summary, ensure_ascii=False),
                     json.dumps(data, ensure_ascii=False)))
                migrated += 1
    except Exception as e:
        print(f"[history] 旧历史迁移出错（已跳过，不影响使用）：{e}")

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('migrated_filestore','1')")
    if migrated:
        print(f"[history] 已迁移 {migrated} 条旧历史进 SQLite（原 JSON 文件保留未删）")


# ---------------------------------------------------------------- 摘要

def _summary_from(rec_id, ts, data):
    """从一次分析的完整 data 里抽出列表页要展示的轻量摘要。

    字段口径与前端 history.html 严格对应，改名要同步改前端。
    """
    stats = data.get('stats') or {}
    matches = data.get('matches') or []
    sal = [m.get('salience') for m in matches
           if isinstance(m.get('salience'), (int, float))]
    return {
        'id': rec_id,
        'timestamp': ts,  # epoch 秒
        'time_str': time.strftime('%Y-%m-%d %H:%M', time.localtime(ts)),
        'filename': data.get('filename') or '未知文件',
        'analysis_mode': data.get('analysis_mode') or 'structured',
        'text_length': data.get('text_length') or 0,
        'match_count': stats.get('total_matches', len(matches)),
        'keep_count': stats.get('keep_count', 0),
        'ai_engine_used': data.get('ai_engine_used'),
        'salience_avg': round(sum(sal) / len(sal), 3) if sal else None,
        'score_mode': data.get('score_mode'),                       # 评分档：teacher/expert/professor（老记录可能为 None）
        'score_cap': (data.get('quality_score') or {}).get('cap'),  # 该档总分上限（老师档体现滑块值）
    }


# ---------------------------------------------------------------- 对外 API

def add_record(data):
    """把一次分析结果（即 /api/upload 返回的 data）写入历史；返回该条摘要。

    调用方应自行 try/except 兜底——历史写入失败绝不能影响上传主流程。
    """
    _init_db()
    ts = int(time.time())
    rec_id = f"{ts}_{uuid.uuid4().hex[:6]}"
    summary = _summary_from(rec_id, ts, data)
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO records (id, timestamp, summary_json, data_json) "
                "VALUES (?,?,?,?)",
                (rec_id, ts,
                 json.dumps(summary, ensure_ascii=False),
                 json.dumps(data, ensure_ascii=False)))
    finally:
        conn.close()
    return summary


def list_records():
    """返回摘要数组（最新在前）。任何异常都降级为空列表——列表页要稳，绝不抛。"""
    try:
        _init_db()
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT summary_json FROM records "
                "ORDER BY timestamp DESC, id DESC").fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r['summary_json']))
            except (json.JSONDecodeError, TypeError):
                continue
        return out
    except Exception as e:
        print(f"[history] 读取列表失败：{e}")
        return []


def get_record(rec_id):
    """取某条完整结果（供回看）。找不到 / 损坏 / 出错都返回 None。"""
    try:
        _init_db()
        rec_id = os.path.basename(str(rec_id))  # 习惯性收敛；参数化查询本就不怕注入
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT data_json FROM records WHERE id=?", (rec_id,)).fetchone()
        finally:
            conn.close()
        return json.loads(row['data_json']) if row else None
    except Exception as e:
        print(f"[history] 读取记录失败：{e}")
        return None


def delete_record(rec_id):
    """删除单条。删到了返回 True，本就不存在 / 出错返回 False。"""
    try:
        _init_db()
        rec_id = os.path.basename(str(rec_id))
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM records WHERE id=?", (rec_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except Exception as e:
        print(f"[history] 删除失败：{e}")
        return False


def clear_records():
    """清空全部历史，返回删除条数。出错返回 0。"""
    try:
        _init_db()
        conn = _connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            conn.execute("DELETE FROM records")
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception as e:
        print(f"[history] 清空失败：{e}")
        return 0
