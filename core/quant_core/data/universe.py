"""S&P 500 constituents with GICS sectors.

Only the current list is available for free, so historical backtests carry survivorship bias.
"""

from io import StringIO
from pathlib import Path

import pandas as pd
import polars as pl
import requests

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = "quant-ranker/0.1 (research project)"


def to_yahoo_symbol(symbol: str) -> str:
    """Wikipedia writes share classes as BRK.B, Yahoo as BRK-B."""
    return symbol.strip().replace(".", "-")


def parse_sp500_table(html: str) -> pl.DataFrame:
    table = pd.read_html(StringIO(html), attrs={"id": "constituents"})[0]
    return pl.DataFrame(
        {
            "ticker": [to_yahoo_symbol(s) for s in table["Symbol"]],
            "name": table["Security"].astype(str).tolist(),
            "sector": table["GICS Sector"].astype(str).tolist(),
            "industry": table["GICS Sub-Industry"].astype(str).tolist(),
        }
    ).unique("ticker", keep="first").sort("ticker")


def fetch_sp500(timeout: float = 30.0) -> pl.DataFrame:
    """Download the current constituents: ticker, name, sector, industry."""
    response = requests.get(WIKI_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return parse_sp500_table(response.text)


def load_universe(path: Path, refresh: bool = False) -> pl.DataFrame:
    """Read the cached universe, downloading it on first use or when refresh is set."""
    path = Path(path)
    if path.exists() and not refresh:
        return pl.read_parquet(path)
    universe = fetch_sp500()
    path.parent.mkdir(parents=True, exist_ok=True)
    universe.write_parquet(path)
    return universe
