# Quant Ranker

Cross-sectional stock ranking with an honest backtest, packaged as a lightweight desktop app.

Every trading day a cloud pipeline ranks S&P 500 stocks by expected 5-day relative return.
The desktop app (Windows, macOS, Linux) shows the ranking and lets you backtest a long/short
strategy built on it, with costs included.

> Research and learning tool. Not investment advice.

## Status

Early development. See [docs/architecture.md](docs/architecture.md) for the design and stages.

## Development

Requirements: [uv](https://docs.astral.sh/uv/), Rust (stable), Node.js 20+.

```bash
uv sync
uv run pytest
```

Research run (downloads about 15 years of S&P 500 prices into `data/`, then evaluates every model
walk-forward); the findings are in [research/01_stage1_findings.ipynb](research/01_stage1_findings.ipynb):

```bash
uv run python research/stage1.py
```

Turnover reduction on top of those scores (smoothing, holding buffer, rebalance frequency), written
up in [research/02_turnover.ipynb](research/02_turnover.ipynb):

```bash
uv run python research/turnover.py
```

## License

[AGPL-3.0](LICENSE)
