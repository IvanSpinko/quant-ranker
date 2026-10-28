"""Offline tests for parsing vendor data."""

import pandas as pd
import polars as pl

from quant_core.data.prices import _to_long
from quant_core.data.universe import parse_sp500_table

HTML = """
<table id="constituents">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
<tr><td>MMM</td><td>3M</td><td>Industrials</td><td>Industrial Conglomerates</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi-Sector Holdings</td></tr>
</table>
"""


def test_parse_sp500_table_normalises_symbols():
    universe = parse_sp500_table(HTML)
    assert universe["ticker"].to_list() == ["BRK-B", "MMM"]
    assert universe.filter(pl.col("ticker") == "MMM")["sector"].item() == "Industrials"


def test_yfinance_frame_to_long():
    index = pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="Date")
    columns = pd.MultiIndex.from_product([["AAA", "BBB"], ["Open", "High", "Low", "Close", "Volume"]])
    raw = pd.DataFrame(
        [[1, 2, 0.5, 1.5, 100, 10, 11, 9, 10.5, 200], [1.5, 2, 1, 1.8, 110, None, None, None, None, None]],
        index=index,
        columns=columns,
    )
    long = _to_long(raw).sort("ticker", "date")
    assert long.columns == ["date", "ticker", "open", "high", "low", "close", "volume"]
    assert long.height == 3
    assert long.filter(pl.col("ticker") == "AAA")["close"].to_list() == [1.5, 1.8]
    assert long.schema["date"] == pl.Date
