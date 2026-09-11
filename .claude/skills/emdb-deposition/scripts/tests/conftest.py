"""Shared test fixtures for emdb-deposition's scripts.

The scripts under test are standalone CLI files, not an installed package,
so importing them for testing needs a small dynamic-loading dance
(importlib.util.spec_from_file_location + module_from_spec + exec_module).
This used to be copy-pasted near-identically into every test file; it's
collected here once instead.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


def load_script_module(name: str):
    """Import scripts/<name>.py as a module named `name`, registered in
    sys.modules under that name. The sys.modules registration matters:
    without it, `patch("em_deposit.save_manifest")` imports a SECOND, fresh
    copy of the module and patches that, so the mock never reaches the
    module the test actually drives - the patch silently no-ops and the
    test passes vacuously."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fails_json(capsys):
    """Assert a call exits via the shared fail() contract: SystemExit code 1,
    exactly one JSON object on stdout with "success": false, and each given
    substring present in the error message. Returns the parsed object. One
    definition so the many call sites can't drift in how strictly they check
    the contract. Usage: `fails_json(lambda: cmd(...), "expected substring")`.
    """

    def _run(fn, *substrings: str) -> dict:
        with pytest.raises(SystemExit) as exc:
            fn()
        assert exc.value.code == 1
        obj = json.loads(capsys.readouterr().out)  # raises unless stdout is one JSON object
        assert obj["success"] is False
        for substring in substrings:
            assert substring in obj["error"], f"{substring!r} not in {obj['error']!r}"
        return obj

    return _run
