"""Append-only log of every evaluated model variant.

Trying many variants and reporting the best one inflates results; keeping the count makes the
multiple-testing problem visible when reading the findings.
"""

import json
from datetime import UTC, datetime
from pathlib import Path


class ExperimentLog:
    def __init__(self, path: Path):
        self.path = Path(path)

    def record(self, name: str, params: dict, metrics: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "name": name,
            "params": params,
            "metrics": metrics,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def count(self) -> int:
        return len(self.entries())
