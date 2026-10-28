"""Trading calendar taken from a reference series (SPY) and per-ticker alignment to it."""

import polars as pl


def trading_calendar(prices: pl.DataFrame, reference: str = "SPY") -> pl.Series:
    """Sorted trading dates on which the reference ticker traded."""
    dates = prices.filter(pl.col("ticker") == reference)["date"]
    if dates.is_empty():
        raise ValueError(f"reference ticker {reference!r} has no prices")
    return dates.unique().sort()


def align_to_calendar(prices: pl.DataFrame, calendar: pl.Series) -> pl.DataFrame:
    """Give every ticker one row per calendar date between its first and last bar.

    Rolling windows and forward shifts count rows, so a missing bar would silently stretch a
    window. Missing days get the previous close and zero volume (a zero return), and are
    flagged in `filled`. Dates off the calendar are dropped.
    """
    cal = pl.DataFrame({"date": calendar}).with_row_index("day")
    spans = prices.group_by("ticker").agg(first=pl.col("date").min(), last=pl.col("date").max())
    grid = spans.join(cal, how="cross").filter(pl.col("date").is_between(pl.col("first"), pl.col("last")))
    return (
        grid.select("ticker", "date", "day")
        .join(prices, on=["ticker", "date"], how="left")
        .sort("ticker", "date")
        .with_columns(filled=pl.col("close").is_null())
        .with_columns(
            pl.col("close").forward_fill().over("ticker"),
            pl.col("volume").fill_null(0.0),
            *[pl.col(c).fill_null(pl.col("close").forward_fill().over("ticker")) for c in ("open", "high", "low")],
        )
        .drop("day")
    )
