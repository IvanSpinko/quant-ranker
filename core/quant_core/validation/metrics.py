"""Information coefficient: per-date Spearman correlation between score and realised target."""

import math

import polars as pl


def daily_ic(scores: pl.DataFrame, panel: pl.DataFrame, target: str = "target", min_names: int = 10) -> pl.DataFrame:
    """date, ic, n for every date where at least min_names stocks have both score and target."""
    joined = scores.select("date", "ticker", "score").join(
        panel.select("date", "ticker", target), on=["date", "ticker"], how="inner"
    )
    return (
        joined.drop_nulls(["score", target])
        .group_by("date")
        .agg(ic=pl.corr("score", target, method="spearman"), n=pl.len())
        .filter(pl.col("n") >= min_names, pl.col("ic").is_not_nan())
        .sort("date")
    )


def monthly_ic(ic: pl.DataFrame) -> pl.DataFrame:
    return ic.group_by(month=pl.col("date").dt.truncate("1mo")).agg(ic=pl.col("ic").mean()).sort("month")


def ic_summary(ic: pl.DataFrame, horizon: int = 5) -> dict[str, float]:
    """Mean IC, its standard deviation, IC IR (mean / std, per date) and the share of positive months.

    Neighbouring labels overlap, so daily ICs are autocorrelated and a naive t-stat overstates
    significance; ic_t counts only len / horizon independent observations as a rough correction.
    """
    values = ic["ic"]
    mean, std = values.mean(), values.std()
    ir = mean / std if std else math.nan
    return {
        "mean_ic": mean,
        "ic_std": std,
        "ic_ir": ir,
        "ic_t": ir * math.sqrt(len(values) / horizon),
        "pct_positive_months": (monthly_ic(ic)["ic"] > 0).mean(),
        "n_dates": len(values),
    }
