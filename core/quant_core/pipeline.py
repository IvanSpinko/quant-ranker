"""Daily cloud pipeline: ingest prices, then predict and publish. Also the one-off backfill.

Storage layout (one bucket or folder):
  raw/universe.parquet         current index members (private)
  raw/prices/<year>.parquet    daily OHLCV per ticker, including SPY (private, vendor data)
  state/model/current.txt      live LightGBM model; state/model/meta.json describes it
  published/...                what the app downloads, see quant_core.publish
"""

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from quant_core.data import align_to_calendar, download_prices, fetch_sp500, trading_calendar
from quant_core.features import FEATURES, build_panel
from quant_core.models import LGBMModel
from quant_core.publish import daily_frames, parquet_bytes, publish, research_frames, split_by_year
from quant_core.storage import Storage

log = logging.getLogger(__name__)

MARKET = "SPY"
HISTORY_START = date(2010, 1, 1)
UNIVERSE_KEY = "raw/universe.parquet"
PRICES_PREFIX = "raw/prices/"
MODEL_KEY = "state/model/current.txt"
MODEL_META_KEY = "state/model/meta.json"
PUBLISHED = "published/"
# Features need about 300 trading days of history; three calendar years always cover that.
PREDICT_YEARS = 3
# The close is 16:00 in New York; leave a margin for the vendor to settle the daily bar.
MARKET_TZ = ZoneInfo("America/New_York")
MARKET_CLOSED_AFTER = time(16, 30)

Downloader = Callable[[list[str], date], pl.DataFrame]


def _download(tickers: list[str], start: date) -> pl.DataFrame:
    return download_prices(tickers, start)


def read_parquet(storage: Storage, key: str) -> pl.DataFrame | None:
    data = storage.get(key)
    return None if data is None else pl.read_parquet(data)


def price_years(storage: Storage) -> list[int]:
    return sorted(int(Path(k).stem) for k in storage.list(PRICES_PREFIX) if k.endswith(".parquet"))


def load_prices(storage: Storage, since_year: int | None = None) -> pl.DataFrame | None:
    years = [y for y in price_years(storage) if since_year is None or y >= since_year]
    frames = [read_parquet(storage, f"{PRICES_PREFIX}{y}.parquet") for y in years]
    return pl.concat(frames) if frames else None


def store_prices(storage: Storage, prices: pl.DataFrame, merge: bool = True) -> list[int]:
    """Write prices into yearly files, merging with what is stored (new rows win). Returns the years written."""
    written = []
    for year, part in split_by_year(prices).items():
        key = f"{PRICES_PREFIX}{year}.parquet"
        existing = read_parquet(storage, key) if merge else None
        if existing is not None:
            part = pl.concat([existing, part.select(existing.columns)])
        part = part.unique(["date", "ticker"], keep="last").sort("ticker", "date")
        storage.put(key, parquet_bytes(part))
        written.append(year)
    return sorted(written)


def last_closed_session(now: datetime) -> date:
    """The latest date whose daily bar is final: today in New York once the market has closed."""
    local = now.astimezone(MARKET_TZ)
    return local.date() if local.time() >= MARKET_CLOSED_AFTER else local.date() - timedelta(days=1)


def ingest(
    storage: Storage,
    download: Downloader = _download,
    fetch_universe: Callable[[], pl.DataFrame] = fetch_sp500,
    now: datetime | None = None,
    overlap_days: int = 10,
) -> dict:
    """Refresh the universe and append recent prices.

    The last overlap_days are downloaded again so late vendor corrections replace earlier rows.
    Tickers new to the index get their full history, which the 252-day features need. Bars of a
    session still trading are dropped: the vendor already returns them, but they are not final.
    """
    now = now or datetime.now(UTC)
    today = now.astimezone(MARKET_TZ).date()
    cutoff = last_closed_session(now)
    universe = fetch_universe()
    storage.put(UNIVERSE_KEY, parquet_bytes(universe))
    tickers = [*universe["ticker"], MARKET]

    recent = load_prices(storage, since_year=today.year - 1)
    known = set() if recent is None else set(recent["ticker"].unique())
    new = sorted(set(tickers) - known)
    frames = []
    if known:
        start = recent["date"].max() - timedelta(days=overlap_days)
        frames.append(download(sorted(set(tickers) & known), start))
    if new:
        log.info("full history for %d new tickers", len(new))
        frames.append(download(new, HISTORY_START))
    frames = [f.filter(pl.col("date") <= cutoff) for f in frames]
    frames = [f for f in frames if not f.is_empty()]
    if not frames or MARKET not in set(pl.concat(frames)["ticker"]):
        raise RuntimeError("no prices downloaded (vendor unavailable or blocked?)")
    fresh = pl.concat(frames)
    years = store_prices(storage, fresh)
    result = {
        "latest_date": fresh["date"].max().isoformat(),
        "rows": fresh.height,
        "tickers": fresh["ticker"].n_unique(),
        "new_tickers": new,
        "years_written": years,
    }
    log.info("ingest: %s", result)
    return result


