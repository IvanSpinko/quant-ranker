"""The published file set the desktop app reads (S3 published/, see docs/architecture.md).

Only derived data is written (scores, returns, ranked features, metrics), never raw vendor
prices. Returns are split by year so a daily update rewrites only the current year's file.
"""

import hashlib
import io
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from quant_core.backtest import daily_returns
from quant_core.features import FEATURES
from quant_core.models import LGBMModel
from quant_core.storage import Storage

MANIFEST_VERSION = 2
MANIFEST = "manifest.json"
# Models whose out-of-sample histories are published; the first one drives live signals.
MODELS = ["lgbm", "ridge_lgbm", "ridge", "combo", "momentum_12_1", "reversal_5"]
HOLDOUT_START = "2024-11-01"


def parquet_bytes(frame: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    return buffer.getvalue()


def latest_signals(scores: pl.DataFrame, universe: pl.DataFrame) -> pl.DataFrame:
    """Scores of the most recent date with rank 1 = best and pct in [0, 1], 1 = best."""
    last = scores["date"].max()
    day = scores.filter(pl.col("date") == last).drop_nulls("score")
    n = day.height
    return (
        day.join(universe.select("ticker", "name", "sector"), on="ticker", how="left")
        .with_columns(rank=pl.col("score").rank("ordinal", descending=True).cast(pl.Int32))
        .with_columns(pct=1 - (pl.col("rank") - 1) / max(n - 1, 1))
        .select("date", "ticker", "name", "sector", "score", "rank", "pct")
        .sort("rank")
    )


def shap_long(contributions: pl.DataFrame, tickers: pl.Series) -> pl.DataFrame:
    """ticker, feature, contribution; the base value is repeated per ticker as feature 'base_value'."""
    return (
        contributions.with_columns(ticker=tickers)
        .unpivot(index="ticker", variable_name="feature", value_name="contribution")
        .sort("ticker", "feature")
    )


def split_by_year(frame: pl.DataFrame) -> dict[int, pl.DataFrame]:
    """Rows grouped by the calendar year of their date column."""
    parts = frame.with_columns(_year=pl.col("date").dt.year()).partition_by("_year", as_dict=True, include_key=False)
    return {year: part for (year,), part in parts.items()}


def _by_year(frame: pl.DataFrame, folder: str, years: Iterable[int] | None) -> dict[str, pl.DataFrame]:
    wanted = None if years is None else set(years)
    keys = [c for c in ("date", "ticker") if c in frame.columns]
    return {
        f"{folder}/{year}.parquet": part.sort(*keys)
        for year, part in split_by_year(frame).items()
        if wanted is None or year in wanted
    }


def daily_frames(
    stocks: pl.DataFrame,
    market: pl.DataFrame,
    universe: pl.DataFrame,
    panel: pl.DataFrame,
    model: LGBMModel,
    years: Iterable[int] | None = None,
) -> dict[str, pl.DataFrame]:
    """Files that change every trading day: live ranking, features, contributions and returns.

    stocks: aligned prices without the market proxy; market: date, close of SPY; panel: ranked
    features from build_panel. years limits which yearly return files are produced.
    """
    latest = panel.filter(pl.col("date") == panel["date"].max())
    live = latest.select("date", "ticker", score=pl.Series(model.predict(latest.select(FEATURES))))
    frames = {
        "signals/latest.parquet": latest_signals(live, universe),
        "features/latest.parquet": latest.select("date", "ticker", *FEATURES),
        "explain/shap.parquet": shap_long(model.contributions(latest.select(FEATURES)), latest["ticker"]),
        "universe.parquet": universe.select("ticker", "name", "sector", "industry").sort("ticker"),
    }
    frames |= _by_year(daily_returns(stocks), "returns/daily", years)
    market_returns = daily_returns(market.with_columns(ticker=pl.lit("SPY"))).select("date", "ret")
    frames |= _by_year(market_returns, "returns/market", years)
    return frames


def research_frames(results: Path, models: Sequence[str]) -> dict[str, pl.DataFrame]:
    """Files from the research runs: out-of-sample score histories and model quality."""
    results = Path(results)
    frames = {
        f"signals/history/{m}.parquet": pl.read_parquet(results / f"scores_{m}.parquet").select("date", "ticker", "score").sort("date", "ticker")
        for m in models
    }
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    frames["quality/models.parquet"] = pl.DataFrame(
        [
            {"model": m}
            | {f"ic_{k}": v for k, v in summary[m]["ic"].items()}
            | {f"net_{k}": v for k, v in summary[m]["net"].items()}
            | {f"gross_{k}": v for k, v in summary[m]["gross"].items()}
            for m in models
        ]
    )
    frames["quality/ic.parquet"] = pl.concat(
        [pl.read_parquet(results / f"ic_{m}.parquet").select("date", "ic", model=pl.lit(m)) for m in models]
    )
    importance = pl.read_parquet(results / "lgbm_importance.parquet")
    frames["quality/importance.parquet"] = (
        importance.with_columns(share=pl.col("gain") / pl.col("gain").sum().over("test_start"))
        .group_by("feature")
        .agg(pl.col("share").mean())
        .sort("share", descending=True)
    )
    return frames


def research_meta(results: Path, models: Sequence[str], holdout_start: str) -> dict:
    """Manifest fields describing the research results being published."""
    results = Path(results)
    history = pl.read_parquet(results / f"scores_{models[0]}.parquet")["date"]
    return {
        "history_start": history.min(),
        "history_end": history.max(),
        "holdout_start": holdout_start,
        "models": list(models),
        "default_backtest": json.loads((results / "turnover_chosen.json").read_text(encoding="utf-8")),
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_default(value):
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def publish(
    storage: Storage,
    prefix: str,
    frames: dict[str, pl.DataFrame],
    meta: dict,
    full: bool = False,
) -> dict:
    """Write frames under prefix and update manifest.json last, so readers never see a manifest
    pointing at files that are not there yet.

    Unchanged files (same hash as in the previous manifest) are not rewritten. full starts the
    manifest from scratch instead of merging with the previous one.
    """
    previous_raw = None if full else storage.get(prefix + MANIFEST)
    previous = json.loads(previous_raw) if previous_raw else {}
    entries = {} if full else dict(previous.get("files", {}))
    for key, frame in frames.items():
        data = parquet_bytes(frame)
        entry = {"sha256": _sha256(data), "bytes": len(data)}
        if entries.get(key) != entry:
            storage.put(prefix + key, data)
        entries[key] = entry
    kept = {k: v for k, v in previous.items() if k not in ("files", "generated_at", "version")}
    manifest = {
        "version": MANIFEST_VERSION,
        **kept,
        **meta,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": dict(sorted(entries.items())),
    }
    storage.put(prefix + MANIFEST, json.dumps(manifest, indent=2, default=_json_default).encode())
    return manifest
