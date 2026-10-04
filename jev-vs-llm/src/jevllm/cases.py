"""The use cases shown on the landing page, and the recorded results presented next to them.

Cases live in web/cases.json. The results live in web/showcase.json: a static file with what JEV and the LLMs decided on
the cases and on the labeled dataset, recorded on the date it carries. It holds decisions only: no keys.
"""

from __future__ import annotations

import json
from pathlib import Path

WEB = Path(__file__).parent / "web"
CASES = WEB / "cases.json"
SHOWCASE = WEB / "showcase.json"


def load_cases(path: Path = CASES) -> list[dict]:
    return json.loads(path.read_text())["cases"]
