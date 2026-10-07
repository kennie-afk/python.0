from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

TEST_API_KEY = "sifa-test-api-key-of-sufficient-length"

os.environ.setdefault("SIFA_API_KEYS", TEST_API_KEY)
# A test that builds the platform through the app must never write ./state.
os.environ.setdefault("SIFA_STATE_DIR", tempfile.mkdtemp(prefix="sifa-test-state-"))


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    from sifa.serving.auth import limiter

    limiter.reset()
    yield
    limiter.reset()
