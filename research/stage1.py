"""Stage 1 research run: data -> features -> walk-forward models -> IC -> backtests.

Run from the repository root:  uv run python research/stage1.py
Downloads and caches prices under data/, writes results to data/results/ and appends every
evaluated variant to research/experiments.jsonl. The hold-out period is never touched here.
"""

import json
import time
from datetime import date
from pathlib import Path

import polars as pl

from quant_core.backtest import BacktestConfig, daily_returns, performance_summary, run_backtest
from quant_core.data import align_to_calendar, load_prices, load_universe, trading_calendar
from quant_core.features import FEATURES, build_panel
from quant_core.models import BASELINES, LGBMModel, RidgeModel, combine_scores
from quant_core.validation import ExperimentLog, daily_ic, ic_summary, walk_forward_predict, walk_forward_splits

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESULTS = DATA / "results"

START = date(2010, 1, 1)
HOLDOUT_START = date(2024, 11, 1)
HORIZON, LAG = 5, 1
SPLITS = {"min_train": 756, "test_size": 63, "purge": HORIZON + LAG, "embargo": 5}
BACKTEST = BacktestConfig(fraction=0.1, rebalance_every=5, lag=LAG, commission_bps=1.0, slippage_bps=5.0)


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def load_data() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    universe = load_universe(DATA / "universe.parquet")
    prices = load_prices(DATA / "raw" / "prices.parquet", [*universe["ticker"], "SPY"], START)
    aligned = align_to_calendar(prices, trading_calendar(prices))
    market = aligned.filter(pl.col("ticker") == "SPY").select("date", "close")
    stocks = aligned.filter(pl.col("ticker") != "SPY")
    log(f"{stocks['ticker'].n_unique()} tickers, {market.height} days {market['date'].min()}..{market['date'].max()}")
    return universe, stocks, market


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    experiments = ExperimentLog(ROOT / "research" / "experiments.jsonl")

    universe, stocks, market = load_data()
    panel = build_panel(stocks, market, universe, horizon=HORIZON, lag=LAG)
    # The first year only warms up the 252-day features.
    panel = panel.filter(pl.col("mom_12_1").is_not_null().any().over("date"))
    panel.write_parquet(RESULTS / "panel.parquet")
    log(f"panel {panel.shape}")

    folds = walk_forward_splits(panel["date"], holdout_start=HOLDOUT_START, **SPLITS)
    log(f"{len(folds)} folds, tests {folds[0].test_start}..{folds[-1].test_end}")

    importances = []

    def keep_importance(fold, model):
        importances.append(model.feature_importance().with_columns(test_start=pl.lit(fold.test_start)))

    makers = dict(BASELINES) | {"ridge": RidgeModel, "lgbm": LGBMModel}
    scores = {}
    for name, make in makers.items():
        started = time.time()
        hook = keep_importance if name == "lgbm" else None
        scores[name] = walk_forward_predict(panel, FEATURES, make, folds, on_fold=hook)
        log(f"{name}: {time.time() - started:.0f}s")
    scores["ridge_lgbm"] = combine_scores({"ridge": scores["ridge"], "lgbm": scores["lgbm"]})
    pl.concat(importances).write_parquet(RESULTS / "lgbm_importance.parquet")

    returns = daily_returns(stocks)
    summary = {}
    for name, frame in scores.items():
        frame.write_parquet(RESULTS / f"scores_{name}.parquet")
        ic = daily_ic(frame, panel)
        ic.write_parquet(RESULTS / f"ic_{name}.parquet")
        result = run_backtest(frame, returns, BACKTEST)
        result.daily.write_parquet(RESULTS / f"backtest_{name}.parquet")
        metrics = {
            "ic": ic_summary(ic, HORIZON),
            "net": performance_summary(result.daily, "net"),
            "gross": performance_summary(result.daily, "gross"),
        }
        summary[name] = metrics
        experiments.record(
            f"stage1/{name}",
            {"splits": SPLITS, "backtest": BACKTEST.__dict__, "holdout_start": HOLDOUT_START, "features": FEATURES},
            metrics,
        )
        log(
            f"{name:>14}: IC {metrics['ic']['mean_ic']:+.4f} IR {metrics['ic']['ic_ir']:+.3f} "
            f"| net Sharpe {metrics['net']['sharpe']:+.2f} CAGR {metrics['net']['cagr']:+.2%} "
            f"| gross Sharpe {metrics['gross']['sharpe']:+.2f}"
        )

    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log(f"done; {experiments.count()} variants logged in total")


if __name__ == "__main__":
    main()
