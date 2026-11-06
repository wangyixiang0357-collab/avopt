# -*- coding: utf-8 -*-
"""农光互补布局优化系统 V1.4 —— 启动入口。

双击运行或命令行执行：
    python 农光互补布局优化系统.py
浏览器将自动打开 http://127.0.0.1:8765/
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
runtime = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime")
if sys.version_info[:2] == (3, 12) and os.path.isdir(runtime):
    sys.path.insert(0, runtime)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from AVOpt.webapp import main

if __name__ == "__main__":
    port = 8765
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    main(port=port, open_browser=True)
