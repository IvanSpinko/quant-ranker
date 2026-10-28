"""Write the golden fixture that the Rust backtest must reproduce.

Run from the repository root after changing the Python backtest:  uv run python tests/make_golden.py
Inputs are synthetic; outputs come from quant_core.backtest, the reference implementation.
"""

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import polars as pl

from quant_core.backtest import BacktestConfig, performance_summary, run_backtest, yearly_returns

OUT = Path(__file__).resolve().parents[1] / "app" / "src-tauri" / "tests" / "golden" / "backtest.json"

CONFIGS = {
    "equal": BacktestConfig(fraction=0.2),
    "inverse_vol": BacktestConfig(fraction=0.2, weighting="inverse_vol", vol_window=10),
    "lag0_rebalance7": BacktestConfig(fraction=0.25, lag=0, rebalance_every=7),
    "buffer": BacktestConfig(fraction=0.2, exit_fraction=0.4),
    "smooth_buffer": BacktestConfig(fraction=0.1, exit_fraction=0.3, smoothing_halflife=4.0, commission_bps=2),
    "smooth_inverse_vol": BacktestConfig(fraction=0.15, smoothing_halflife=10.0, weighting="inverse_vol"),
}


def business_days(n: int) -> list:
    days = pl.date_range(pl.date(2021, 1, 1), pl.date(2023, 12, 31), "1d", eager=True)
    return days.filter(days.dt.weekday() <= 5)[:n].to_list()


def synthetic() -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(7)
    days = business_days(220)
    returns, scores = [], []
    for i in range(24):
        ticker = f"T{i:02d}"
        start = 40 if i in (3, 17) else 0  # late listings
        end = 180 if i == 9 else len(days)  # a delisting
        for k in range(start, end):
            returns.append((days[k], ticker, float(rng.normal(0.0004, 0.02))))
            if rng.random() > 0.05 and k >= 15:  # scores start later and have gaps
                scores.append((days[k], ticker, float(rng.normal())))
    schema = {"date": pl.Date, "ticker": pl.String}
    return (
        pl.DataFrame(returns, schema=schema | {"ret": pl.Float64}, orient="row"),
        pl.DataFrame(scores, schema=schema | {"score": pl.Float64}, orient="row"),
    )


def rows(frame: pl.DataFrame) -> list[dict]:
    return frame.with_columns(pl.col(pl.Date).dt.to_string()).to_dicts()


def main() -> None:
    returns, scores = synthetic()
    cases = []
    for name, config in CONFIGS.items():
        result = run_backtest(scores, returns, config)
        cases.append(
            {
                "name": name,
                "config": asdict(config),
                "daily": rows(result.daily),
                "weights": rows(result.weights.sort("signal_date", "ticker")),
                "summary": performance_summary(result.daily),
                "yearly": rows(yearly_returns(result.daily)),
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"returns": rows(returns), "scores": rows(scores), "cases": cases}, allow_nan=False),
        encoding="utf-8",
    )
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e3:.0f} kB, {len(cases)} cases)")


if __name__ == "__main__":
    main()
