import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis import damage_model as dm  # noqa: E402
from controllers import cost  # noqa: E402
from controllers.costaware import make_estimator  # noqa: E402


class TestDamageModel(unittest.TestCase):
    def test_synthetic_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            summ = dm.main(["--synthetic", "160", "--out", tmp])
            s = summ.set_index(["target", "estimator"])
            # every learned estimator beats predict-the-mean on held-out groups
            for name in ("formula", "tree", "gbr"):
                self.assertLess(s.loc[(dm.TARGET, name), "mae_mean"],
                                s.loc[(dm.TARGET, "mean"), "mae_mean"])
            for f in ("model_summary.md", "model_folds.csv", "model_predictions.csv"):
                self.assertTrue(os.path.exists(os.path.join(tmp, f)))
            self.assertIn("SYNTHETIC", open(os.path.join(tmp, "model_summary.md")).read())
            # the controllers load what the script saved, and predict on a raw feature dict
            feat = dm.records(dm.synthetic(1))[0]
            for kind, fn in (("formula", "formula.json"), ("model", "damage_model.joblib")):
                est = make_estimator(kind, os.path.join(tmp, "models", fn))
                go, why, det = cost.decide_move(feat, est)
                self.assertGreaterEqual(det["cost_bytes"], 0.0)

    def test_synthetic_never_writes_repo_models(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            dm.main(["--synthetic", "40", "--out", tmp, "--models",
                     os.path.join(dm.REPO, "models")])


if __name__ == "__main__":
    unittest.main()
