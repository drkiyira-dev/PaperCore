"""
PaperCore · 上传文件「原始名」映射（local-first）

为什么需要它
------------
上传时为了安全，物理文件名要过 secure_filename——中文名「论文.pdf」会被剥成
「doc.pdf」（CJK 被丢光）。结果「我的文档」列表、「重新分析」里都显示成 doc.pdf，
既难看又认不出；而且「已分析 N 次」是拿 doc.pdf 去和历史里的真名对，永远对不上、恒显 0。

这里把「safe 物理名 → 用户原始名」单独存一份小映射，展示时还原真名。
物理文件名仍保持 ASCII-safe（不动既有的防穿越逻辑），只是旁路加一张对照表。

存储就是本地一个小 json（data/doc_names.json），不上云、随 data/ 一起被 .gitignore 排除，
与 PaperCore「数据不出本机」一致。单用户本地场景，进程内一把锁 + 原子写足够稳。

对外 API：remember / lookup / forget
"""

import os
import json
import threading

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE_DIR, 'data')
PATH = os.path.join(DATA_DIR, 'doc_names.json')

_lock = threading.Lock()


def _load():
    """读映射表；文件不存在 / 损坏时返回空 dict（绝不抛，展示链要稳）。"""
    try:
        with open(PATH, encoding='utf-8') as fp:
            data = json.load(fp)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _atomic_write(obj):
    """临时文件 + os.replace 原子替换，避免写到一半崩溃损坏映射表。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = f"{PATH}.tmp"
    with open(tmp, 'w', encoding='utf-8') as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)


def remember(safe_filename, original_name):
    """记下 safe 物理名 → 原始名。失败只打日志、不抛——绝不能影响上传主流程。"""
    if not safe_filename or not original_name:
        return
    try:
        with _lock:
            mapping = _load()
            mapping[safe_filename] = original_name
            _atomic_write(mapping)
    except Exception as e:
        print(f"[docnames] 记录失败（不影响上传）：{e}")


def lookup(safe_filename, default=None):
    """查原始名；查不到 / 出错返回 default（调用方一般回退到去前缀的名字）。"""
    if not safe_filename:
        return default
    try:
        return _load().get(safe_filename, default)
    except Exception:
        return default


def forget(safe_filename):
    """删文件时同步清掉映射，别让表无限堆旧条目。"""
    if not safe_filename:
        return
    try:
        with _lock:
            mapping = _load()
            if safe_filename in mapping:
                mapping.pop(safe_filename, None)
                _atomic_write(mapping)
    except Exception as e:
        print(f"[docnames] 清除失败：{e}")
