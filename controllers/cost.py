"""Damage estimators and the cost-aware decision rule (Chunk 6), stdlib only.

One interface for both variants of our controller, so controllers/costaware.py
and the offline evaluation (analysis/damage_model.py) call the same code:

    est = FormulaCost()            # hand-written formula, coefficients fitted
    est = ModelCost(path)          # sklearn model trained by analysis/damage_model.py
    c_hat = est.predict(features)  # bytes of goodput deficit over HORIZON_S

`features` is the pre-move feature dict the Hedera-style controller already
writes to policy_log.jsonl for every move (controllers/hedera.py, _decide),
so the model is trained and used on identical inputs. CONTROLLER_FEATURES
lists the keys, DERIVED adds the few that are computed from them.

Decision rule (CLAUDE.md, with gain and cost in the same units, CHUNK0_REVIEW
1.1): move f from p to q only if

    gain_bytes(f, p, q) > C_hat(f, p, q)   and   t - t_last(f) > T_cool

gain_bytes = the extra goodput f is expected to get over HORIZON_S if it
moves: (U_p - U_q - r_f) Mbit/s, the same quantity the Hedera-style
controller maximises, clipped to [0, LINK - r_f], times the horizon. C_hat is
the predicted destination-path total deficit (moved flow + bystanders) over
the same horizon, the project's single damage number (CHUNKS.md amendment).
"""

import json
import math
import os
import time

HORIZON_S = 10.0          # W10, the longest damage window measured (analysis/loaded_move.py)
LINK_MBPS = 10.0
T_COOL_S = 5.0            # same as the cooldown-only ablation (controllers/cooldown.py)

# keys of hedera.py's per-move feature record that the model may use
CONTROLLER_FEATURES = [
    "rate_mbps",            # moved flow's rate in the last interval
    "U_p", "U_q",           # path load (leaf uplink tx rate) of source and destination
    "gain_mbps",            # U_p - (U_q + r_f)
    "elephants_on_p", "elephants_on_q",
    "age_s",                # time since the flow's SYN
    "bytes_so_far",
    "n_prev_moves",
    "since_last_move_s",    # None for a never-moved flow -> inf
    "delay_ms_p_config", "delay_ms_q_config",
    "link_mbps",            # Chunk 7b: the link rate the controller is configured with
]
# Chunk 7b adds the last three (pre-registered, LOG.md 2026-11-14)
DERIVED = ["delay_gap_ms", "rate_share_q", "bytes_in_flight_est",
           "delay_ratio_qp", "window_ratio_q", "q_empty"]
FEATURES = CONTROLLER_FEATURES + DERIVED


def mbps_to_bytes(mbps, horizon_s=HORIZON_S):
    return mbps * 1e6 / 8.0 * horizon_s


def link_of(feat):
    """The feature record's link rate; LINK_MBPS for records written before
    Chunk 7b (no link_mbps key, or NaN)."""
    v = feat.get("link_mbps")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return LINK_MBPS
    return LINK_MBPS if math.isnan(v) else v


def derive(feat):
    """The DERIVED features from a controller feature dict (pure function)."""
    out = {}
    link = link_of(feat)
    d_p, d_q = float(feat["delay_ms_p_config"]), float(feat["delay_ms_q_config"])
    r = float(feat["rate_mbps"])
    out["delay_gap_ms"] = float(feat["delay_ms_q_config"]) - float(feat["delay_ms_p_config"])
    # the moved flow's share of what the destination path will carry
    denom = float(feat["U_q"]) + float(feat["rate_mbps"])
    out["rate_share_q"] = float(feat["rate_mbps"]) / denom if denom > 0 else 1.0
    # bytes in flight the controller can estimate: rate x path RTT (2 x one-way delay)
    out["bytes_in_flight_est"] = float(feat["rate_mbps"]) * 1e6 / 8.0 * \
        2.0 * float(feat["delay_ms_p_config"]) / 1e3
    # Chunk 7b: configured delays; RTT taken as 2 x one-way delay on both paths
    out["delay_ratio_qp"] = d_q / d_p if d_p > 0 else math.inf
    out["window_ratio_q"] = (r * d_p) / (link * d_q) if link > 0 and d_q > 0 else math.inf
    out["q_empty"] = 1.0 if float(feat["elephants_on_q"]) == 0 else 0.0
    return out


def vector(feat):
    """FEATURES as a list of floats, in FEATURES order (None -> inf for
    since_last_move_s, NaN otherwise)."""
    f = dict(feat)
    f.update(derive(feat))
    out = []
    for k in FEATURES:
        v = f.get(k)
        if v is None:
            v = math.inf if k == "since_last_move_s" else math.nan
        out.append(float(v))
    return out


class DamageEstimator:
    name = "base"

    def predict(self, feat):
        """Predicted destination-path total deficit, bytes over HORIZON_S."""
        raise NotImplementedError

    def describe(self):
        return dict(name=self.name)


