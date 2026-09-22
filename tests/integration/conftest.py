from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def seed_repo(tmp_path: Path) -> tuple[str, str]:
    """A fresh throwaway clone of the seed repository, plus its revision.

    Function-scoped on purpose: the branch tests mutate refs, and a shared repo
    would make them order-dependent.
    """
    root = Path(__file__).resolve().parents[2]
    dest = tmp_path / "repo"
    subprocess.run(
        [str(root / "scripts" / "seed_repo.sh"), str(dest)], check=True, capture_output=True
    )
    revision = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return str(dest), revision


_FIX_DIFF = """\
--- a/src/calculator/pricing.py
+++ b/src/calculator/pricing.py
@@ -25,4 +25,4 @@ def apply_discount(price: float, percent: float) -> float:
 
     `percent` is a percentage (10 means ten percent), not a fraction.
     \"\"\"
-    return price - percent
+    return price * (1.0 - percent / 100.0)
"""

_OTHER_DIFF = """\
--- a/src/calculator/pricing.py
+++ b/src/calculator/pricing.py
@@ -25,4 +25,4 @@ def apply_discount(price: float, percent: float) -> float:
 
     `percent` is a percentage (10 means ten percent), not a fraction.
     \"\"\"
-    return price - percent
+    return price * (100.0 - percent) / 100.0
"""


@pytest.fixture
def fix_diff() -> str:
    """A diff that makes the seed suite green."""
    return _FIX_DIFF


@pytest.fixture
def other_diff() -> str:
    """A different, also-correct fix - used to provoke a branch conflict."""
    return _OTHER_DIFF
