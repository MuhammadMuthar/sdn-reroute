"""Cost-aware controller, ml variant (COST_ESTIMATOR=model; see controllers/costaware.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers.costaware import CostAwareController  # noqa: E402


class CostAwareMLController(CostAwareController):
    APP_NAME = "costaware_ml"
    ESTIMATOR = "model"
