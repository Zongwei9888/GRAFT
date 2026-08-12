from __future__ import annotations

import unittest
from pathlib import Path


class DomainNeutralityTests(unittest.TestCase):
    def test_product_runtime_contains_no_historical_task_routes(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "graft"
        product_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in source.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in {".py", ".json"}
        ).casefold()
        forbidden = (
            "terminal-bench",
            "postfix-payments",
            "bun-sourcemap-leak",
            "embedding-drift-monitor",
            "shadow-relay",
        )
        for task_route in forbidden:
            self.assertNotIn(task_route, product_text)


if __name__ == "__main__":
    unittest.main()
