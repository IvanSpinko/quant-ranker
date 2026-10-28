"""Untrained baselines: a fixed weighted sum of ranked features."""

from collections.abc import Callable, Mapping

import numpy as np
import polars as pl


class FeatureScore:
    """Score = sum of weight * ranked feature, missing features counted as the median (0)."""

    def __init__(self, weights: Mapping[str, float]):
        self.weights = dict(weights)

    def fit(self, X: pl.DataFrame, y: pl.Series) -> "FeatureScore":
        return self

    def predict(self, X: pl.DataFrame) -> np.ndarray:
        score = pl.sum_horizontal(pl.col(f).fill_null(0.0) * w for f, w in self.weights.items())
        return X.select(score).to_series().to_numpy()


BASELINES: dict[str, Callable[[], FeatureScore]] = {
    "momentum_12_1": lambda: FeatureScore({"mom_12_1": 1.0}),
    "reversal_5": lambda: FeatureScore({"mom_5": -1.0}),
    "combo": lambda: FeatureScore({"mom_12_1": 0.5, "mom_5": -0.5}),
}
