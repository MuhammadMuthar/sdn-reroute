"""experiments/metrics.py helpers that do not need a run directory."""
import os
import sys
import unittest

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from experiments.metrics import censored_percentile  # noqa: E402


class TestCensoredPercentile(unittest.TestCase):
    def test_finite_is_numpy(self):
        x = [0.05, 0.04, 1.2, 0.3, 0.07]
        for q in (50, 99):
            self.assertAlmostEqual(censored_percentile(x, q), np.percentile(x, q))

    def test_failed_mice_stay_infinite(self):
        self.assertEqual(censored_percentile([1, 2, np.inf], 50), 2.0)     # numpy: nan
        self.assertEqual(censored_percentile([1, 2, 3, np.inf], 99), np.inf)
        self.assertEqual(censored_percentile([np.inf, np.inf], 50), np.inf)
        self.assertEqual(censored_percentile([1, 2, 3, 4, np.inf], 50), 3.0)


if __name__ == "__main__":
    unittest.main()
