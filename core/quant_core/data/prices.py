"""Daily split- and dividend-adjusted OHLCV from Yahoo Finance in long format.

Columns: date, ticker, open, high, low, close, volume.
"""

import warnings
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import yfinance as yf

PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


def _to_long(raw: pd.DataFrame) -> pl.DataFrame:
    """Turn yfinance's (ticker, field) column frame into one row per date and ticker."""
    if raw is None or raw.empty:
        return pl.DataFrame(schema={"date": pl.Date, "ticker": pl.String} | dict.fromkeys(PRICE_COLUMNS, pl.Float64))
    stacked = raw.stack(level=0).reset_index()
    stacked.columns = [str(c).lower() for c in stacked.columns]
    stacked = stacked.rename(columns={stacked.columns[0]: "date", stacked.columns[1]: "ticker"})
    return (
        pl.from_pandas(stacked[["date", "ticker", *PRICE_COLUMNS]])
        .with_columns(pl.col("date").cast(pl.Date), pl.col(PRICE_COLUMNS).cast(pl.Float64))
        .filter(pl.col("close").is_not_null() & (pl.col("close") > 0))
    )


def download_prices(
    tickers: Sequence[str],
    start: date | str,
    end: date | str | None = None,
    batch_size: int = 100,
) -> pl.DataFrame:
    """Download adjusted daily bars in batches. Tickers without data are silently skipped."""
    frames = []
    tickers = list(dict.fromkeys(tickers))
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        with warnings.catch_warnings():
            # yfinance still calls pandas APIs that pandas 3 deprecates.
            warnings.simplefilter("ignore")
            raw = yf.download(
                batch,
                start=start,
                end=end,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
                multi_level_index=True,
            )
        frames.append(_to_long(raw))
    return pl.concat(frames).unique(["date", "ticker"], keep="last").sort("ticker", "date")


def load_prices(
    path: Path,
    tickers: Sequence[str],
    start: date | str,
    end: date | str | None = None,
    refresh: bool = False,
) -> pl.DataFrame:
    """Read cached prices, downloading them on first use or when refresh is set."""
    path = Path(path)
    if path.exists() and not refresh:
        return pl.read_parquet(path)
    prices = download_prices(tickers, start, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    prices.write_parquet(path)
    return prices
