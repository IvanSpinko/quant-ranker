# Architecture

Quant Ranker ranks S&P 500 stocks by expected relative return over the next five trading days
and lets the user explore how a long/short strategy built on those ranks would have performed.
It is a research and learning tool, not investment advice.

## Goals

- Honest methodology first: no look-ahead, walk-forward validation, costs included, baselines compared.
- A lightweight desktop app that friends can install on Windows, macOS and Linux.
- No GPU requirement. Everything runs on an ordinary laptop CPU.
- Installer should not trigger antivirus false positives.

## Hybrid design

All modelling lives in Python, in one place. The desktop app is pure Rust + web UI and never
computes features or predictions, so training and serving cannot drift apart.

```
AWS (Python, shared quant_core package)
  EventBridge (each trading day)
    -> Lambda ingest     : daily OHLCV -> S3 raw/
    -> Lambda predict    : features + current model -> S3 published/
  Lambda retrain (monthly, container image)
                         : walk-forward retrain, publish only if out-of-sample IC does not regress
  CloudFront             : read-only HTTPS access to published/

Desktop app (Tauri 2: Rust + React/TypeScript)
  - reads published/manifest.json and downloads only changed files
  - keeps a local Parquet cache and works offline on the last snapshot
  - runs the backtest locally in Rust (arrow + parquet crates, plain arrays), nothing else
```

The Rust backtest is a port of the Python reference. A golden test (`app/src-tauri/tests/golden.rs`)
checks it against outputs written by `tests/make_golden.py`, and a second test checks it against
the Python run on the real published data when that data is present.

### What the cloud publishes

| File | Content |
|---|---|
| `manifest.json` | Versions, hashes and update date of every published file; data dates, models, features, default backtest |
| `universe.parquet` | Tickers with name, sector and industry |
| `signals/latest.parquet` | Today's scores and ranks per ticker |
| `signals/history/<model>.parquet` | Historical out-of-sample scores (walk-forward) per date and ticker, one file per model |
| `returns/daily/<year>.parquet` | Daily close-to-close returns per ticker, used by the backtest and the growth chart |
| `returns/market/<year>.parquet` | Daily SPY returns, the benchmark |
| `features/latest.parquet` | Ranked feature values shown on the stock detail screen |
| `explain/shap.parquet` | Per-ticker feature contributions (LightGBM TreeSHAP) and the base value |
| `quality/*.parquet` | Daily IC per model, model comparison, feature importance |

Raw vendor prices are not redistributed. Only derived data is published. Check the data vendor's
terms before publishing anything.

Returns are split by year, so a daily update rewrites only the current year's file (about
1 MB in total). `research/publish_local.py` writes the same layout to `data/published/`, which
debug builds of the app read by default when no data URL is set.

### Daily pipeline

- One private bucket: `raw/` (vendor prices by year, universe), `state/` (live model),
  `published/` (the only prefix CloudFront can read).
- EventBridge Scheduler starts `ingest` at 18:30 New York time on weekdays. It refreshes the
  universe, downloads the last days again (late corrections win), gives new index members their
  full history and drops bars of a session still trading.
- A successful `ingest` invokes `predict` (Lambda destination). It computes features on the last
  three years, scores the latest date with the live model and publishes changed files; the
  manifest is written last.
- Both functions run the same container image (`cloud/lambdas/Dockerfile`); the logic lives in
  `quant_core.pipeline` and is tested on a local folder. `cloud/backfill.py` uploads the history
  once; `cloud/deploy.sh` builds, pushes and applies Terraform.
- The app downloads `manifest.json`, fetches only files whose SHA-256 changed, verifies each one
  and keeps working offline on the last complete snapshot.

### Desktop app screens

- **Signals**: today's ranking with search and sector filter, data date.
- **Stock detail**: price chart, score history, features, SHAP contributions.
- **Backtest**: long/short fraction, rebalance frequency, costs, period, model -> equity curve,
  Sharpe, drawdown, turnover, yearly returns.
