from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import pytest

@pytest.fixture
def future_ts() -> float:
    return datetime(2099, 1, 1).timestamp()

@pytest.fixture
def past_ts() -> float:
    return datetime(2000, 1, 1).timestamp()

@pytest.fixture
def fake_cookies(tmp_path: Path, future_ts: float, past_ts: float) -> Path:
    cookies = [
        {"name": "CASTGC", "value": "tgc_val", "domain": "sso.gatech.edu", "expires": future_ts},
        {"name": "JSESSIONID", "value": "jsess_val", "domain": "registration.banner.gatech.edu", "expires": past_ts},
        {"name": "untracked", "value": "x", "domain": "example.com", "expires": future_ts},
    ]
    p = tmp_path / "session.json"
    p.write_text(json.dumps(cookies))
    return p