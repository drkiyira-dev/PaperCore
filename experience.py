"""
PaperCore · 体验区（公网试用）服务端 —— M1 体验区 MVP 的后端地基。

只在 EXPERIENCE_MODE=1（公网体验区形态）下启用；本地开源版（不设该变量）
完全不碰这里的任何逻辑，行为零变化。本模块四件事：

  1. 按访客配额   —— 每访客（签名前的随机 vid cookie）/ 每 IP / 全站，三层
                     24h 滚动窗口计数；防止「一个人用完全站锁死」(旧 usage.py
                     是全局单计数器，仅供答辩演示，不能上公网)。
  2. 全局熔断     —— 全站 24h 调用上限 = 生死线，防爆量/被刷把云账单冲爆。
  3. 留邮箱候补   —— 到限后收集邮箱，验证付费意愿（M1 的核心信号之一）。
  4. 成本埋点     —— 每次云调用记 token 与估算成本，产出定价校准所需真实数据
                     （M1 的另一核心产出）。

存储：本地单文件 data/experience.db，沿用 history.py 的 WAL + 短连接 + 失败
降级写法，和「数据不出本机」一致；与 history.db 分开，互不耦合。

配置（env 驱动，占位值由 2026-06-28 定价专场锁定）：
    EXPERIENCE_MODE              0/1，总开关
    EXPERIENCE_DAILY_QUOTA       每访客/24h，默认 5
    EXPERIENCE_IP_DAILY_CAP      每 IP/24h 软顶，默认 20（~4 人/IP，防清 cookie 刷量）
    EXPERIENCE_GLOBAL_DAILY_CAP  全站/24h 熔断，默认 2000（≈¥12/天）
    USD_CNY                      汇率，默认 7.1，用于成本估算

对外 API：check / record / log_cost / add_waitlist / stats / new_vid / is_on
"""

import os
import re
import time
import uuid
import hashlib
import sqlite3
import threading


# ──────────────────────────────────────────────── 配置（import 时读一次 env）

def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def is_on():
    """体验区是否开启。每次读 env，便于测试时动态切换。"""
    return os.environ.get("EXPERIENCE_MODE", "").strip().lower() in ("1", "true", "on", "yes")


DAILY_QUOTA = _int_env("EXPERIENCE_DAILY_QUOTA", 5)            # 每访客/24h
IP_DAILY_CAP = _int_env("EXPERIENCE_IP_DAILY_CAP", 20)         # 每 IP/24h 软顶
GLOBAL_DAILY_CAP = _int_env("EXPERIENCE_GLOBAL_DAILY_CAP", 2000)  # 全站/24h 熔断
WINDOW_SECONDS = 24 * 3600

# DeepSeek 实价（USD / 百万 token，2026-06 查实）。汇率 env 可调。
PRICE = {
    "flash": {"in": 0.14, "out": 0.28},    # deepseek-v4-flash（体验区/免费档）
    "v4pro": {"in": 1.74, "out": 3.48},    # deepseek-v4-pro（深度/Pro 档，促销已过期按原价）
}
USD_CNY = float(os.environ.get("USD_CNY", "7.1"))

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "experience.db")

_lock = threading.Lock()      # 只护「一次性初始化」；写并发交给 SQLite（WAL）
_initialized = False


# ──────────────────────────────────────────────── 连接 / 初始化（仿 history.py）

