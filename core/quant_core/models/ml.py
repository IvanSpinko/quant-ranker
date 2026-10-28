"""Trained models on ranked features. Inputs are polars frames of feature columns."""

from collections.abc import Mapping
from typing import Protocol

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from quant_core.features import cross_sectional_rank


class Model(Protocol):
    def fit(self, X: pl.DataFrame, y: pl.Series) -> "Model": ...

    def predict(self, X: pl.DataFrame) -> np.ndarray: ...


class RidgeModel:
    """Linear model; missing ranked features are set to the cross-sectional median (0)."""

    def __init__(self, alpha: float = 10.0):
        self.model = Ridge(alpha=alpha)

    def fit(self, X: pl.DataFrame, y: pl.Series) -> "RidgeModel":
        self.model.fit(X.fill_null(0.0).to_numpy(), y.to_numpy())
        return self

    def predict(self, X: pl.DataFrame) -> np.ndarray:
        return self.model.predict(X.fill_null(0.0).to_numpy())

    def coefficients(self, features: list[str]) -> pl.DataFrame:
        return pl.DataFrame({"feature": features, "coef": self.model.coef_.astype(float)})


LGBM_DEFAULTS = {
    "n_estimators": 300,
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_child_samples": 500,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "random_state": 0,
    "n_jobs": -1,
    "verbose": -1,
}


class LGBMModel:
    """Gradient-boosted trees; LightGBM handles missing values natively.

    A fitted model round-trips through to_string / from_string, which is how the cloud
    pipeline stores the live model.
    """

    def __init__(self, **params):
        self.params = LGBM_DEFAULTS | params
        self.booster: lgb.Booster | None = None
        self.features: list[str] = []

    def fit(self, X: pl.DataFrame, y: pl.Series) -> "LGBMModel":
        self.features = list(X.columns)
        model = lgb.LGBMRegressor(**self.params).fit(X.to_numpy(), y.to_numpy(), feature_name=self.features)
        self.booster = model.booster_
        return self

    def predict(self, X: pl.DataFrame) -> np.ndarray:
        return self.booster.predict(X.select(self.features).to_numpy())

    def contributions(self, X: pl.DataFrame) -> pl.DataFrame:
        """Per-row SHAP contributions (LightGBM's TreeSHAP) plus the shared base value."""
        values = self.booster.predict(X.select(self.features).to_numpy(), pred_contrib=True)
        return pl.DataFrame(values, schema=[*self.features, "base_value"], orient="row")

    def feature_importance(self) -> pl.DataFrame:
        gain = self.booster.feature_importance("gain").astype(float)
        return pl.DataFrame({"feature": self.features, "gain": gain}).sort("gain", descending=True)

    def to_string(self) -> str:
        return self.booster.model_to_string()

    @classmethod
    def from_string(cls, text: str) -> "LGBMModel":
        model = cls()
        model.booster = lgb.Booster(model_str=text)
        model.features = model.booster.feature_name()
        return model


def combine_scores(scores: Mapping[str, pl.DataFrame], weights: Mapping[str, float] | None = None) -> pl.DataFrame:
    """Ensemble: weighted mean of per-date score ranks over the (date, ticker) rows all models share."""
    weights = weights or dict.fromkeys(scores, 1.0)
    total = sum(weights.values())
    combined = None
    for name, frame in scores.items():
        ranked = cross_sectional_rank(frame.select("date", "ticker", "score"), ["score"])
        ranked = ranked.select("date", "ticker", pl.col("score").fill_null(0.0).alias(name))
        combined = ranked if combined is None else combined.join(ranked, on=["date", "ticker"], how="inner")
    return combined.select(
        "date",
        "ticker",
        score=pl.sum_horizontal(pl.col(n) * (w / total) for n, w in weights.items()),
    ).sort("date", "ticker")
