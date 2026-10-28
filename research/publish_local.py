"""Write the published file set to data/published/ for the desktop app, until the cloud does it.

Run after research/stage1.py and research/turnover.py:  uv run python research/publish_local.py

History scores are the walk-forward out-of-sample scores, so they end before the hold-out.
Today's signals come from a LightGBM trained on every labelled date; that model is used only
for the live ranking and its contributions, never evaluated.
"""

import json
import shutil
import time
from pathlib import Path

import polars as pl

from quant_core.backtest import daily_returns
from quant_core.data import align_to_calendar, trading_calendar
from quant_core.features import FEATURES
from quant_core.models import LGBMModel
from quant_core.publish import latest_signals, shap_long, write_manifest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESULTS = DATA / "results"
OUT = DATA / "published"

MODELS = ["lgbm", "ridge_lgbm", "ridge", "combo", "momentum_12_1", "reversal_5"]
HOLDOUT_START = "2024-11-01"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def write(frame: pl.DataFrame, rel: str) -> None:
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path, compression="zstd")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    universe = pl.read_parquet(DATA / "universe.parquet")
    panel = pl.read_parquet(RESULTS / "panel.parquet")
    prices = pl.read_parquet(DATA / "raw" / "prices.parquet")
    aligned = align_to_calendar(prices, trading_calendar(prices))

    # Live model: all labelled dates, every fifth date as in the walk-forward runs.
    day = panel.select(pl.col("date").unique().sort()).with_row_index("day")
    train = panel.join(day, on="date").filter(pl.col("target").is_not_null(), pl.col("day") % 5 == 0)
    model = LGBMModel().fit(train.select(FEATURES), train["target"])
    latest = panel.filter(pl.col("date") == panel["date"].max())
    live = latest.select("date", "ticker", score=pl.Series(model.predict(latest.select(FEATURES))))
    log(f"live model trained on {train.height} rows through {train['date'].max()}, scoring {latest['date'].max()}")

    write(latest_signals(live, universe), "signals/latest.parquet")
    write(latest.select("date", "ticker", *FEATURES), "features/latest.parquet")
    write(shap_long(model.contributions(latest.select(FEATURES)), latest["ticker"]), "explain/shap.parquet")

    for name in MODELS:
        scores = pl.read_parquet(RESULTS / f"scores_{name}.parquet").select("date", "ticker", "score")
        write(scores.sort("date", "ticker"), f"signals/history/{name}.parquet")

    stocks = aligned.filter(pl.col("ticker") != "SPY")
    write(daily_returns(stocks).sort("date", "ticker"), "returns/daily.parquet")
    write(daily_returns(aligned.filter(pl.col("ticker") == "SPY")).select("date", "ret"), "returns/market.parquet")
    write(universe.select("ticker", "name", "sector", "industry"), "universe.parquet")

    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    write(
        pl.DataFrame(
            [
                {"model": m, **{f"ic_{k}": v for k, v in summary[m]["ic"].items()},
                 **{f"net_{k}": v for k, v in summary[m]["net"].items()},
                 **{f"gross_{k}": v for k, v in summary[m]["gross"].items()}}
                for m in MODELS
            ]
        ),
        "quality/models.parquet",
    )
    write(
        pl.concat([pl.read_parquet(RESULTS / f"ic_{m}.parquet").select("date", "ic", model=pl.lit(m)) for m in MODELS]),
        "quality/ic.parquet",
    )
    importance = pl.read_parquet(RESULTS / "lgbm_importance.parquet")
    write(
        importance.with_columns(share=pl.col("gain") / pl.col("gain").sum().over("test_start"))
        .group_by("feature")
        .agg(pl.col("share").mean())
        .sort("share", descending=True),
        "quality/importance.parquet",
    )

    history_dates = pl.read_parquet(RESULTS / "scores_lgbm.parquet")["date"]
    default_backtest = json.loads((RESULTS / "turnover_chosen.json").read_text(encoding="utf-8"))
    manifest = write_manifest(
        OUT,
        {
            "data_date": latest["date"].max(),
            "history_start": history_dates.min(),
            "history_end": history_dates.max(),
            "holdout_start": HOLDOUT_START,
            "models": MODELS,
            "features": FEATURES,
            "default_backtest": default_backtest,
        },
    )
    total = sum(f["bytes"] for f in manifest["files"].values())
    log(f"wrote {len(manifest['files'])} files, {total / 1e6:.1f} MB, to {OUT}")


if __name__ == "__main__":
    main()