- **Model quality**: IC over time, model vs baselines, feature importance.
- **Paper trading** (optional): user's own Alpaca paper keys in the OS keychain, rebalance with confirmation.
- **Settings**: data refresh, cache location, theme.

## Methodology

- **Universe**: S&P 500 constituents, daily data, about 15 years. Survivorship bias is acknowledged.
- **Features** (past-only, cross-sectionally ranked per day): momentum (5/21/63/126/252 days, 12-1),
  short-term reversal (1/5 days), realised volatility (21/63), volatility ratio, volume ratio,
  dollar volume, Amihud illiquidity, beta and residual return vs SPY, return relative to sector.
- **Target**: 5-day forward return minus market (or sector) return, as a cross-sectional rank.
- **Models**: baselines (12-1 momentum, 5-day reversal, equal-weight combo), Ridge, LightGBM,
  optional Ridge + LightGBM ensemble.
- **Validation**: expanding-window walk-forward, purging and a 5-day embargo, untouched final
  hold-out period, number of tried variants recorded.
- **Metrics**: mean IC, IC IR, share of positive-IC months; portfolio CAGR, volatility, Sharpe,
  Sortino, max drawdown, Calmar, turnover, hit rate.
- **Backtest**: vectorised, long top N% / short bottom N%, equal or inverse-volatility weights,
  commission and slippage in basis points.
- **Correctness tests**: look-ahead test (features at t unchanged when data after t is cut),
  walk-forward split disjointness, backtest on synthetic data with a known answer, and a golden
  test that checks the Rust backtest matches the Python reference.

## Performance and distribution targets

| Metric | Target |
|---|---|
| Installer size | <= 25 MB |
| RAM while running | <= 200 MB |
| Cold start | < 1 s |
| Backtest, 15 years x 500 stocks | well under 1 s |
| Daily update download | <= 1 MB |

- Rust release profile: `opt-level = 3`, `lto = true`, `codegen-units = 1`, `strip = true`, `panic = "abort"`.
- No UPX or other executable packers (they trigger antivirus heuristics).
- No PyInstaller inside the app.
- Builds per OS in CI (macOS and Linux cannot be built on Windows): `.msi`/`.exe`, universal `.dmg`,
  `.AppImage`/`.deb`, published on GitHub Releases with the Tauri updater.
- Windows code signing via SignPath Foundation (free for open source) to avoid SmartScreen warnings;
  macOS notarization is optional.

## Repository layout

```
core/quant_core/   Python package shared by research and cloud
  data/            loaders, universe, trading calendar
  features/        feature engineering (past-only)
  models/          baselines, ridge, lgbm
  validation/      walk-forward splitter, purging, IC metrics
  backtest/        vectorised portfolio engine (Python reference)
cloud/
  lambdas/         Lambda image and handlers (ingest, predict; retrain in stage 5)
  terraform/       S3, ECR, Lambda, EventBridge Scheduler, CloudFront, IAM
  backfill.py      one-off upload of history, model and research results
  deploy.sh        build and push the image, apply Terraform
app/               Tauri 2 + React + TypeScript desktop app
research/          notebooks and experiment notes
tests/             look-ahead, walk-forward, backtest sanity tests
```

## Stages

| # | Stage | Outcome |
|---|---|---|
| 1 | Research core | Data, features, baselines, walk-forward, LightGBM, Python backtest, look-ahead tests, findings notebook |
| 2 | AWS pipeline | Terraform, ingest, predict, publishing through CloudFront, history backfill |
| 3 | Desktop app | Tauri + React screens, Rust backtest with golden test against Python, local cache sync |
| 4 | Release | Cross-platform CI builds, signing, GitHub Releases, auto-update |
| 5 | Optional | Paper trading via Alpaca, monthly cloud retrain with regression guard |

## Known limitations

- The expected signal is weak; after costs the strategy may be close to zero. That is a valid result.
- Free data carries survivorship bias.
- Daily bars only; no intraday effects.
