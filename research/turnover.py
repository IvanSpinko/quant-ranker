"""Turnover reduction: pick smoothing, holding buffer and rebalance frequency without peeking.

Run after research/stage1.py:  uv run python research/turnover.py
Uses the stage 1 out-of-sample LightGBM scores. The grid is judged on net Sharpe in the tuning
period only (2014-2018); the chosen configuration and the stage 1 configuration are then
compared once on the validation period (2019 to the hold-out). Every grid point is logged.
"""

import itertools
import json
import time
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import polars as pl

from quant_core.backtest import BacktestConfig, daily_returns, performance_summary, run_backtest
from quant_core.validation import ExperimentLog

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "results"

TUNING_END = date(2018, 12, 31)
BASE = BacktestConfig(fraction=0.1, rebalance_every=5, lag=1, commission_bps=1.0, slippage_bps=5.0)
GRID = {
    "smoothing_halflife": [0.0, 3.0, 10.0],
    "exit_fraction": [None, 0.2, 0.3],
    "rebalance_every": [5, 10, 21],
}
COMPARE_MODELS = ["lgbm", "ridge_lgbm", "combo"]


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def split(daily: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    return daily.filter(pl.col("date") <= TUNING_END), daily.filter(pl.col("date") > TUNING_END)


def main() -> None:
    experiments = ExperimentLog(ROOT / "research" / "experiments.jsonl")
    stocks = pl.read_parquet(ROOT / "data" / "raw" / "prices.parquet").filter(pl.col("ticker") != "SPY")
    returns = daily_returns(stocks)
    scores = {m: pl.read_parquet(RESULTS / f"scores_{m}.parquet") for m in COMPARE_MODELS}

    # Tuning: the whole grid, tuning-period metrics only.
    grid_rows = []
    for values in itertools.product(*GRID.values()):
        config = replace(BASE, **dict(zip(GRID, values)))
        daily = run_backtest(scores["lgbm"], returns, config).daily
        tuning, _ = split(daily)
        net, gross = performance_summary(tuning, "net"), performance_summary(tuning, "gross")
        params = {k: getattr(config, k) for k in GRID}
        grid_rows.append(params | {"net_sharpe": net["sharpe"], "net_cagr": net["cagr"],
                                   "gross_sharpe": gross["sharpe"], "turnover": net["annual_turnover"]})
        experiments.record("turnover/tuning/lgbm", asdict(config) | {"period_end": TUNING_END},
                           {"net": net, "gross": gross})
        log(f"{params}: tuning net Sharpe {net['sharpe']:+.2f}, turnover {net['annual_turnover']:.0f}")
    grid = pl.DataFrame(grid_rows).sort("net_sharpe", descending=True)
    grid.write_parquet(RESULTS / "turnover_grid.parquet")

    best = grid.row(0, named=True)
    chosen = replace(BASE, **{k: best[k] for k in GRID})
    log(f"chosen on tuning period: {({k: best[k] for k in GRID})}")

    # Validation: evaluated once, stage 1 configuration against the chosen one.
    comparison, curves = [], []
    for model in COMPARE_MODELS:
        for label, config in (("stage1", BASE), ("chosen", chosen)):
            daily = run_backtest(scores[model], returns, config).daily
            tuning, validation = split(daily)
            curves.append(daily.select("date", "net", "gross", "turnover").with_columns(
                model=pl.lit(model), config=pl.lit(label)))
            for period, frame in (("tuning", tuning), ("validation", validation)):
                net, gross = performance_summary(frame, "net"), performance_summary(frame, "gross")
                comparison.append({"model": model, "config": label, "period": period,
                                   "net_cagr": net["cagr"], "net_sharpe": net["sharpe"],
                                   "gross_sharpe": gross["sharpe"], "max_drawdown": net["max_drawdown"],
                                   "turnover": net["annual_turnover"]})
            if label == "chosen":
                experiments.record(f"turnover/validation/{model}", asdict(config) | {"period_start": TUNING_END},
                                   {"net": performance_summary(validation, "net"),
                                    "gross": performance_summary(validation, "gross")})
    comparison = pl.DataFrame(comparison)
    comparison.write_parquet(RESULTS / "turnover_comparison.parquet")
    pl.concat(curves).write_parquet(RESULTS / "turnover_daily.parquet")
    (RESULTS / "turnover_chosen.json").write_text(json.dumps(asdict(chosen), indent=2), encoding="utf-8")

    with pl.Config(tbl_rows=20, float_precision=3, tbl_width_chars=140):
        print(comparison)
    log(f"done; {experiments.count()} variants logged in total")


if __name__ == "__main__":
    main()
