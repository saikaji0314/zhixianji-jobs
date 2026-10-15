#!/usr/bin/env python3
"""仓库根目录的启动入口，免安装直接运行。

用法：
    python run.py validate --config config/search.local.json
    python run.py run --config config/search.local.json

等价于把 src/ 加入 sys.path 后执行 zhixianji.cli.main。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from zhixianji.cli import main  # noqa: E402  （必须先把 src 放进 sys.path）

if __name__ == "__main__":
    raise SystemExit(main())
