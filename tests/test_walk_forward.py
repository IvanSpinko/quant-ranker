"""Walk-forward splits are disjoint, purged, embargoed and never touch the hold-out."""

import numpy as np
import polars as pl
import pytest

from quant_core.features import FEATURES, build_panel
from quant_core.models import BASELINES, LGBMModel, RidgeModel, combine_scores
from quant_core.validation import daily_ic, ic_summary, walk_forward_predict, walk_forward_splits

from conftest import business_days


@pytest.fixture
def dates():
    return business_days(1000)


def test_folds_are_disjoint_and_purged(dates):
    purge, embargo = 6, 5
    folds = walk_forward_splits(dates, min_train=300, test_size=50, purge=purge, embargo=embargo)
    index = {d: i for i, d in enumerate(dates)}
    assert len(folds) > 5
    for fold in folds:
        assert fold.train_start == dates[0]
        assert index[fold.test_start] - index[fold.train_end] == purge + embargo + 1
    for a, b in zip(folds, folds[1:]):
        assert index[b.test_start] == index[a.test_end] + 1
        assert index[b.train_end] > index[a.train_end]
    assert folds[0].test_start == dates[300 + purge + embargo]
    assert folds[-1].test_end == dates[-1]


def test_holdout_is_untouched(dates):
    holdout_start = dates[800]
    folds = walk_forward_splits(dates, min_train=300, test_size=50, holdout_start=holdout_start, purge=6)
    # A label reaches 6 dates ahead, so the last usable date is 7 before the hold-out.
    assert folds[-1].test_end == dates[800 - 7]
    assert all(f.test_end < holdout_start and f.train_end < holdout_start for f in folds)


def test_no_folds_without_enough_history(dates):
    assert walk_forward_splits(dates[:200], min_train=300) == []


@pytest.fixture(scope="module")
def planted():
    """A panel whose target is a noisy function of one feature, so a model can find it."""
    rng = np.random.default_rng(1)
    days = business_days(300)
    n = 40
    rows = []
    for d in days:
        x = rng.uniform(-0.5, 0.5, (n, len(FEATURES)))
        y = 0.8 * x[:, 0] + rng.normal(0, 0.2, n)
        rows.append(
            pl.DataFrame({"date": [d] * n, "ticker": [f"S{i}" for i in range(n)], "target": y}).hstack(
                pl.DataFrame(x, schema=FEATURES)
            )
        )
    return pl.concat(rows)


@pytest.mark.parametrize("make_model", [RidgeModel, lambda: LGBMModel(n_estimators=50, min_child_samples=20)])
def test_walk_forward_finds_planted_signal(planted, make_model):
    folds = walk_forward_splits(planted["date"], min_train=100, test_size=40)
    scores = walk_forward_predict(planted, FEATURES, make_model, folds)
    tested = scores["date"].unique()
    assert tested.min() == folds[0].test_start and tested.max() == folds[-1].test_end
    assert not scores.filter(pl.col("date") <= folds[0].train_end).height
    summary = ic_summary(daily_ic(scores, planted))
    assert summary["mean_ic"] > 0.5


def test_baselines_and_ensemble_run_on_real_features(synthetic):
    panel = build_panel(synthetic["prices"], synthetic["market"], synthetic["sectors"])
    folds = walk_forward_splits(panel["date"], min_train=100, test_size=60)
    scores = {name: walk_forward_predict(panel, FEATURES, make, folds) for name, make in BASELINES.items()}
    scores["ridge"] = walk_forward_predict(panel, FEATURES, RidgeModel, folds)
    combined = combine_scores({"ridge": scores["ridge"], "combo": scores["combo"]})
    assert combined.height == scores["ridge"].height
    assert combined["score"].is_between(-0.5, 0.5).all()
    for frame in scores.values():
        ic = daily_ic(frame, panel)
        assert ic["ic"].is_between(-1, 1).all()


def test_ic_is_one_for_a_perfect_score(planted):
    perfect = planted.select("date", "ticker", score=pl.col("target"))
    assert daily_ic(perfect, planted)["ic"].min() == pytest.approx(1.0)
