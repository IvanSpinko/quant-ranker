"""Vectorised long/short portfolio backtest (Python reference implementation)."""

from quant_core.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    daily_returns,
    equity_curve,
    performance_summary,
    run_backtest,
    target_weights,
    yearly_returns,
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "daily_returns",
    "equity_curve",
    "performance_summary",
    "run_backtest",
    "target_weights",
    "yearly_returns",
]
