"""Files the desktop app reads: the layout of S3 published/, written to any directory.

Only derived data is written (scores, returns, ranked features, metrics), never raw vendor prices.
"""

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

MANIFEST_VERSION = 1


def latest_signals(scores: pl.DataFrame, universe: pl.DataFrame) -> pl.DataFrame:
    """Scores of the most recent date with rank 1 = best and pct in [0, 1], 1 = best."""
    last = scores["date"].max()
    day = scores.filter(pl.col("date") == last).drop_nulls("score")
    n = day.height
    return (
        day.join(universe.select("ticker", "name", "sector"), on="ticker", how="left")
        .with_columns(rank=pl.col("score").rank("ordinal", descending=True).cast(pl.Int32))
        .with_columns(pct=1 - (pl.col("rank") - 1) / max(n - 1, 1))
        .select("date", "ticker", "name", "sector", "score", "rank", "pct")
        .sort("rank")
    )


def shap_long(contributions: pl.DataFrame, tickers: pl.Series) -> pl.DataFrame:
    """ticker, feature, contribution; the base value is repeated per ticker as feature 'base_value'."""
    return (
        contributions.with_columns(ticker=tickers)
        .unpivot(index="ticker", variable_name="feature", value_name="contribution")
        .sort("ticker", "feature")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(out_dir: Path, meta: dict) -> dict:
    """Hash every file under out_dir into manifest.json so clients download only what changed."""
    out_dir = Path(out_dir)
    files = {}
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            rel = path.relative_to(out_dir).as_posix()
            files[rel] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **meta,
        "files": files,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=_json_default), encoding="utf-8")
    return manifest


def _json_default(value):
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")
