import sys
from pathlib import Path

# Ensure tests/ is on sys.path so bare imports work on all platforms
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from types import SimpleNamespace

from test_bsl_helpers import _make_bsl_fixture


@pytest.fixture
def bsl_env(tmp_path):
    """Shared BSL test environment with default CF fixture.

    Returns SimpleNamespace with:
        path  – tmp_path (pathlib.Path) where the CF structure lives
        bsl   – dict of BSL helper functions
        helpers – dict of generic helper functions
    """
    tmpdir = str(tmp_path)
    bsl, helpers = _make_bsl_fixture(tmpdir)
    return SimpleNamespace(path=tmp_path, bsl=bsl, helpers=helpers)
