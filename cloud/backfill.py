"""One-off upload of the history to the bucket: raw prices, universe, live model and the full
published set. Run after research/stage1.py and research/turnover.py, and after Terraform:

    uv run python cloud/backfill.py --bucket "$(terraform -chdir=cloud/terraform output -raw bucket)"

The daily Lambdas take over from there.
"""

import argparse
import time
from pathlib import Path

import boto3
import polars as pl

from quant_core.pipeline import UNIVERSE_KEY, publish_all, save_model, store_prices
from quant_core.publish import HOLDOUT_START, MODELS, parquet_bytes, research_meta
from quant_core.storage import S3Storage

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--profile", default="main", help="AWS CLI profile")
    args = parser.parse_args()

    started = time.time()
    session = boto3.Session(profile_name=args.profile)
    storage = S3Storage(args.bucket, client=session.client("s3"))
    prices = pl.read_parquet(DATA / "raw" / "prices.parquet")
    universe = pl.read_parquet(DATA / "universe.parquet")

    years = store_prices(storage, prices, merge=False)
    storage.put(UNIVERSE_KEY, parquet_bytes(universe))
    print(f"raw prices: {len(years)} yearly files {years[0]}..{years[-1]}")

    meta = research_meta(DATA / "results", MODELS, HOLDOUT_START)
    manifest, model, model_meta = publish_all(storage, prices, universe, DATA / "results", MODELS, meta)
    save_model(storage, model, model_meta["trained_through"])
    total = sum(f["bytes"] for f in manifest["files"].values())
    print(
        f"published {len(manifest['files'])} files ({total / 1e6:.1f} MB), data as of {manifest['data_date']}, "
        f"model trained through {model_meta['trained_through']}, in {time.time() - started:.0f}s"
    )


if __name__ == "__main__":
    main()
