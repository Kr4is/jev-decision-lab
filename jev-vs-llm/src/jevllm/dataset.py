"""The 40 labeled tickets used by the dataset run (data/tickets.jsonl)."""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "tickets.jsonl"


def load_dataset(path: Path = DATA, limit: int | None = None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows[:limit] if limit else rows