def prepare(prices: pl.DataFrame, universe: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Align prices to the market calendar; return (index members, market close)."""
    aligned = align_to_calendar(prices, trading_calendar(prices, MARKET))
    market = aligned.filter(pl.col("ticker") == MARKET).select("date", "close")
    stocks = aligned.filter(pl.col("ticker").is_in(universe["ticker"].implode()))
    return stocks, market


def train_live_model(panel: pl.DataFrame, sample_every: int = 5) -> LGBMModel:
    """LightGBM on every labelled date (every n-th date), as in the walk-forward runs."""
    day = panel.select(pl.col("date").unique().sort()).with_row_index("day")
    train = panel.join(day, on="date").filter(pl.col("target").is_not_null(), pl.col("day") % sample_every == 0)
    return LGBMModel().fit(train.select(FEATURES), train["target"])


def save_model(storage: Storage, model: LGBMModel, trained_through: date | str) -> dict:
    trained_through = trained_through if isinstance(trained_through, str) else trained_through.isoformat()
    meta = {"trained_through": trained_through, "features": model.features, "kind": "lgbm"}
    storage.put(MODEL_KEY, model.to_string().encode())
    storage.put(MODEL_META_KEY, json.dumps(meta, indent=2).encode())
    return meta


def predict(storage: Storage) -> dict:
    """Score the latest date with the live model and publish the daily files."""
    years = price_years(storage)
    if not years:
        raise RuntimeError("no stored prices; run the backfill or ingest first")
    universe = read_parquet(storage, UNIVERSE_KEY)
    prices = load_prices(storage, since_year=years[-1] - PREDICT_YEARS + 1)
    stocks, market = prepare(prices, universe)
    panel = build_panel(stocks, market, universe)
    model = LGBMModel.from_string(storage.get(MODEL_KEY).decode())
    model_meta = json.loads(storage.get(MODEL_META_KEY))

    data_date = panel["date"].max()
    # Normally only this year's returns change; early in January last year's may still be corrected.
    publish_years = [data_date.year - 1, data_date.year] if data_date.month == 1 else [data_date.year]
    frames = daily_frames(stocks, market, universe, panel, model, years=publish_years)
    manifest = publish(storage, PUBLISHED, frames, {"data_date": data_date, "live_model": model_meta})
    result = {"data_date": data_date.isoformat(), "files_written": sorted(frames), "n_tickers": frames["signals/latest.parquet"].height}
    log.info("predict: %s", result)
    return result | {"manifest_files": len(manifest["files"])}


def publish_all(
    storage: Storage,
    prices: pl.DataFrame,
    universe: pl.DataFrame,
    results: Path,
    models: Iterable[str],
    meta: dict,
) -> tuple[dict, LGBMModel, dict]:
    """Full publish from local research results: every yearly file, histories and quality.

    Trains a live model on every labelled date and returns it with its description, so the
    backfill can store it. Used by the backfill and by publish_local.
    """
    stocks, market = prepare(prices, universe)
    panel = build_panel(stocks, market, universe)
    model = train_live_model(panel)
    trained_through = panel.filter(pl.col("target").is_not_null())["date"].max()
    model_meta = {"trained_through": trained_through.isoformat(), "features": model.features, "kind": "lgbm"}
    frames = daily_frames(stocks, market, universe, panel, model) | research_frames(results, list(models))
    meta = meta | {"data_date": panel["date"].max(), "features": FEATURES, "live_model": model_meta}
    manifest = publish(storage, PUBLISHED, frames, meta, full=True)
    return manifest, model, model_meta
