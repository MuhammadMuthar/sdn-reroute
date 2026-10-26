"""Cost-aware controller, formula variant (COST_ESTIMATOR=formula; see controllers/costaware.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers.costaware import CostAwareController  # noqa: E402


class CostAwareFormulaController(CostAwareController):
    APP_NAME = "costaware_formula"
    ESTIMATOR = "formula"
