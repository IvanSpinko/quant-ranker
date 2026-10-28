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

## License

[AGPL-3.0](LICENSE)
