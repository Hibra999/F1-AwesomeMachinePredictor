from types import SimpleNamespace
import unittest

import numpy as np

from f1forecast.engine import FEATURES
from f1forecast.inspection import _eda_summary, _study


class InspectionTest(unittest.TestCase):
    def test_eda_uses_observed_quantiles_and_total_missing_denominator(self):
        x = np.full((3, len(FEATURES)), np.nan)
        col = FEATURES.index("driver_form")
        x[:, col] = [0.0, np.nan, 1.0]
        snapshot = SimpleNamespace(x=x, gp={"year": 2024})
        rows = [SimpleNamespace(snapshot=snapshot, gp_id="2024-01")]
        horizon = _eda_summary("fp1", rows, {}, {col: 0.0}, {col: 1})

        self.assertEqual(horizon["entrants"], [{"drivers": 3, "cuts": 1}])
        self.assertEqual(horizon["by_year"][2], {"year": 2024, "gps": 1, "cuts": 1, "driver_rows": 3})
        features = {f["name"]: f for f in horizon["features"]}
        form = features["driver_form"]
        self.assertEqual((form["n"], form["missing"], form["oos_delta_brier"]), (3, 1, 0.0))
        self.assertAlmostEqual(form["missing_pct"], 100 / 3)
        np.testing.assert_allclose([form["p10"], form["median"], form["p90"]], [0.1, 0.5, 0.9])
        self.assertIsNone(features["driver_elo"]["median"])
        self.assertIsNone(features["driver_elo"]["oos_delta_brier"])

    def test_rustuna_study_keeps_trial_details_but_not_unknown_fields(self):
        params = {"learning_rate": 0.05, "n_estimators": 50, "num_leaves": 9}
        trials = [{"trial": i, "params": params, "brier": brier, "seconds": 1.2, "gp_ids": ["2024-01"]}
                  for i, brier in enumerate((0.2, 0.1))]
        raw = {**trials[1], "best_trial": 1, "trials": trials}
        name = "lightgbm/rustuna"
        study = _study(raw, name, 2)

        self.assertEqual(study["best_trial"], 1)
        self.assertEqual(study["trials"][1], {"trial": 1, "params": params, "brier": 0.1, "seconds": 1.2})
        self.assertNotIn("gp_ids", study)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            _study({**raw, "trials": trials[:1]}, name, 2)
        with self.assertRaisesRegex(ValueError, "best trial"):
            _study({**raw, "best_trial": 0}, name, 2)
