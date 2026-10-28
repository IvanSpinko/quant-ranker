"""Look-ahead tests: what is known at date t must not change when data after t is removed or altered."""

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from quant_core.data import align_to_calendar
from quant_core.features import FEATURES, add_target, build_panel, compute_features, cross_sectional_rank


def features_at(prices, market, sectors, cutoff):
    raw = compute_features(prices, market, sectors)
    return cross_sectional_rank(raw, FEATURES).filter(pl.col("date") <= cutoff).sort("date", "ticker")


@pytest.mark.parametrize("cut", [150, 260, 399])
def test_features_unchanged_when_future_is_cut(synthetic, cut):
    prices, market, sectors, dates = synthetic["prices"], synthetic["market"], synthetic["sectors"], synthetic["dates"]
    cutoff = dates[cut]
    full = features_at(prices, market, sectors, cutoff)
    truncated = features_at(
        prices.filter(pl.col("date") <= cutoff), market.filter(pl.col("date") <= cutoff), sectors, cutoff
    )
    assert_frame_equal(full, truncated)


def test_features_unchanged_when_future_is_scrambled(synthetic):
    prices, market, sectors, dates = synthetic["prices"], synthetic["market"], synthetic["sectors"], synthetic["dates"]
    cutoff = dates[300]
    rng = np.random.default_rng(0)
    future = pl.col("date") > cutoff
    scrambled = prices.with_columns(
        pl.when(future).then(pl.col(c) * pl.lit(rng.uniform(0.5, 2.0, len(prices)))).otherwise(pl.col(c)).alias(c)
        for c in ("close", "volume")
    )
    scrambled_market = market.with_columns(pl.when(future).then(pl.col("close") * 3).otherwise(pl.col("close")))
    assert_frame_equal(
        features_at(prices, market, sectors, cutoff),
        features_at(scrambled, scrambled_market, sectors, cutoff),
    )


def test_target_uses_only_the_label_window(synthetic):
    """The label at t may see closes up to t + lag + horizon, and nothing later."""
    prices, market, sectors, dates = synthetic["prices"], synthetic["market"], synthetic["sectors"], synthetic["dates"]
    lag, horizon, t = 1, 5, 200
    last_needed = dates[t + lag + horizon]
    full = build_panel(prices, market, sectors, horizon=horizon, lag=lag).filter(pl.col("date") <= dates[t])
    cut = build_panel(
        prices.filter(pl.col("date") <= last_needed), market.filter(pl.col("date") <= last_needed), sectors
    ).filter(pl.col("date") <= dates[t])
    assert_frame_equal(full, cut)


def test_target_matches_definition(synthetic):
    prices, market = synthetic["prices"], synthetic["market"]
    panel = add_target(prices.select("date", "ticker", "close"), market, horizon=5, lag=1)
    closes = prices.filter(pl.col("ticker") == "T01")["close"].to_numpy()
    mkt = market["close"].to_numpy()
    row = panel.filter(pl.col("ticker") == "T01").sort("date").row(10, named=True)
    expected = (closes[16] / closes[11] - 1) - (mkt[16] / mkt[11] - 1)
    assert row["excess_ret"] == pytest.approx(expected)


def test_align_fills_gaps_without_inventing_history(synthetic):
    prices, dates = synthetic["prices"], synthetic["dates"]
    holed = prices.filter(~((pl.col("ticker") == "T01") & (pl.col("date") == dates[50])))
    aligned = align_to_calendar(holed, pl.Series(dates))
    t01 = aligned.filter(pl.col("ticker") == "T01").sort("date")
    assert len(t01) == len(dates)
    filled = t01.filter(pl.col("date") == dates[50]).row(0, named=True)
    assert filled["filled"] and filled["volume"] == 0.0
    assert filled["close"] == t01.filter(pl.col("date") == dates[49])["close"].item()
    late = aligned.filter(pl.col("ticker") == "T00")
    assert late["date"].min() == prices.filter(pl.col("ticker") == "T00")["date"].min()
