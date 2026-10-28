"""Cloud pipeline on a local folder: ingest, predict and publish with a fake price vendor."""

import json
from datetime import UTC, date, datetime

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from quant_core.features import FEATURES, build_panel
from quant_core.models import LGBMModel
from quant_core.pipeline import (
    HISTORY_START,
    MARKET,
    PUBLISHED,
    UNIVERSE_KEY,
    ingest,
    last_closed_session,
    load_prices,
    predict,
    prepare,
    save_model,
    store_prices,
    train_live_model,
)
from quant_core.publish import parquet_bytes
from quant_core.storage import LocalStorage


@pytest.fixture
def vendor(synthetic):
    """All synthetic prices, the market as SPY, and an index of the 30 synthetic stocks."""
    market = synthetic["market"].with_columns(
        ticker=pl.lit(MARKET), open=pl.col("close"), high=pl.col("close"), low=pl.col("close"), volume=pl.lit(1e8)
    )
    prices = pl.concat([synthetic["prices"], market.select(synthetic["prices"].columns)])
    universe = synthetic["sectors"].with_columns(name=pl.col("ticker"), industry=pl.lit("x"))
    return prices, universe, synthetic["dates"]


class FakeVendor:
    def __init__(self, prices: pl.DataFrame, until: date):
        self.prices, self.until, self.calls = prices, until, []

    def __call__(self, tickers, start):
        self.calls.append((sorted(tickers), start))
        return self.prices.filter(pl.col("ticker").is_in(tickers), pl.col("date").is_between(start, self.until))


def after_close(day: date) -> datetime:
    """22:00 UTC: after the New York close, summer or winter."""
    return datetime(day.year, day.month, day.day, 22, tzinfo=UTC)


def bootstrap(storage, prices, universe, until):
    """What the backfill does: history, universe and a live model."""
    store_prices(storage, prices.filter(pl.col("date") <= until), merge=False)
    storage.put(UNIVERSE_KEY, parquet_bytes(universe))
    stocks, market = prepare(prices.filter(pl.col("date") <= until), universe)
    panel = build_panel(stocks, market, universe)
    save_model(storage, train_live_model(panel), panel["date"].max())


def manifest(storage):
    return json.loads(storage.get(PUBLISHED + "manifest.json"))


def test_predict_publishes_daily_files(tmp_path, vendor):
    prices, universe, dates = vendor
    storage = LocalStorage(tmp_path)
    bootstrap(storage, prices, universe, dates[300])
    result = predict(storage)
    assert result["data_date"] == dates[300].isoformat()
    m = manifest(storage)
    assert m["data_date"] == dates[300].isoformat()
    assert {"signals/latest.parquet", "features/latest.parquet", "explain/shap.parquet", "universe.parquet"} <= set(m["files"])
    assert f"returns/daily/{dates[300].year}.parquet" in m["files"]
    latest = pl.read_parquet(storage.get(PUBLISHED + "signals/latest.parquet"))
    assert latest.height == 30 and latest["rank"].to_list() == list(range(1, 31))
    assert MARKET not in set(latest["ticker"])
    for key, entry in m["files"].items():
        assert len(storage.get(PUBLISHED + key)) == entry["bytes"]


def test_predict_twice_changes_nothing(tmp_path, vendor):
    prices, universe, dates = vendor
    storage = LocalStorage(tmp_path)
    bootstrap(storage, prices, universe, dates[300])
    predict(storage)
    first = manifest(storage)
    predict(storage)
    assert manifest(storage)["files"] == first["files"]


def test_ingest_appends_and_predict_moves_forward(tmp_path, vendor):
    prices, universe, dates = vendor
    storage = LocalStorage(tmp_path)
    bootstrap(storage, prices, universe, dates[300])
    predict(storage)
    before = manifest(storage)["files"]

    fake = FakeVendor(prices, until=dates[330])
    result = ingest(storage, download=fake, fetch_universe=lambda: universe, now=after_close(dates[330]))
    assert result["latest_date"] == dates[330].isoformat() and result["new_tickers"] == []
    # Only recent days are requested, with an overlap so corrections replace older rows.
    (_, start), = fake.calls
    assert dates[290] <= start < dates[300]
    stored = load_prices(storage)
    assert stored["date"].max() == dates[330]
    assert stored.select(pl.struct("date", "ticker").is_duplicated().any()).item() is False
    assert_frame_equal(stored.sort("ticker", "date"), prices.filter(pl.col("date") <= dates[330]).sort("ticker", "date"), check_column_order=False)

    predict(storage)
    after = manifest(storage)
    assert after["data_date"] == dates[330].isoformat()
    assert after["files"]["signals/latest.parquet"] != before["signals/latest.parquet"]


def test_new_index_member_gets_full_history(tmp_path, vendor):
    prices, universe, dates = vendor
    storage = LocalStorage(tmp_path)
    old = universe.filter(pl.col("ticker") != "T05")
    bootstrap(storage, prices.filter(pl.col("ticker") != "T05"), old, dates[300])
    fake = FakeVendor(prices, until=dates[310])
    result = ingest(storage, download=fake, fetch_universe=lambda: universe, now=after_close(dates[310]))
    assert result["new_tickers"] == ["T05"]
    assert (["T05"], HISTORY_START) in fake.calls
    stored = load_prices(storage).filter(pl.col("ticker") == "T05")
    assert stored["date"].min() == dates[0]


def test_ingest_fails_loudly_without_data(tmp_path, vendor):
    prices, universe, dates = vendor
    storage = LocalStorage(tmp_path)
    bootstrap(storage, prices, universe, dates[300])
    with pytest.raises(RuntimeError, match="no prices"):
        ingest(storage, download=lambda tickers, start: prices.clear(), fetch_universe=lambda: universe, now=after_close(dates[310]))


def test_lgbm_round_trips_through_text(vendor):
    prices, universe, _ = vendor
    stocks, market = prepare(prices, universe)
    panel = build_panel(stocks, market, universe)
    model = train_live_model(panel)
    restored = LGBMModel.from_string(model.to_string())
    sample = panel.select(FEATURES).tail(200)
    assert restored.features == FEATURES
    np.testing.assert_allclose(restored.predict(sample), model.predict(sample))
    assert_frame_equal(restored.contributions(sample), model.contributions(sample))


def test_local_storage_rejects_escaping_keys(tmp_path):
    storage = LocalStorage(tmp_path / "root")
    with pytest.raises(ValueError):
        storage.put("../outside.txt", b"x")


def test_session_still_trading_is_dropped(tmp_path, vendor):
    prices, universe, dates = vendor
    storage = LocalStorage(tmp_path)
    bootstrap(storage, prices, universe, dates[300])
    during = datetime(dates[310].year, dates[310].month, dates[310].day, 15, tzinfo=UTC)  # 10:00-11:00 in New York
    assert last_closed_session(during) < dates[310]
    result = ingest(storage, download=FakeVendor(prices, until=dates[310]), fetch_universe=lambda: universe, now=during)
    assert result["latest_date"] == dates[309].isoformat()
    assert load_prices(storage)["date"].max() == dates[309]
