"""Expanding-window walk-forward validation.

A label at date index i covers returns up to index i + lag + horizon. Training dates whose
labels reach into the test window are purged, and an embargo adds a further gap. Dates whose
labels reach into the hold-out are never used, neither for training nor for testing.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import polars as pl

from quant_core.models.ml import Model


@dataclass(frozen=True)
class Fold:
    """Inclusive date bounds of one training and one test window."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date


def walk_forward_splits(
    dates: Sequence[date] | pl.Series,
    *,
    min_train: int = 756,
    test_size: int = 63,
    purge: int = 6,
    embargo: int = 5,
    holdout_start: date | None = None,
) -> list[Fold]:
    """Folds over sorted unique trading dates.

    min_train: training dates in the first fold; test_size: dates per test window;
    purge: lag + horizon of the label; embargo: extra dates dropped before each test window.
    """
    days = sorted(set(dates))
    end = len(days)
    if holdout_start is not None:
        first_holdout = next((i for i, d in enumerate(days) if d >= holdout_start), end)
        end = max(first_holdout - purge, 0)
    gap = purge + embargo
    folds = []
    test_start = min_train + gap
    while test_start < end:
        test_end = min(test_start + test_size, end)
        folds.append(Fold(days[0], days[test_start - gap - 1], days[test_start], days[test_end - 1]))
        test_start = test_end
    return folds


def walk_forward_predict(
    panel: pl.DataFrame,
    features: Sequence[str],
    make_model: Callable[[], Model],
    folds: Sequence[Fold],
    target: str = "target",
    sample_every: int = 5,
    on_fold: Callable[[Fold, Model], None] | None = None,
) -> pl.DataFrame:
    """Out-of-sample scores (date, ticker, score, fold) from a fresh model per fold.

    sample_every keeps every n-th training date: neighbouring 5-day labels overlap heavily,
    so thinning loses little information and speeds training up.
    """
    features = list(features)
    day_index = panel.select(pl.col("date").unique().sort()).with_row_index("day")
    panel = panel.join(day_index, on="date", how="left")
    out = []
    for k, fold in enumerate(folds):
        train = panel.filter(
            pl.col("date").is_between(fold.train_start, fold.train_end),
            pl.col(target).is_not_null(),
            pl.col("day") % sample_every == 0,
        )
        test = panel.filter(pl.col("date").is_between(fold.test_start, fold.test_end))
        if train.is_empty() or test.is_empty():
            continue
        model = make_model().fit(train.select(features), train[target])
        if on_fold is not None:
            on_fold(fold, model)
        out.append(
            test.select("date", "ticker").with_columns(
                score=pl.Series(model.predict(test.select(features)), dtype=pl.Float64),
                fold=pl.lit(k, dtype=pl.Int32),
            )
        )
    if not out:
        return pl.DataFrame(schema={"date": pl.Date, "ticker": pl.String, "score": pl.Float64, "fold": pl.Int32})
    return pl.concat(out)
