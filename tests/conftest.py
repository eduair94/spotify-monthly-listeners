import sys
import time
from pathlib import Path

import pytest

# The tests import the tracker package from the repository root and helpers.py from here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture
def no_sdk_wait(monkeypatch):
    """The SDK client waits before each automatic retry: skip the waits."""

    monkeypatch.setattr(time, "sleep", lambda seconds: None)
