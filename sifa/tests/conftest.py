from __future__ import annotations

import os

TEST_API_KEY = "sifa-test-api-key-of-sufficient-length"

os.environ.setdefault("SIFA_API_KEYS", TEST_API_KEY)
