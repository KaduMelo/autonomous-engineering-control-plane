#!/usr/bin/env python
"""A worker whose fixer is scripted, for demonstrating durability.

What the kill-resume demo proves is that a run survives its worker dying without
redoing finished work. The model is not under test there, and calling it would
bill real money to demonstrate something unrelated to it. This harness swaps the
FixerPort for a script: two wrong answers, then the right one.

It is a demo harness, not production code - `control_plane.worker` is the real
entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane.activities import propose  # noqa: E402
from control_plane.adapters.fixer import FixProposal  # noqa: E402
from control_plane.domain.models import Attempt, RepositoryContext  # noqa: E402
from control_plane.worker import run  # noqa: E402

TARGET = "src/calculator/pricing.py"

WRONG_ONE = """\
--- a/src/calculator/pricing.py
+++ b/src/calculator/pricing.py
@@ -25,4 +25,4 @@ def apply_discount(price: float, percent: float) -> float:
 
     `percent` is a percentage (10 means ten percent), not a fraction.
     \"\"\"
-    return price - percent
+    return price - percent / 100.0
"""

WRONG_TWO = WRONG_ONE.replace("price - percent / 100.0", "price - percent * 100.0")

RIGHT = WRONG_ONE.replace("price - percent / 100.0", "price * (1.0 - percent / 100.0)")

SCRIPT = [
    (WRONG_ONE, "subtract the percentage scaled down"),
    (WRONG_TWO, "subtract the percentage scaled up"),
    (RIGHT, "treat percent as a percentage of the price"),
]


# A real proposal takes tens of seconds. Without standing in for that latency
# the whole run finishes in a few seconds, the kill lands after it is over, and
# the demo "passes" while demonstrating nothing.
DELAY_S = float(os.environ.get("FIXER_DELAY_S", "8"))


class ScriptedFixer:
    """Wrong, wrong, right - so the loop has to iterate to reach green."""

    async def propose(
        self, context: RepositoryContext, history: list[Attempt]
    ) -> FixProposal:
        await asyncio.sleep(DELAY_S)
        diff, rationale = SCRIPT[min(len(history), len(SCRIPT) - 1)]
        return FixProposal(diff=diff, rationale=rationale, files_touched=[TARGET])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    propose.set_fixer(ScriptedFixer())
    logging.getLogger(__name__).info("worker running with a SCRIPTED fixer (no model calls)")
    asyncio.run(run())


if __name__ == "__main__":
    main()
