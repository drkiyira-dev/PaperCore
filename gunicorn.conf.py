"""
PaperCore · 生产部署 gunicorn 配置（体验区上公网用）。

启动：
    gunicorn -c gunicorn.conf.py app:app

gunicorn 直接 import `app` 模块、取其中的 Flask 实例 `app`，**不走** app.py 末尾
`if __name__ == '__main__'` 的 app.run 启动段——所以端口/绑定由这里的 bind 决定，
不是 app.py 里的 PORT/HOST（那套是 `python app.py` 直跑时用的）。

形态开关仍靠环境变量：体验区部署要在 systemd/shell 里设 EXPERIENCE_MODE=1、
DEEPSEEK_API_KEY、EXPERIENCE_ADMIN_TOKEN（详见 部署指南.md）。
"""
import os

# 监听地址：反代（nginx）在前。默认绑全网 + 体验区端口 5004，由 nginx 转发到这里。
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:" + os.environ.get("PORT", "5004"))

# 进程数：docling + RapidOCR 很吃内存，按 VPS 内存给（4GB≈2，8GB≈3–4）。
# preload_app=True 让重型模型（docling）在 master 里只加载一次，再 fork 给各 worker
# （写时复制共享），显著省内存。本项目的 SQLite 连接都是按请求新开、不在 import 期打开，
# 所以 preload + fork 安全。
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
preload_app = True

# 单次请求可能较慢：docling 解析 +（体验区）云端 LLM 调用（最长 ~90s）。给足超时，
# 避免请求被 worker 超时杀掉。
timeout = 180
graceful_timeout = 30
keepalive = 5

# 日志走 stdout/stderr，交给 systemd/journald 收集。
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
accesslog = "-"
errorlog = "-"
