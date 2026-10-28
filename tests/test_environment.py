"""Smoke test: the core package and the numeric stack import and work."""

import lightgbm
import numpy as np
import polars as pl

import quant_core


def test_package_imports():
    assert quant_core.__version__


def test_numeric_stack():
    df = pl.DataFrame({"x": np.arange(5, dtype=float)})
    assert df["x"].sum() == 10.0
    assert lightgbm.__version__
