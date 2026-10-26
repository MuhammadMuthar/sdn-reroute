"""Cost-aware rerouting (Chunk 6): the Hedera-style controller with our rule.

Everything except the rule is controllers/hedera.py (monitoring, elephant
threshold, 1 s polling, least-loaded target, feature record, logging), so the
five controllers differ only in their decision. The rule is controllers/cost.
decide_move on hedera's own pre-move feature record:

    move f from p to q only if gain_bytes > C_hat(f, p, q) and the flow was
    not moved in the last T_COOL_S (cost.py: gain and cost in bytes over
    HORIZON_S)

C_hat comes from COST_ESTIMATOR:
  formula   cost.FormulaCost, coefficients from COST_MODEL (default
            models/formula.json, written by analysis/damage_model.py)
  model     cost.ModelCost, sklearn model from COST_MODEL (default
            models/damage_model.joblib, written by analysis/damage_model.py)
  zero      C_hat = 0 (wiring check: Hedera-style greedy plus the cooldown)
A missing model file stops the controller at start: it never falls back to
C_hat = 0 silently. The runner names the variants by file:
controllers/costaware_formula.py, controllers/costaware_ml.py.

Logs: every move's record in policy_log.jsonl also has gain_bytes, cost_bytes,
estimator and decision_ms (decision time per flow, a Chunk 6 metric); the
candidates the rule held back are logged once per round as kind "held", so
"moves avoided" can be counted.
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from controllers import cost  # noqa: E402
from controllers.hedera import HederaController  # noqa: E402

DEFAULT_MODEL = {"formula": os.path.join(REPO, "models", "formula.json"),
                 "model": os.path.join(REPO, "models", "damage_model.joblib"),
                 "zero": None}


def make_estimator(kind, path=None):
    if kind not in DEFAULT_MODEL:
        raise ValueError("COST_ESTIMATOR must be one of %s" % sorted(DEFAULT_MODEL))
    if kind == "zero":
        return cost.ZeroCost()
    path = path or DEFAULT_MODEL[kind]
    if not os.path.exists(path):
        raise FileNotFoundError("%s estimator needs %s (run analysis/damage_model.py)"
                                % (kind, path))
    return cost.FormulaCost(path=path) if kind == "formula" else cost.ModelCost(path)


class CostAwareController(HederaController):
    APP_NAME = "costaware"
    POLICY = "costaware"
    POLICIES = ("none", "costaware")
    ESTIMATOR = "formula"

    def __init__(self, *args, **kwargs):
        # before super().__init__, which logs the config record and starts polling
        self.estimator = make_estimator(os.environ.get("COST_ESTIMATOR", self.ESTIMATOR),
                                        os.environ.get("COST_MODEL"))
        self.COOLDOWN_S = cost.T_COOL_S
        self.held = []
        super().__init__(*args, **kwargs)
        self._log(dict(kind="estimator", horizon_s=cost.HORIZON_S, t_cool_s=cost.T_COOL_S,
                       estimator=self.estimator.describe()))   # nested: a model's own keys include 'kind'

    def _rule(self, feat):
        go, why, det = cost.decide_move(feat, self.estimator, link_mbps=self.LINK_MBPS)
        feat.update(det)
        if not go and feat["gain_mbps"] > 0:
            # a move the greedy rule would have made: keep it for "moves avoided"
            self.held.append(dict(feat, why=why))
        return go, why

    def _decide(self, now):
        self.held = []
        moves = super()._decide(now)
        if self.held:
            self._log(dict(kind="held", round=self.round, t=now, held=self.held))
        return moves