class ZeroCost(DamageEstimator):
    """C_hat = 0: with this estimator the rule is the Hedera-style greedy
    rule plus the cooldown (a check that the wiring changes nothing else)."""
    name = "zero"

    def predict(self, feat):
        return 0.0


class FormulaCost(DamageEstimator):
    """Hand-written structure, fitted coefficients.

    The structure follows what Chunks 2-3 measured (STATUS.md): the moved
    flow's damage grows with the delay gap it crosses (reordering, RTO) and
    with its rate (bytes in flight that arrive out of order); a flow landing
    on a loaded path takes share from the bystanders, so their deficit grows
    with the destination load relative to the moved flow's rate. Terms:

        C_hat = a0 + a1 * max(gap_ms, 0) * r_f
                   + a2 * r_f
                   + a3 * U_q * elephants_on_q
                   + a4 * bytes_in_flight_est
                   + a5 * q_empty * max(0, link - r_f * d_p / d_q)
                                                       (all in bytes over H)

    The a5 term (Chunk 7b, LOG.md 2026-11-14) is the window mismatch Chunk 7a
    measured: a flow moved onto an EMPTY path with a longer delay arrives with
    the window of its old path, i.e. a rate of about r_f * d_p / d_q there,
    and needs seconds to grow to the link rate.

    The coefficients are NOT invented: the defaults are all zero (predict
    nothing) until analysis/damage_model.py fits them by least squares on the
    Chunk 5 dataset and writes them to models/formula.json, which is then
    loaded here. The structure is what is pre-registered, the numbers are
    measured.
    """
    name = "formula"
    TERMS = ["const", "gap_x_rate", "rate", "load_q_x_n", "bif", "ramp"]

    def __init__(self, coef=None, path=None):
        if path:
            with open(path) as fh:
                saved = json.load(fh)
            coef = saved["coef"]
            self.fit_info = saved.get("fit", {})
        self.coef = dict.fromkeys(self.TERMS, 0.0)
        if coef:
            self.coef.update(coef)

    @staticmethod
    def terms(feat):
        d = derive(feat)
        r = float(feat["rate_mbps"])
        return dict(const=1.0,
                    gap_x_rate=max(d["delay_gap_ms"], 0.0) * r,
                    rate=r,
                    load_q_x_n=float(feat["U_q"]) * float(feat["elephants_on_q"]),
                    bif=d["bytes_in_flight_est"],
                    ramp=d["q_empty"] * max(0.0, link_of(feat) - r * float(feat["delay_ms_p_config"]) /
                                            float(feat["delay_ms_q_config"])))

    def predict(self, feat):
        t = self.terms(feat)
        return max(0.0, sum(self.coef[k] * t[k] for k in self.TERMS))

    def describe(self):
        return dict(name=self.name, coef=self.coef)

    def save(self, path, fit=None):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(dict(coef=self.coef, terms=self.TERMS, fit=fit or {}), fh, indent=1)


class ModelCost(DamageEstimator):
    """A model saved by analysis/damage_model.py (joblib: sklearn regressor +
    the FEATURES list it was trained on). Imports sklearn lazily so this
    module stays importable without the venv."""
    name = "model"

    def __init__(self, path):
        import joblib
        saved = joblib.load(path)
        self.model = saved["model"]
        self.features = list(saved["features"])
        self.info = {k: v for k, v in saved.items() if k not in ("model",)}
        if self.features != FEATURES:
            raise ValueError("model trained on %s, controller provides %s" %
                             (self.features, FEATURES))

    def predict(self, feat):
        import numpy as np
        x = np.array([vector(feat)], dtype=float)
        x[~np.isfinite(x)] = 1e9       # since_last_move_s = inf for never-moved flows
        return max(0.0, float(self.model.predict(x)[0]))

    def describe(self):
        return dict(name=self.name, **{k: v for k, v in self.info.items()
                                      if isinstance(v, (str, int, float))})


def decide_move(feat, estimator, horizon_s=HORIZON_S, t_cool_s=T_COOL_S,
                link_mbps=LINK_MBPS):
    """The cost-aware rule on one candidate (flow, p, q).

    Returns (go, why, details). details has gain_bytes, cost_bytes and the
    estimator's wall-clock decision time, so the controller can log them
    with the move (decision time per flow is a Chunk 6 metric)."""
    since = feat.get("since_last_move_s")
    cool = since is None or since >= t_cool_s
    r = float(feat["rate_mbps"])
    gain_mbps = min(max(float(feat["gain_mbps"]), 0.0), max(link_mbps - r, 0.0))
    gain_bytes = mbps_to_bytes(gain_mbps, horizon_s)
    t = time.perf_counter()
    cost_bytes = float(estimator.predict(feat))
    dt_ms = (time.perf_counter() - t) * 1e3
    go = cool and gain_bytes > cost_bytes
    why = ("gain>cost" if gain_bytes > cost_bytes else "gain<=cost") + ("" if cool else ",cooling")
    return go, why, dict(gain_bytes=gain_bytes, cost_bytes=cost_bytes, horizon_s=horizon_s,
                         estimator=estimator.name, decision_ms=dt_ms)
