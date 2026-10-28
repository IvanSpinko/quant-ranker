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

### Desktop app

The app reads the published file set. Until the cloud pipeline exists, write it locally to
`data/published/` (needs the two research runs above):

```bash
uv run python research/publish_local.py
```

Then start the app from `app/` (the first build compiles the Rust side and takes a few minutes):

```bash
npm install
```

```bash
npm run tauri dev
```

Rust tests, including the golden test that compares the Rust backtest with the Python one, run
from `app/src-tauri/`:

```bash
cargo test
```

IntelliJ IDEA picks up shared run configurations from `.run/` (App, Data, Research, Tests). They
use the `.venv` interpreter created by `uv sync` and the Node.js on `PATH`.

After changing the Python backtest, regenerate the golden fixture:

```bash
uv run python tests/make_golden.py
```

## License

[AGPL-3.0](LICENSE)
