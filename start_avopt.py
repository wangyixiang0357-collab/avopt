"""ASCII 文件名供 Windows 批处理启动，避免控制台代码页破坏中文入口名称。"""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("农光互补布局优化系统.py")), run_name="__main__")
