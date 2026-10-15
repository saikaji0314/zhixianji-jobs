"""测试环境自身的回归测试。

本机环境的系统临时目录对 Python 子进程不可写，而 ``tempfile.gettempdir()`` 的候选列表
最后一项是 ``os.getcwd()``，会静默回落到当前目录。曾经因此把几十个临时目录建在仓库根目录里。
这些测试把这个约束固定下来：临时目录只能落在 data/_test-tmp 内，且测试不得依赖系统临时目录。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from . import REPO_ROOT, TEST_TMP_ROOT, temp_workdir


class TempWorkdirTest(unittest.TestCase):
    def test_workdir_is_inside_the_repo_temp_root(self) -> None:
        with temp_workdir() as path:
            self.assertEqual(path.parent.resolve(), TEST_TMP_ROOT.resolve())
            self.assertTrue(path.is_dir())

    def test_workdir_is_usable_for_reads_and_writes(self) -> None:
        with temp_workdir() as path:
            target = path / "sub" / "file.txt"
            target.parent.mkdir()
            target.write_text("内容", encoding="utf-8")
            self.assertEqual(target.read_text(encoding="utf-8"), "内容")

    def test_workdir_is_cleaned_up(self) -> None:
        with temp_workdir() as path:
            pass
        self.assertFalse(path.exists())

    def test_two_workdirs_do_not_collide(self) -> None:
        with temp_workdir() as first, temp_workdir() as second:
            self.assertNotEqual(first, second)

    def test_temp_root_is_not_the_repo_root(self) -> None:
        # 这条断言直接对着曾经的故障：临时目录根不能等于仓库根。
        self.assertNotEqual(TEST_TMP_ROOT.resolve(), REPO_ROOT.resolve())
        self.assertEqual(TEST_TMP_ROOT.parent.name, "data")


class NoStrayTempUsageTest(unittest.TestCase):
    def test_source_does_not_use_tempfile_without_explicit_dir(self) -> None:
        """仓库代码里不应出现不指定 dir 的 tempfile 调用（会回落到 cwd）。"""
        offenders: list[str] = []
        for base in (REPO_ROOT / "src", REPO_ROOT / "tests"):
            for path in base.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for needle in ("tempfile.mkdtemp(", "tempfile.TemporaryDirectory(", "tempfile.NamedTemporaryFile("):
                    if needle in text and "dir=" not in text:
                        offenders.append(f"{path.relative_to(REPO_ROOT)} 使用 {needle} 但没有 dir=")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_cwd_is_not_the_system_temp_dir(self) -> None:
        """若当前目录恰好等于系统临时目录，说明测试运行方式有问题。"""
        import tempfile

        self.assertNotEqual(Path(os.getcwd()).resolve(), Path(tempfile.gettempdir()).resolve())


if __name__ == "__main__":
    unittest.main()