def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_db():
    """建表（幂等）。双检锁保证只跑一次；失败不抛给业务，降级即可。"""
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
                        CREATE TABLE IF NOT EXISTS usage (
                            id  INTEGER PRIMARY KEY AUTOINCREMENT,
                            key TEXT NOT NULL,    -- 'vid:<uuid>' / 'ip:<hash>' / 'global'
                            ts  INTEGER NOT NULL  -- epoch 秒
                        )
                    """)
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_usage_key_ts ON usage(key, ts)")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS waitlist (
                            id         INTEGER PRIMARY KEY AUTOINCREMENT,
                            email      TEXT NOT NULL UNIQUE,
                            created_at INTEGER NOT NULL,
                            source     TEXT,
                            vid_hash   TEXT
                        )
                    """)
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS cost_log (
                            id                INTEGER PRIMARY KEY AUTOINCREMENT,
                            ts                INTEGER NOT NULL,
                            vid_hash          TEXT,
                            engine            TEXT,
                            prompt_tokens     INTEGER,
                            completion_tokens INTEGER,
                            est_cost_cny      REAL,
                            ok                INTEGER
                        )
                    """)
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_cost_ts ON cost_log(ts)")
            finally:
                conn.close()
            _initialized = True
        except Exception as e:
            print(f"[experience] 初始化失败（体验区本次降级）：{e}")


# ──────────────────────────────────────────────── 小工具

def new_vid():
    """生成一个新访客 id（明文随机 UUID，由 app.py 下发为 cookie）。
    vid 不需防篡改：伪造随机 vid 等价于清 cookie，已由 IP 软顶兜住。"""
    return uuid.uuid4().hex


def _h(s):
    """取短哈希；IP / vid 入库只存哈希，不留明文（隐私）。"""
    if not s:
        return None
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()[:16]


def _count(conn, key, since):
    row = conn.execute(
        "SELECT COUNT(*) FROM usage WHERE key=? AND ts>=?", (key, since)).fetchone()
    return row[0] if row else 0


def _reset_at(conn, key, since):
    """该 key 配额回补时刻 = 窗口内最早一次 + 窗口长度。"""
    row = conn.execute(
        "SELECT MIN(ts) FROM usage WHERE key=? AND ts>=?", (key, since)).fetchone()
    oldest = row[0] if row and row[0] else None
    return (oldest + WINDOW_SECONDS) if oldest else None


# ──────────────────────────────────────────────── 配额：check / record

def check(vid, ip):
    """这个访客现在还能不能再分析一篇（24h 滚动窗口）。

    返回 dict：
        can_use     bool
        reason      'ok' | 'global'(熔断) | 'visitor'(个人到限) | 'ip'(IP到限)
                    | 'off'(未开体验区) | 'error'(降级放行)
        remaining   今日该访客剩余篇数（None=未知）
        daily_quota 每访客每日配额
        reset_at    到限时的回补时刻（epoch 秒，可选）

    异常一律降级为放行：体验区可用性优先，且单篇成本仅 ~¥0.006，
    短暂故障多放几篇也不致命；真正的成本兜底是全局熔断这层。
    """
    if not is_on():
        return {"can_use": True, "reason": "off", "remaining": None, "daily_quota": DAILY_QUOTA}
    try:
        _init_db()
        since = int(time.time()) - WINDOW_SECONDS
        conn = _connect()
        try:
            # ① 全局熔断（生死线，最先查）
            if _count(conn, "global", since) >= GLOBAL_DAILY_CAP:
                return {"can_use": False, "reason": "global", "remaining": 0,
                        "daily_quota": DAILY_QUOTA}
            # ② 每访客
            vkey = f"vid:{vid}" if vid else None
            v_used = _count(conn, vkey, since) if vkey else 0
            if vkey and v_used >= DAILY_QUOTA:
                return {"can_use": False, "reason": "visitor", "remaining": 0,
                        "daily_quota": DAILY_QUOTA, "reset_at": _reset_at(conn, vkey, since)}
            # ③ 每 IP 软顶（防清 cookie 反复刷）
            ikey = f"ip:{_h(ip)}"
            if _count(conn, ikey, since) >= IP_DAILY_CAP:
                return {"can_use": False, "reason": "ip", "remaining": 0,
                        "daily_quota": DAILY_QUOTA, "reset_at": _reset_at(conn, ikey, since)}
            return {"can_use": True, "reason": "ok",
                    "remaining": max(0, DAILY_QUOTA - v_used), "daily_quota": DAILY_QUOTA}
        finally:
            conn.close()
    except Exception as e:
        print(f"[experience] check 失败（放行兜底）：{e}")
        return {"can_use": True, "reason": "error", "remaining": None, "daily_quota": DAILY_QUOTA}


def record(vid, ip):
    """一次云端分析成功后记账：vid + ip + global 各记一条。

    调用顺序应为：①check 通过 → ②真调用成功 → ③record（失败不记，不扣额）。
    顺手清理窗口外旧记录，防 usage 表无限增长。失败不抛。
    """
    if not is_on():
        return
    try:
        _init_db()
        ts = int(time.time())
        rows = [("global", ts), (f"ip:{_h(ip)}", ts)]
        if vid:
            rows.append((f"vid:{vid}", ts))
        conn = _connect()
        try:
            with conn:
                conn.executemany("INSERT INTO usage (key, ts) VALUES (?,?)", rows)
                conn.execute("DELETE FROM usage WHERE ts < ?", (ts - WINDOW_SECONDS,))
        finally:
            conn.close()
    except Exception as e:
        print(f"[experience] record 失败：{e}")


# ──────────────────────────────────────────────── 成本埋点

def log_cost(vid, engine, prompt_tokens, completion_tokens, ok=True):
    """记一次云调用的 token 与估算成本（¥）。engine ∈ {'flash','v4pro'}。失败不抛。"""
    if not is_on():
        return
    try:
        _init_db()
        p = PRICE.get(engine, PRICE["flash"])
        est = ((prompt_tokens or 0) / 1e6 * p["in"]
               + (completion_tokens or 0) / 1e6 * p["out"]) * USD_CNY
        conn = _connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO cost_log (ts, vid_hash, engine, prompt_tokens, "
                    "completion_tokens, est_cost_cny, ok) VALUES (?,?,?,?,?,?,?)",
                    (int(time.time()), _h(vid), engine, prompt_tokens or 0,
                     completion_tokens or 0, round(est, 6), 1 if ok else 0))
        finally:
            conn.close()
    except Exception as e:
        print(f"[experience] log_cost 失败：{e}")


# ──────────────────────────────────────────────── 留邮箱候补

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def add_waitlist(email, source="limit", vid=None):
    """到限留邮箱。返回 (ok, msg)。重复邮箱视为成功（幂等，INSERT OR IGNORE）。

    msg ∈ {'ok','invalid','off','error'}。按 IP 限频由 app.py 那层做。
    """
    if not is_on():
        return (False, "off")
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 200:
        return (False, "invalid")
    try:
        _init_db()
        conn = _connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO waitlist (email, created_at, source, vid_hash) "
                    "VALUES (?,?,?,?)",
                    (email, int(time.time()), source, _h(vid)))
        finally:
            conn.close()
        return (True, "ok")
    except Exception as e:
        print(f"[experience] add_waitlist 失败：{e}")
        return (False, "error")


# ──────────────────────────────────────────────── 聚合统计（喂 /admin/stats）

def stats(window_seconds=WINDOW_SECONDS):
    """窗口内聚合：分析数 / 独立访客 / tokens / 估算成本¥ / 留邮箱数。

    这是 M1 的核心产出——用真实成本与付费意愿数据校准定价。失败返回 {}。
    """
    try:
        _init_db()
        since = int(time.time()) - window_seconds
        conn = _connect()
        try:
            analyses = _count(conn, "global", since)
            uniq = conn.execute(
                "SELECT COUNT(DISTINCT key) FROM usage "
                "WHERE key LIKE 'vid:%' AND ts>=?", (since,)).fetchone()[0]
            c = conn.execute(
                "SELECT COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
                "COALESCE(SUM(est_cost_cny),0), COUNT(*) FROM cost_log WHERE ts>=?",
                (since,)).fetchone()
            wl_new = conn.execute(
                "SELECT COUNT(*) FROM waitlist WHERE created_at>=?", (since,)).fetchone()[0]
            wl_total = conn.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0]
        finally:
            conn.close()
        cost = round(c[2], 4)
        return {
            "window_hours": round(window_seconds / 3600, 1),
            "analyses": analyses,
            "unique_visitors": uniq,
            "prompt_tokens": c[0],
            "completion_tokens": c[1],
            "cost_calls": c[3],
            "est_cost_cny": cost,
            "avg_cost_cny": round(cost / c[3], 4) if c[3] else 0.0,
            "waitlist_new": wl_new,
            "waitlist_total": wl_total,
            "daily_quota": DAILY_QUOTA,
            "ip_cap": IP_DAILY_CAP,
            "global_cap": GLOBAL_DAILY_CAP,
        }
    except Exception as e:
        print(f"[experience] stats 失败：{e}")
        return {}
