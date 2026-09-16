"""Fleet Commander — MVP ML package (EVAL-01 / ML-01).

Seeded, dependency-light core: synthetic scenario generator + feature builder.
The sklearn-dependent parts (model.py, inference.py) degrade gracefully when
sklearn is absent — callers fall back to the legacy slope heuristics and count
a `fleet_ml_fallback_total` metric. Nothing here trains on real fleet data yet;
the bootstrap model is a documented stand-in until LOOP-01 lands.
"""

from app.ml.features import FEATURE_NAMES, series_to_features
from app.ml.scenarios import SCENARIO_KINDS, make_series

__all__ = ["FEATURE_NAMES", "series_to_features", "SCENARIO_KINDS", "make_series"]
