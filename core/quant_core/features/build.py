"""Feature engineering on a long (date, ticker) price panel.

Every feature at date t uses closes and volumes up to and including t only. Rolling windows
count rows, so prices should first go through `align_to_calendar`.
"""

import math
from collections.abc import Sequence

import polars as pl

# Short-term reversal is carried by ret_1 and mom_5: models learn the sign, the reversal
# baseline flips it explicitly.
FEATURES = [
    "mom_5",
    "mom_21",
    "mom_63",
    "mom_126",
    "mom_252",
    "mom_12_1",
    "ret_1",
    "vol_21",
    "vol_63",
    "vol_ratio",
    "volume_ratio",
    "dollar_volume",
    "amihud",
    "beta_126",
    "resid_21",
    "sector_rel_21",
]

BETA_WINDOW = 126


def _market_returns(market: pl.DataFrame) -> pl.DataFrame:
    close = pl.col("close")
    return market.sort("date").select(
        "date",
        mkt_ret=close.pct_change(),
        mkt_mom_21=close / close.shift(21) - 1,
    )


def compute_features(prices: pl.DataFrame, market: pl.DataFrame, sectors: pl.DataFrame) -> pl.DataFrame:
    """Raw (unranked) features.

    prices: date, ticker, close, volume. market: date, close of the market proxy (SPY).
    sectors: ticker, sector. Returns date, ticker, sector, close and the FEATURES columns.
    """
    close, volume = pl.col("close"), pl.col("volume")
    ret, mkt = pl.col("ret"), pl.col("mkt_ret")

    def per_ticker(expr: pl.Expr) -> pl.Expr:
        return expr.over("ticker")

    def mean(expr: pl.Expr, n: int) -> pl.Expr:
        return per_ticker(expr.rolling_mean(n))

    df = (
        prices.select("date", "ticker", "close", "volume")
        .sort("ticker", "date")
        .join(sectors.select("ticker", "sector"), on="ticker", how="left")
        .join(_market_returns(market), on="date", how="left")
        .with_columns(
            ret=per_ticker(close.pct_change()),
            dvol=close * volume,
        )
    )
    df = df.with_columns(
        *[per_ticker(close / close.shift(n) - 1).alias(f"mom_{n}") for n in (5, 21, 63, 126, 252)],
        mom_12_1=per_ticker(close.shift(21) / close.shift(252) - 1),
        ret_1=ret,
        vol_21=per_ticker(ret.rolling_std(21)) * math.sqrt(252),
        vol_63=per_ticker(ret.rolling_std(63)) * math.sqrt(252),
        volume_ratio=mean(volume, 5) / mean(volume, 63),
        dollar_volume=mean(pl.col("dvol"), 21).log(),
        amihud=mean(pl.when(pl.col("dvol") > 0).then(ret.abs() / pl.col("dvol")), 21),
        beta_126=(mean(ret * mkt, BETA_WINDOW) - mean(ret, BETA_WINDOW) * mean(mkt, BETA_WINDOW))
        / (mean(mkt * mkt, BETA_WINDOW) - mean(mkt, BETA_WINDOW) ** 2),
    )
    df = df.with_columns(
        vol_ratio=pl.col("vol_21") / pl.col("vol_63"),
        resid_21=pl.col("mom_21") - pl.col("beta_126") * pl.col("mkt_mom_21"),
        sector_rel_21=pl.col("mom_21") - pl.col("mom_21").mean().over("date", "sector"),
    )
    # Division by zero volume or variance gives inf/NaN; treat them as missing.
    return df.select(
        "date",
        "ticker",
        "sector",
        "close",
        *[pl.when(pl.col(f).is_finite()).then(pl.col(f)).alias(f) for f in FEATURES],
    )


def cross_sectional_rank(df: pl.DataFrame, columns: Sequence[str]) -> pl.DataFrame:
    """Replace columns by their per-date rank scaled to [-0.5, 0.5]. Nulls stay null."""
    exprs = []
    for c in columns:
        n = pl.col(c).count().over("date")
        rank = pl.col(c).rank("average").over("date")
        exprs.append(pl.when(n > 1).then((rank - 1) / (n - 1) - 0.5).alias(c))
    return df.with_columns(exprs)


def add_target(
    df: pl.DataFrame,
    market: pl.DataFrame,
    horizon: int = 5,
    lag: int = 1,
    relative_to: str = "market",
) -> pl.DataFrame:
    """Add the forward relative return and its cross-sectional rank.

    The signal is known at the close of t and the position is opened at the close of t + lag,
    so the label is the return from close t + lag to close t + lag + horizon:
      fwd_ret: the stock's return, excess_ret: minus market or sector-mean return,
      target: excess_ret ranked per date to [-0.5, 0.5].
    Subtracting the market return does not change the rank; it matters for excess_ret only.
    """
    close = pl.col("close")

    def forward(expr_close: pl.Expr) -> pl.Expr:
        return expr_close.shift(-(lag + horizon)) / expr_close.shift(-lag) - 1

    mkt = market.sort("date").select("date", mkt_fwd=forward(close))
    df = df.sort("ticker", "date").with_columns(fwd_ret=forward(close).over("ticker"))
    if relative_to == "market":
        df = df.join(mkt, on="date", how="left").with_columns(excess_ret=pl.col("fwd_ret") - pl.col("mkt_fwd"))
        df = df.drop("mkt_fwd")
    elif relative_to == "sector":
        df = df.with_columns(excess_ret=pl.col("fwd_ret") - pl.col("fwd_ret").mean().over("date", "sector"))
    else:
        raise ValueError(f"relative_to must be 'market' or 'sector', got {relative_to!r}")
    return cross_sectional_rank(df.with_columns(target=pl.col("excess_ret")), ["target"])


def build_panel(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    sectors: pl.DataFrame,
    horizon: int = 5,
    lag: int = 1,
    relative_to: str = "market",
) -> pl.DataFrame:
    """Ranked features plus target, one row per date and ticker, sorted by date then ticker."""
    raw = compute_features(prices, market, sectors)
    ranked = cross_sectional_rank(raw, FEATURES)
    return add_target(ranked, market, horizon, lag, relative_to).sort("date", "ticker")
