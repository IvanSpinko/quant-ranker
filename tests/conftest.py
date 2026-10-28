"""Synthetic market data shared by the tests: random-walk prices, a market index and sectors."""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

N_TICKERS = 30
N_DAYS = 400
SECTORS = ["Tech", "Energy", "Health"]


def business_days(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


@pytest.fixture(scope="session")
def synthetic():
    rng = np.random.default_rng(42)
    dates = business_days(N_DAYS)
    market_ret = rng.normal(0.0003, 0.01, N_DAYS)
    market = pl.DataFrame({"date": dates, "close": 100 * np.cumprod(1 + market_ret)})
    rows = []
    for i in range(N_TICKERS):
        beta = rng.uniform(0.5, 1.5)
        ret = beta * market_ret + rng.normal(0, 0.015, N_DAYS)
        close = 50 * np.cumprod(1 + ret)
        volume = rng.lognormal(13, 0.4, N_DAYS)
        # A few tickers list late, so warm-up and partial histories are exercised.
        start = 0 if i % 7 else 120
        rows.append(
            pl.DataFrame(
                {
                    "date": dates[start:],
                    "ticker": f"T{i:02d}",
                    "open": close[start:],
                    "high": close[start:],
                    "low": close[start:],
                    "close": close[start:],
                    "volume": volume[start:],
                }
            )
        )
    prices = pl.concat(rows)
    sectors = pl.DataFrame(
        {"ticker": [f"T{i:02d}" for i in range(N_TICKERS)], "sector": [SECTORS[i % 3] for i in range(N_TICKERS)]}
    )
    return {"prices": prices, "market": market, "sectors": sectors, "dates": dates}
