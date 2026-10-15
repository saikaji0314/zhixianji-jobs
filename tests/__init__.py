"""测试包。

1. 把仓库的 src/ 加入 sys.path，使测试无需安装即可导入 zhixianji；
2. 提供仓库内的临时工作目录。

关于第 2 点，这里是实测结论，不是风格选择：

- 本机运行环境的系统临时目录（``TEMP``／``TMP`` 指向的 dsh 会话目录）对 Python 子进程不可写。
  ``tempfile.gettempdir()`` 的候选列表最后一项是 ``os.getcwd()``，因此它会**回落到当前目录**；
  任何不带 ``dir=`` 的 ``tempfile`` 调用都会把临时目录建在仓库根目录里，把仓库搞乱。
- ``tempfile.mkdtemp()`` 建出的目录在本环境里创建后即不可写、也不可删除
  （``icacls`` 甚至读不到它的权限项），而 ``Path.mkdir()`` 建出的目录可以正常读写。
  所以这里用 mkdir + 随机后缀，而不用 mkdtemp。

临时目录固定放在仓库的 data/_test-tmp 下，该目录被 .gitignore 忽略；
``temp_workdir()`` 会在返回前断言路径确实落在该目录内，避免再次污染仓库根目录。

运行方式（务必带 -t .，让测试以 tests 包的形式导入）：

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import uuid
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = REPO_ROOT / "data" / "_test-tmp"

SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@contextlib.contextmanager
def temp_workdir(prefix: str = "t-") -> Iterator[Path]:
    """在仓库内的 data/_test-tmp 下创建一个临时目录，退出时尽力清理。"""
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"{prefix}{uuid.uuid4().hex[:12]}"
    path.mkdir()
    # 环境异常时 tempfile 会回落到 cwd；这里显式挡住，宁可测试失败也不要污染仓库。
    if not path.parent.resolve() == TEST_TMP_ROOT.resolve():
        raise AssertionError(f"临时目录不在 {TEST_TMP_ROOT} 下：{path}")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

