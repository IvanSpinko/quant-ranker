"""Backtest on synthetic data where the answer is known in closed form."""

import math

import numpy as np
import polars as pl
import pytest

from quant_core.backtest import BacktestConfig, daily_returns, performance_summary, run_backtest, yearly_returns

from conftest import business_days

DAILY = {"A": 0.01, "B": 0.005, "C": -0.005, "D": -0.01}
SCORE = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}


def constant_market(n_days: int = 60):
    days = business_days(n_days)
    returns = pl.DataFrame(
        {"date": [d for d in days for _ in DAILY], "ticker": list(DAILY) * n_days, "ret": list(DAILY.values()) * n_days}
    )
    scores = pl.DataFrame(
        {"date": [d for d in days for _ in SCORE], "ticker": list(SCORE) * n_days, "score": list(SCORE.values()) * n_days}
    )
    return days, returns, scores


def test_spread_return_turnover_and_costs():
    days, returns, scores = constant_market()
    config = BacktestConfig(fraction=0.25, rebalance_every=5, lag=1, commission_bps=1, slippage_bps=4)
    result = run_backtest(scores, returns, config)
    daily = result.daily
    # Signal on day 0, trade at the close of day 1, first return on day 2.
    assert daily["date"][0] == days[1]
    first = daily.row(0, named=True)
    assert first["gross"] == 0.0
    assert first["turnover"] == pytest.approx(2.0)
    assert first["cost"] == pytest.approx(2.0 * 5e-4)
    rest = daily.slice(1)
    assert rest["gross"].to_numpy() == pytest.approx(0.02)
    assert rest["long"].to_numpy() == pytest.approx(0.01)
    assert rest["short"].to_numpy() == pytest.approx(0.01)
    assert rest["turnover"].sum() == pytest.approx(0.0)
    assert result.weights.group_by("signal_date").agg(pl.col("weight").sum())["weight"].to_numpy() == pytest.approx(0)


def test_no_return_is_earned_before_the_trade():
    """A huge return on the trade day itself must not reach the portfolio."""
    days, returns, scores = constant_market()
    returns = returns.with_columns(
        pl.when((pl.col("date") == days[1]) & (pl.col("ticker") == "A")).then(10.0).otherwise(pl.col("ret")).alias("ret")
    )
    daily = run_backtest(scores, returns, BacktestConfig(fraction=0.25, lag=1)).daily
    assert daily["gross"].max() < 0.05


def test_lag_zero_trades_on_the_signal_close():
    days, returns, scores = constant_market()
    daily = run_backtest(scores, returns, BacktestConfig(fraction=0.25, lag=0)).daily
    assert daily["date"][0] == days[0]
    assert daily["gross"][1] == pytest.approx(0.02)


def test_rotation_turnover():
    """Flipping the ranking at every rebalance trades the whole book twice: turnover 4."""
    days, returns, scores = constant_market(30)
    flip = scores.join(pl.DataFrame({"date": days, "k": np.arange(30) // 5 % 2}), on="date").with_columns(
        score=pl.when(pl.col("k") == 1).then(-pl.col("score")).otherwise(pl.col("score"))
    )
    daily = run_backtest(flip, returns, BacktestConfig(fraction=0.25, rebalance_every=5, lag=0)).daily
    trades = daily.filter(pl.col("turnover") > 0)["turnover"].to_list()
    assert trades[0] == pytest.approx(2.0)
    assert trades[1:] == pytest.approx([4.0] * (len(trades) - 1))


def test_inverse_vol_weights_sum_to_one_per_side(synthetic):
    returns = daily_returns(synthetic["prices"])
    scores = returns.select("date", "ticker", score=pl.col("ret"))
    result = run_backtest(scores, returns, BacktestConfig(weighting="inverse_vol", fraction=0.2))
    sums = result.weights.group_by("signal_date", long=pl.col("weight") > 0).agg(pl.col("weight").sum())
    assert sums["weight"].abs().to_numpy() == pytest.approx(1.0)
    equal = run_backtest(scores, returns, BacktestConfig(weighting="equal", fraction=0.2))
    assert not np.allclose(result.daily["gross"].to_numpy(), equal.daily["gross"].to_numpy())


def test_performance_summary_on_known_series():
    days = business_days(504)
    r = np.full(504, 0.001)
    r[100] = -0.2
    daily = pl.DataFrame({"date": days, "net": r, "turnover": np.full(504, 0.1)})
    stats = performance_summary(daily)
    total = np.prod(1 + r)
    assert stats["years"] == pytest.approx(2.0)
    assert stats["cagr"] == pytest.approx(total**0.5 - 1)
    assert stats["max_drawdown"] == pytest.approx(-0.2)
    assert stats["annual_turnover"] == pytest.approx(25.2)
    assert stats["hit_rate"] == pytest.approx(503 / 504)
    assert stats["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))
    years = yearly_returns(daily)
    assert np.prod(1 + years["ret"].to_numpy()) == pytest.approx(total)
