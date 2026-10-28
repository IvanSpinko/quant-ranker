"""Vectorised long/short backtest. This is the Python reference for the Rust engine in the app.

Timing matches the target: scores known at the close of signal date t, the portfolio is traded
at the close of t + lag and earns returns from the next day on. Between rebalances the target
weights are held constant (no drift), a standard simplification of vectorised backtests.
Weights: the long side sums to +1 and the short side to -1, so the return is the long-short spread.
Costs: turnover (sum of absolute weight changes) times commission plus slippage, in basis points.

Two optional turnover controls, both past-only:
  smoothing_halflife: scores are ranked per date and averaged exponentially over past dates;
  exit_fraction: a holding buffer. A name enters a side in the top (bottom) `fraction` and stays
    until it leaves the top (bottom) `exit_fraction`; the freed slots go to the best new names.
"""

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import polars as pl

from quant_core.features import cross_sectional_rank


@dataclass(frozen=True)
class BacktestConfig:
    fraction: float = 0.1
    rebalance_every: int = 5
    lag: int = 1
    weighting: Literal["equal", "inverse_vol"] = "equal"
    vol_window: int = 21
    commission_bps: float = 1.0
    slippage_bps: float = 5.0
    smoothing_halflife: float = 0.0
    exit_fraction: float | None = None

    @property
    def cost_rate(self) -> float:
        return (self.commission_bps + self.slippage_bps) / 1e4


@dataclass(frozen=True)
class BacktestResult:
    daily: pl.DataFrame
    """date, gross, cost, net, turnover, long, short (returns earned on that date)."""
    weights: pl.DataFrame
    """signal_date, ticker, weight: target weights at each rebalance."""


def daily_returns(prices: pl.DataFrame) -> pl.DataFrame:
    """date, ticker, ret: close-to-close simple returns."""
    return (
        prices.sort("ticker", "date")
        .select("date", "ticker", ret=pl.col("close").pct_change().over("ticker"))
        .drop_nulls("ret")
    )


def smooth_scores(scores: pl.DataFrame, halflife: float) -> pl.DataFrame:
    """Per-date ranks averaged exponentially over each ticker's past dates (halflife in dates).

    Ranking first puts every fold's predictions on the same scale.
    """
    if halflife <= 0:
        return scores
    ranked = cross_sectional_rank(scores.drop_nulls("score"), ["score"])
    return ranked.sort("ticker", "date").with_columns(
        pl.col("score").ewm_mean(half_life=halflife).over("ticker")
    )


def _buffered_sides(df: pl.DataFrame, fraction: float, exit_fraction: float) -> pl.DataFrame:
    """Select sides date by date, keeping held names while they stay inside the exit band."""
    held = {1: set(), -1: set()}
    rows: list[tuple] = []
    for (day,), frame in df.sort("date").partition_by("date", as_dict=True, maintain_order=True).items():
        order = frame.sort("score", "ticker", descending=[True, False])["ticker"].to_list()
        n = len(order)
        m = max(1, math.floor(n * fraction))
        band = max(m, math.floor(n * exit_fraction))
        if n < 2 * band:
            band = m
        if n < 2 * m:
            held = {1: set(), -1: set()}
            continue
        for side, ranked in ((1, order), (-1, order[::-1])):
            keep = [t for t in ranked[:band] if t in held[side]]
            taken = set(keep) | (held[-side] if side == -1 else set())
            new = [t for t in ranked if t not in taken][: m - len(keep)]
            held[side] = set(keep) | set(new)
            rows.extend((day, t, side) for t in held[side])
    return pl.DataFrame(rows, schema={"date": pl.Date, "ticker": pl.String, "side": pl.Int32}, orient="row")


def target_weights(scores: pl.DataFrame, config: BacktestConfig) -> pl.DataFrame:
    """Long the top fraction and short the bottom fraction of each date's scores.

    scores: date, ticker, score and, for inverse-vol weighting, vol. Dates must be the
    rebalance dates only, since the holding buffer carries positions from one to the next.
    """
    scores = scores.drop_nulls("score").sort("date", "ticker")
    exit_fraction = config.exit_fraction
    if exit_fraction is None or exit_fraction <= config.fraction:
        n = pl.len().over("date")
        m = (n * config.fraction).floor().clip(lower_bound=1)
        rank = pl.col("score").rank("ordinal", descending=True).over("date")
        side = pl.when(rank <= m).then(1).when(rank > n - m).then(-1).otherwise(0)
        df = scores.with_columns(side=side, m=m, n=n).filter(pl.col("side") != 0, pl.col("n") >= 2 * pl.col("m"))
    else:
        sides = _buffered_sides(scores, config.fraction, exit_fraction)
        df = scores.join(sides, on=["date", "ticker"], how="inner").sort("date", "ticker")
    if config.weighting == "equal":
        raw = pl.lit(1.0)
    elif config.weighting == "inverse_vol":
        vol = pl.when(pl.col("vol") > 0).then(pl.col("vol"))
        vol = vol.fill_null(vol.median().over("date", "side"))
        raw = (1.0 / vol).fill_null(1.0)
    else:
        raise ValueError(f"unknown weighting {config.weighting!r}")
    return df.with_columns(raw=raw).select(
        pl.col("date").alias("signal_date"),
        "ticker",
        weight=pl.col("side") * pl.col("raw") / pl.col("raw").sum().over("date", "side"),
    )


