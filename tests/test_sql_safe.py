from __future__ import annotations

import unittest

from app.sql_safe import safe_eval, validate_sql
from app.sql_safe import safe_eval_filtered


class SqlSafeTests(unittest.TestCase):
    def test_validate_sql_whitelist(self):
        self.assertTrue(validate_sql("SELECT * FROM documents WHERE id = ?"))
        self.assertTrue(validate_sql("WITH x AS (SELECT 1) SELECT * FROM x"))
        self.assertFalse(validate_sql("DELETE FROM documents"))
        self.assertFalse(validate_sql("DROP TABLE documents"))

    def test_safe_eval_expression(self):
        self.assertEqual(safe_eval("1 + 2 * 3", {}), 7)
        self.assertEqual(safe_eval("min(1, 2, 3)", {}), 1)
        self.assertTrue(safe_eval("True and 3 > 2", {}))

    def test_safe_eval_blocks_unsafe_access(self):
        with self.assertRaises(ValueError):
            safe_eval("__import__('os').system('echo')", {})

    def test_safe_eval_filtered_limits_large_output(self):
        value = safe_eval_filtered("'x' * 5000", {}, max_str_length=10)
        self.assertEqual(value, "x" * 10)


if __name__ == "__main__":
    unittest.main()
