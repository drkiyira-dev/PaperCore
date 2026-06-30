"""pytest 公共配置。运行：`python -m pytest`（在项目根目录）。"""
import os
import sys

# 让 tests/ 能 import 顶层模块（app / semantic / experience）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 嵌入模型一律从本地缓存离线加载：有则用，无则 semantic.available()=False、相关用例自动跳过。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
