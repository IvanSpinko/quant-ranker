"""Turnover controls: score smoothing and the holding buffer."""

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from quant_core.backtest import BacktestConfig, daily_returns, run_backtest, smooth_scores, target_weights

from conftest import business_days


def ranking(day, order):
    """One date's scores: the first ticker in order gets the highest score."""
    return pl.DataFrame({"date": [day] * len(order), "ticker": order, "score": np.arange(len(order), 0, -1.0)})


def held(weights, day, side):
    sign = pl.col("weight") > 0 if side > 0 else pl.col("weight") < 0
    return set(weights.filter(pl.col("signal_date") == day, sign)["ticker"])


TICKERS = [f"S{i:02d}" for i in range(20)]


def test_buffer_keeps_names_inside_the_exit_band():
    d0, d1, d2 = business_days(3)
    # Long top 10% (2 names), keep while in the top 30% (6 names); short mirrored.
    first = ranking(d0, TICKERS)
    # S00 slips to 5th (inside the band), S01 to 10th (outside); on the short side S18 stays
    # last and S19 rises to 10th from the bottom (outside).
    second = ranking(d1, ["S02", "S03", "S04", "S05", "S00", "S06", "S07", "S08", "S09", "S01",
                          "S19", "S10", "S11", "S12", "S13", "S14", "S15", "S16", "S17", "S18"])
    third = ranking(d2, list(reversed(TICKERS)))
    config = BacktestConfig(fraction=0.1, exit_fraction=0.3)
    weights = target_weights(pl.concat([first, second, third]), config)
    assert held(weights, d0, 1) == {"S00", "S01"}
    assert held(weights, d1, 1) == {"S00", "S02"}
    assert held(weights, d1, -1) == {"S17", "S18"}
    # A full reversal leaves the band entirely, so both sides are replaced.
    assert held(weights, d2, 1) == {"S19", "S18"}
    assert held(weights, d2, -1) == {"S00", "S01"}
    sums = weights.group_by("signal_date", long=pl.col("weight") > 0).agg(pl.col("weight").sum())
    assert sums["weight"].abs().to_numpy() == pytest.approx(1.0)


def test_buffer_equal_to_fraction_is_the_plain_rule(synthetic):
    returns = daily_returns(synthetic["prices"])
    scores = returns.select("date", "ticker", score=pl.col("ret"))
    plain = run_backtest(scores, returns, BacktestConfig(fraction=0.2))
    same = run_backtest(scores, returns, BacktestConfig(fraction=0.2, exit_fraction=0.2))
    assert_frame_equal(plain.daily, same.daily)


def test_buffer_and_smoothing_cut_turnover(synthetic):
    returns = daily_returns(synthetic["prices"])
    rng = np.random.default_rng(3)
    scores = returns.select("date", "ticker", score=pl.lit(rng.normal(size=returns.height)))
    base = run_backtest(scores, returns, BacktestConfig(fraction=0.2))
    buffered = run_backtest(scores, returns, BacktestConfig(fraction=0.2, exit_fraction=0.4))
    smoothed = run_backtest(scores, returns, BacktestConfig(fraction=0.2, smoothing_halflife=10))
    base_turnover = base.daily["turnover"].sum()
    assert buffered.daily["turnover"].sum() < 0.8 * base_turnover
    assert smoothed.daily["turnover"].sum() < 0.8 * base_turnover


def test_smoothing_is_past_only(synthetic):
    returns = daily_returns(synthetic["prices"])
    scores = returns.select("date", "ticker", score=pl.col("ret"))
    cutoff = synthetic["dates"][250]
    full = smooth_scores(scores, 5).filter(pl.col("date") <= cutoff).sort("date", "ticker")
    cut = smooth_scores(scores.filter(pl.col("date") <= cutoff), 5).sort("date", "ticker")
    assert_frame_equal(full, cut)


def test_smoothing_converges_to_a_constant_ranking():
    days = business_days(40)
    scores = pl.concat([ranking(d, TICKERS) for d in days])
    smoothed = smooth_scores(scores, 3).filter(pl.col("date") == days[-1]).sort("score", descending=True)
    assert smoothed["ticker"].to_list() == TICKERS
