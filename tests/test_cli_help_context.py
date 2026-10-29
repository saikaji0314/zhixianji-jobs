"""Upstream context regression: listing stored batches is explicitly offline."""
import unittest
from zhixianji.cli import build_parser


class BatchesContextTests(unittest.TestCase):
    def test_global_short_help_keeps_offline_context(self):
        lines = build_parser().format_help().splitlines()
        row = next(line for line in lines if line.strip().startswith('batches '))
        self.assertIn('离线', row)


if __name__ == '__main__':
    unittest.main()
