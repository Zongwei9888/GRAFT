from __future__ import annotations

import unittest

from graft.costing import stage_cost_from_turn
from graft.schema import TurnResult


class CostingTests(unittest.TestCase):
    def test_unknown_usage_remains_unknown_instead_of_zero(self) -> None:
        cost = stage_cost_from_turn(
            "stage",
            "modeling",
            TurnResult(None, "", (), {}, 0, "", 1.5),
        )
        self.assertFalse(cost.usage_known)
        self.assertIsNone(cost.input_tokens)
        self.assertIsNone(cost.estimated_cost_usd)

    def test_available_usage_is_normalized(self) -> None:
        cost = stage_cost_from_turn(
            "stage",
            "modeling",
            TurnResult(
                None,
                "",
                (),
                {
                    "input_tokens": 120,
                    "cached_input_tokens": 80,
                    "output_tokens": 15,
                    "estimated_cost_usd": 0.02,
                },
                0,
                "",
                2.0,
            ),
        )
        self.assertTrue(cost.usage_known)
        self.assertEqual(cost.input_tokens, 120)
        self.assertEqual(cost.output_tokens, 15)
        self.assertEqual(cost.estimated_cost_usd, 0.02)


if __name__ == "__main__":
    unittest.main()