def run_backtest(scores: pl.DataFrame, returns: pl.DataFrame, config: BacktestConfig = BacktestConfig()) -> BacktestResult:
    """scores: date, ticker, score. returns: date, ticker, ret (from daily_returns)."""
    calendar = returns["date"].unique().sort()
    tickers = returns["ticker"].unique().sort()
    n_days, n_tickers = len(calendar), len(tickers)
    day = pl.DataFrame({"date": calendar}).with_row_index("day")
    col = pl.DataFrame({"ticker": tickers}).with_row_index("col")

    scores = smooth_scores(scores, config.smoothing_halflife)
    signal_dates = scores.select(pl.col("date").unique()).join(day, on="date").sort("day")
    rebalances = signal_dates.gather_every(config.rebalance_every)

    if config.weighting == "inverse_vol":
        vol = returns.sort("ticker", "date").select(
            "date", "ticker", vol=pl.col("ret").rolling_std(config.vol_window).over("ticker")
        )
        scores = scores.join(vol, on=["date", "ticker"], how="left")
    weights = target_weights(scores.join(rebalances.select("date"), on="date", how="semi"), config)

    trades = (
        weights.join(day.rename({"date": "signal_date"}), on="signal_date")
        .join(col, on="ticker")
        .with_columns(trade_day=pl.col("day") + config.lag)
        .filter(pl.col("trade_day") < n_days)
    )
    trade_days = np.unique(rebalances["day"].to_numpy() + config.lag)
    trade_days = trade_days[trade_days < n_days]

    target = np.zeros((n_days, n_tickers))
    target[trades["trade_day"].to_numpy(), trades["col"].to_numpy()] = trades["weight"].to_numpy()
    # Positions held after each close: the most recent trade day's targets, zero before the first.
    is_trade = np.zeros(n_days, dtype=bool)
    is_trade[trade_days] = True
    last_trade = np.maximum.accumulate(np.where(is_trade, np.arange(n_days), -1))
    positions = np.where((last_trade >= 0)[:, None], target[np.maximum(last_trade, 0)], 0.0)

    ret = np.zeros((n_days, n_tickers))
    r = returns.join(day, on="date").join(col, on="ticker")
    ret[r["day"].to_numpy(), r["col"].to_numpy()] = r["ret"].to_numpy()
    ret = np.nan_to_num(ret)

    held = np.vstack([np.zeros((1, n_tickers)), positions[:-1]])
    gross = (held * ret).sum(axis=1)
    long = (np.clip(held, 0, None) * ret).sum(axis=1)
    short = (np.clip(held, None, 0) * ret).sum(axis=1)
    turnover = np.abs(positions - held).sum(axis=1)
    cost = turnover * config.cost_rate

    daily = pl.DataFrame(
        {
            "date": calendar,
            "gross": gross,
            "cost": cost,
            "net": gross - cost,
            "turnover": turnover,
            "long": long,
            "short": short,
        }
    )
    if len(trade_days):
        end = min(int(trade_days[-1]) + config.rebalance_every, n_days - 1)
        daily = daily.slice(int(trade_days[0]), end - int(trade_days[0]) + 1)
    else:
        daily = daily.clear()
    return BacktestResult(daily=daily, weights=weights)


def performance_summary(daily: pl.DataFrame, column: str = "net", periods_per_year: int = 252) -> dict[str, float]:
    """CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, annual turnover and daily hit rate."""
    r = daily[column].to_numpy()
    n = len(r)
    if n == 0:
        return {}
    equity = np.cumprod(1 + r)
    years = n / periods_per_year
    cagr = equity[-1] ** (1 / years) - 1 if equity[-1] > 0 else -1.0
    std = r.std(ddof=1) if n > 1 else math.nan
    downside = math.sqrt(np.mean(np.minimum(r, 0) ** 2))
    drawdown = equity / np.maximum.accumulate(np.maximum(equity, 1.0)) - 1
    max_dd = float(drawdown.min())
    annualise = math.sqrt(periods_per_year)
    return {
        "cagr": float(cagr),
        "volatility": float(std * annualise),
        "sharpe": float(r.mean() / std * annualise) if std else math.nan,
        "sortino": float(r.mean() / downside * annualise) if downside else math.nan,
        "max_drawdown": max_dd,
        "calmar": float(cagr / -max_dd) if max_dd < 0 else math.nan,
        "annual_turnover": float(daily["turnover"].mean() * periods_per_year),
        "hit_rate": float((r > 0).mean()),
        "total_return": float(equity[-1] - 1),
        "years": years,
    }


def yearly_returns(daily: pl.DataFrame, column: str = "net") -> pl.DataFrame:
    return (
        daily.group_by(year=pl.col("date").dt.year())
        .agg(ret=(1 + pl.col(column)).product() - 1)
        .sort("year")
    )


def equity_curve(daily: pl.DataFrame, column: str = "net") -> pl.DataFrame:
    equity = (1 + pl.col(column)).cum_prod()
    return daily.select("date", equity=equity, drawdown=equity / pl.max_horizontal(equity.cum_max(), 1.0) - 1)
