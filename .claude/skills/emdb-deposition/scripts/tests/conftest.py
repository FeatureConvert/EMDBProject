"""Shared test fixtures for emdb-deposition's scripts.

The scripts under test are standalone CLI files, not an installed package,
so importing them for testing needs a small dynamic-loading dance
(importlib.util.spec_from_file_location + module_from_spec + exec_module).
This used to be copy-pasted near-identically into every test file; it's
collected here once instead.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


def load_script_module(name: str):
    """Import scripts/<name>.py as a fresh module named `name`."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
