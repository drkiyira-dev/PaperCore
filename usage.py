"""
PaperCore · v4pro 高级模型滑动配额（local-first 持久化）

设计要点
--------
- 演示 freemium / 高级模型受限调用：**5 小时滚动窗口内最多 5 次**，
  超限后该选项强制锁定（前端灰显、后端拒调）。
- **「刷新无效」天然成立**：状态存在本地磁盘 `data/v4pro_usage.json`，
  不在浏览器 sessionStorage。重启浏览器、清缓存都不影响。
- 文件 = 时间戳数组（epoch 秒）。每次记录时先清掉窗口外的旧时间戳，
  数组不会无限长。原子写（临时文件 + os.replace）防中途崩坏。
- 调试后门：直接删 `data/v4pro_usage.json` 即重置（供演示前清场）。

对外 API：check_quota / record_use / reset / get_status
"""

import os
import json
import time
import uuid
import threading

WINDOW_SECONDS = 5 * 3600  # 5 小时滚动窗口
QUOTA_LIMIT = 5            # 窗口内最多 5 次

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE_DIR, 'data')
USAGE_PATH = os.path.join(_DATA_DIR, 'v4pro_usage.json')

_lock = threading.Lock()


def _ensure_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


def _atomic_write(path, obj):
    _ensure_dir()
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp, 'w', encoding='utf-8') as fp:
        json.dump(obj, fp)
    os.replace(tmp, path)


def _load_stamps():
    """读时间戳数组；缺失/损坏返回空数组。读时顺手过滤掉窗口外的旧时间戳。"""
    try:
        with open(USAGE_PATH, encoding='utf-8') as fp:
            data = json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    now = time.time()
    return [t for t in data if isinstance(t, (int, float)) and now - t < WINDOW_SECONDS]


def get_status():
    """返回 {used, remaining, limit, window_seconds, locked_until, can_use}，
    供 /api/status 暴露给前端做倒计时显示。
    locked_until = 锁定解除时间（epoch 秒）；未锁定时为 None。
    """
    stamps = _load_stamps()
    used = len(stamps)
    remaining = max(0, QUOTA_LIMIT - used)
    can_use = used < QUOTA_LIMIT
    oldest = min(stamps) if stamps else None
    newest = max(stamps) if stamps else None
    # 满额时，下一次解锁 = 最早那次调用 + 窗口
    locked_until = None if can_use else (oldest + WINDOW_SECONDS)
    # 即便未满额也给「回补 / 全部重置」时刻，供前端常驻显示重置时间（不只锁定时才有）：
    next_recover = (oldest + WINDOW_SECONDS) if stamps else None   # 下一格额度回补时刻（最早一次过期）
    full_reset   = (newest + WINDOW_SECONDS) if stamps else None   # 全部用量过期、回满 5/5 的时刻
    return {
        "used": used,
        "remaining": remaining,
        "limit": QUOTA_LIMIT,
        "window_seconds": WINDOW_SECONDS,
        "locked_until": locked_until,
        "next_recover": next_recover,
        "full_reset": full_reset,
        "can_use": can_use,
    }


def check_quota():
    """是否还能再调一次。同 get_status()['can_use']，提供更短的调用名。"""
    return get_status()["can_use"]


def record_use():
    """记一次调用。若已满额则不写、返回 False。
    主流程应：①先 check_quota → ②真调用 → ③再 record_use（成功后才记）。
    """
    with _lock:
        stamps = _load_stamps()
        if len(stamps) >= QUOTA_LIMIT:
            return False
        stamps.append(int(time.time()))
        _atomic_write(USAGE_PATH, stamps)
        return True


def reset():
    """清空全部使用记录（调试/演示前清场用）。"""
    with _lock:
        try:
            os.remove(USAGE_PATH)
        except FileNotFoundError:
            pass
    return True
