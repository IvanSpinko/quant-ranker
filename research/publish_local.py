"""Write the published file set to data/published/ for the desktop app, without the cloud.

Run after research/stage1.py and research/turnover.py:  uv run python research/publish_local.py

The same full publish as cloud/backfill.py, into a local folder. History scores are the
walk-forward out-of-sample scores, so they end before the hold-out. Today's signals come from a
LightGBM trained on every labelled date; that model is used only for the live ranking.
"""

import shutil
import time
from pathlib import Path

import polars as pl

from quant_core.pipeline import PUBLISHED, publish_all
from quant_core.publish import HOLDOUT_START, MODELS, research_meta
from quant_core.storage import LocalStorage

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESULTS = DATA / "results"


def main() -> None:
    started = time.time()
    shutil.rmtree(DATA / PUBLISHED, ignore_errors=True)
    prices = pl.read_parquet(DATA / "raw" / "prices.parquet")
    universe = pl.read_parquet(DATA / "universe.parquet")
    meta = research_meta(RESULTS, MODELS, HOLDOUT_START)
    manifest, _, _ = publish_all(LocalStorage(DATA), prices, universe, RESULTS, MODELS, meta)
    total = sum(f["bytes"] for f in manifest["files"].values())
    print(f"wrote {len(manifest['files'])} files, {total / 1e6:.1f} MB, data as of {manifest['data_date']}, in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
