#!/usr/bin/env python3
"""Chunk 6: train and evaluate the damage model on the Chunk 5 dataset.

Input: dataset.csv from analysis/damage_dataset.py. Rows with a complete
replay pair (replay_ok = 1) and a measured label are used.

Target: dest_total_deficit_W10, the destination-path total deficit in bytes
over 10 s (= cost.HORIZON_S), the project's single damage number (CHUNKS.md
amendment). The moved and bystander terms are evaluated the same way and
reported next to it. Inputs: controllers/cost.FEATURES only (controller-
visible at decision time, built with cost.vector exactly as the controller
does); the [host] columns are never model inputs.

Estimators, all fitted on the training folds only, hyperparameters fixed here
before any real data exist (no tuning on test folds):
  mean      predict the training mean (baseline 1)
  formula   cost.FormulaCost, coefficients by least squares (baseline 2,
            the hand-written formula)
  tree      DecisionTreeRegressor(max_depth=4, min_samples_leaf=5)
  gbr       GradientBoostingRegressor(200 trees, depth 3, lr 0.05,
            subsample 0.8)
Split: leave-one-group-out over --group (default `group` = live seed) when
there are at most 10 groups, else GroupKFold(10). Use --group scenario (or
any dataset column) for held-out topologies / loads.

Reported per estimator and target: MAE per fold, mean and 95 % t CI over
folds, pooled MAE, and decision agreement: the share of held-out rows where
the rule's verdict with C_hat (gain_bytes > C_hat) equals its verdict with the
measured damage (gain_bytes > damage). Decision time: wall clock of
cost.decide_move per row through the saved estimators, median and p99.

Final fit on all rows; the saved estimators are what the controllers load:
  <models>/formula.json          controllers/costaware_formula.py
  <models>/damage_model.joblib   controllers/costaware_ml.py (gbr, chosen in
                                 advance; the tree is saved as damage_tree.joblib)

  .venv/bin/python analysis/damage_model.py --data results/dataset/<n>/dataset.csv \\
      --out results/model/<n> [--models models] [--group group] [--fig]
  .venv/bin/python analysis/damage_model.py --synthetic 300 --out scratch/model-synth
      (labelled SYNTHETIC; models go to <out>/models, never to models/)
"""

import argparse
import hashlib
import os
import sys
import time

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from analysis.gate import mean_ci  # noqa: E402
from controllers import cost  # noqa: E402

TARGET = "dest_total_deficit_W10"
TERMS = ["moved_deficit_W10", "bystander_deficit_W10", TARGET]
BIG = 1e9            # non-finite inputs (never-moved since_last_move_s), as cost.ModelCost
ESTIMATORS = ["mean", "formula", "tree", "gbr"]


def features(df):
    """X with cost.FEATURES columns, built through cost.vector like the controller."""
    rows = []
    for r in df.to_dict("records"):
        feat = {k: r.get(k) for k in cost.CONTROLLER_FEATURES}
        s = feat.get("since_last_move_s")
        if s is None or (isinstance(s, float) and np.isnan(s)):
            feat["since_last_move_s"] = None
        rows.append(cost.vector(feat))
    x = np.array(rows, dtype=float)
    x[~np.isfinite(x)] = BIG
    return x


def records(df):
    out = []
    for r in df.to_dict("records"):
        feat = {k: r.get(k) for k in cost.CONTROLLER_FEATURES}
        s = feat.get("since_last_move_s")
        if s is None or (isinstance(s, float) and np.isnan(s)):
            feat["since_last_move_s"] = None
        out.append(feat)
    return out


def make(name):
    from sklearn.dummy import DummyRegressor
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.tree import DecisionTreeRegressor
    if name == "mean":
        return DummyRegressor(strategy="mean")
    if name == "tree":
        return DecisionTreeRegressor(max_depth=4, min_samples_leaf=5, random_state=0)
    if name == "gbr":
        return GradientBoostingRegressor(n_estimators=200, max_depth=3, learning_rate=0.05,
                                         subsample=0.8, random_state=0)
    raise ValueError(name)


def fit_formula(recs, y):
    """Least-squares coefficients of cost.FormulaCost's pre-registered terms."""
    a = np.array([[cost.FormulaCost.terms(f)[k] for k in cost.FormulaCost.TERMS] for f in recs])
    coef, *_ = np.linalg.lstsq(a, np.asarray(y, float), rcond=None)
    return cost.FormulaCost(coef=dict(zip(cost.FormulaCost.TERMS, map(float, coef))))


def fit(name, df, y):
    if name == "formula":
        return fit_formula(records(df), y)
    return make(name).fit(features(df), y)


def predict(name, est, df):
    if name == "formula":
        # same clipping at 0 as the controller (FormulaCost.predict)
        return np.array([est.predict(f) for f in records(df)])
    return np.maximum(est.predict(features(df)), 0.0)


def gain_bytes(df):
    g = np.clip(df.gain_mbps.astype(float), 0, None)
    g = np.minimum(g, np.clip(cost.LINK_MBPS - df.rate_mbps.astype(float), 0, None))
    return cost.mbps_to_bytes(g)


def splits(groups):
    from sklearn.model_selection import GroupKFold, LeaveOneGroupOut
    n = len(set(groups))
    if n < 2:
        raise SystemExit("need at least 2 groups for a held-out evaluation, got %d" % n)
    return (LeaveOneGroupOut() if n <= 10 else GroupKFold(n_splits=10)), n


def evaluate(df, group_col):
    groups = df[group_col].astype(str).to_numpy()
    cv, n_groups = splits(groups)
    folds, preds = [], []
    gb = gain_bytes(df).to_numpy()
    for k, (tr, te) in enumerate(cv.split(df, groups=groups)):
        dtr, dte = df.iloc[tr], df.iloc[te]
        for tgt in TERMS:
            ytr, yte = dtr[tgt].to_numpy(float), dte[tgt].to_numpy(float)
            for name in ESTIMATORS:
                p = predict(name, fit(name, dtr, ytr), dte)
                agree = np.mean((gb[te] > p) == (gb[te] > yte)) if tgt == TARGET else np.nan
                folds.append(dict(fold=k, held_out=",".join(sorted(set(groups[te]))),
                                  n_test=len(te), target=tgt, estimator=name,
                                  mae=float(np.mean(np.abs(p - yte))), agreement=agree))
                if tgt == TARGET:
                    preds.extend(dict(fold=k, row=int(i), group=groups[i], estimator=name,
                                      y=float(yy), pred=float(pp), gain_bytes=float(gb[i]))
                                 for i, yy, pp in zip(te, yte, p))
    return pd.DataFrame(folds), pd.DataFrame(preds), n_groups


def summarise(folds, preds):
    out = []
    for (tgt, name), d in folds.groupby(["target", "estimator"], sort=False):
        m, h = mean_ci(d.mae)
        pooled = (np.average(d.mae, weights=d.n_test))
        row = dict(target=tgt, estimator=name, folds=len(d), mae_mean=m, mae_ci95=h,
                   mae_pooled=pooled)
        if tgt == TARGET:
            a, ha = mean_ci(d.agreement)
            row.update(agreement_mean=a, agreement_ci95=ha)
        out.append(row)
    return pd.DataFrame(out)


def decision_times(df, formula_path, model_path, n=500):
    """cost.decide_move wall clock per row (ms) through the saved estimators."""
    recs = records(df)
    recs = (recs * (n // max(len(recs), 1) + 1))[:n]
    out = {}
    for name, est in (("formula", cost.FormulaCost(path=formula_path)),
                      ("gbr", cost.ModelCost(model_path))):
        ts = []
        for f in recs:
            t = time.perf_counter()
            cost.decide_move(f, est)
            ts.append((time.perf_counter() - t) * 1e3)
        out[name] = dict(median_ms=float(np.median(ts)), p99_ms=float(np.percentile(ts, 99)),
                         n=len(ts))
    return out


def save_all(df, models_dir, meta):
    import joblib
    os.makedirs(models_dir, exist_ok=True)
    y = df[TARGET].to_numpy(float)
    fpath = os.path.join(models_dir, "formula.json")
    fit("formula", df, y).save(fpath, fit=dict(meta, target=TARGET))
    paths = {"formula": fpath}
    for name, fn in (("gbr", "damage_model.joblib"), ("tree", "damage_tree.joblib")):
        p = os.path.join(models_dir, fn)
        joblib.dump(dict(model=fit(name, df, y), features=list(cost.FEATURES), kind=name,
                         target=TARGET, horizon_s=cost.HORIZON_S, **meta), p)
        paths[name] = p
    return paths


def synthetic(n, seed=0):
    """A fake dataset in damage_dataset's columns with a known damage function.
    Only for exercising this script and the controllers without Mininet."""
    rng = np.random.default_rng(seed)
    delays = [1, 2, 5, 10]
    rows = []
    for i in range(n):
        p, q = rng.choice(4, 2, replace=False)
        r = rng.uniform(1, 9)
        uq = rng.uniform(0, 9)
        nq = int(rng.integers(0, 3))
        f = dict(rate_mbps=r, U_p=r + rng.uniform(0, 5), U_q=uq, elephants_on_p=1 + int(rng.integers(0, 3)),
                 elephants_on_q=nq, age_s=rng.uniform(1, 30), bytes_so_far=rng.uniform(1e5, 3e7),
                 n_prev_moves=int(rng.integers(0, 4)), delay_ms_p_config=delays[p],
                 delay_ms_q_config=delays[q])
        f["gain_mbps"] = f["U_p"] - (uq + r)
        f["since_last_move_s"] = None if f["n_prev_moves"] == 0 else rng.uniform(1, 20)
        f.update(cost.derive(f))
        gap = f["delay_gap_ms"]
        moved = 3e3 * max(gap, 0) * r + 2e4 * r + rng.normal(0, 2e4)
        byst = 4e3 * uq * nq + rng.normal(0, 2e4)
        rows.append(dict(f, group=int(i % 8) + 1, scenario="synthetic", replay_ok=1,
                         moved_deficit_W10=moved, bystander_deficit_W10=byst,
                         dest_total_deficit_W10=moved + byst))
    return pd.DataFrame(rows)


def write_summary(path, summ, times, info, synthetic_n):
    with open(path, "w") as fh:
        if synthetic_n:
            fh.write("# SYNTHETIC: generated data (--synthetic %d), not a result\n\n" % synthetic_n)
        fh.write("# Damage model evaluation\n\nData: %(data)s (sha256 %(sha)s), %(rows)d rows, "
                 "%(groups)d groups by `%(group_col)s`, split %(split)s. Target %(target)s "
                 "(bytes over %(h)g s). MAE in kB (1 kB = 1000 bytes), mean ± 95 %% CI over "
                 "folds. Agreement = share of held-out rows where the rule decides as it "
                 "would with the measured damage.\n\n" % info)
        fh.write("| target | estimator | MAE kB | pooled MAE kB | agreement |\n|---|---|---|---|---|\n")
        for r in summ.to_dict("records"):
            ag = ("%.2f ± %.2f" % (r["agreement_mean"], r["agreement_ci95"])
                  if r["target"] == TARGET else "")
            fh.write("| %s | %s | %.1f ± %.1f | %.1f | %s |\n" % (
                r["target"], r["estimator"], r["mae_mean"] / 1e3, r["mae_ci95"] / 1e3,
                r["mae_pooled"] / 1e3, ag))
        fh.write("\nDecision time per flow (cost.decide_move, saved estimators):\n\n")
        for k, v in times.items():
            fh.write("- %s: median %.3f ms, p99 %.3f ms (n = %d)\n" % (k, v["median_ms"],
                                                                      v["p99_ms"], v["n"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", help="dataset.csv from analysis/damage_dataset.py")
    ap.add_argument("--synthetic", type=int, default=0, help="use N generated rows instead")
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default=None,
                    help="where the controllers' estimators go (default models/, or "
                    "<out>/models with --synthetic)")
    ap.add_argument("--group", default="group", help="dataset column for held-out groups")
    ap.add_argument("--fig", action="store_true", help="write <out>/damage_model.png")
    a = ap.parse_args(argv)
    if bool(a.data) == bool(a.synthetic):
        ap.error("give exactly one of --data and --synthetic")
    os.makedirs(a.out, exist_ok=True)
    if a.synthetic:
        df = synthetic(a.synthetic)
        src, sha = "synthetic", "-"
        models_dir = a.models or os.path.join(a.out, "models")
        if os.path.abspath(models_dir) == os.path.join(REPO, "models"):
            ap.error("synthetic models never go to models/")
    else:
        df = pd.read_csv(a.data)
        src, sha = a.data, hashlib.sha256(open(a.data, "rb").read()).hexdigest()[:16]
        models_dir = a.models or os.path.join(REPO, "models")
    n_all = len(df)
    df = df[(df.replay_ok == 1) & df[TERMS].notna().all(axis=1)].reset_index(drop=True)
    print("rows: %d of %d usable" % (len(df), n_all))
    folds, preds, n_groups = evaluate(df, a.group)
    summ = summarise(folds, preds)
    meta = dict(data=src, data_sha256=sha, n_rows=len(df), synthetic=bool(a.synthetic))
    paths = save_all(df, models_dir, meta)
    times = decision_times(df, paths["formula"], paths["gbr"])
    folds.to_csv(os.path.join(a.out, "model_folds.csv"), index=False)
    preds.to_csv(os.path.join(a.out, "model_predictions.csv"), index=False)
    summ.to_csv(os.path.join(a.out, "model_summary.csv"), index=False)
    info = dict(data=src, sha=sha, rows=len(df), groups=n_groups, group_col=a.group,
                split="leave-one-group-out" if n_groups <= 10 else "GroupKFold(10)",
                target=TARGET, h=cost.HORIZON_S)
    write_summary(os.path.join(a.out, "model_summary.md"), summ, times, info, a.synthetic)
    print(open(os.path.join(a.out, "model_summary.md")).read())
    print("estimators: %s" % ", ".join(paths.values()))
    if a.fig:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(4.5, 4.5))
        for name in ("formula", "gbr"):
            d = preds[preds.estimator == name]
            ax.scatter(d.y / 1e3, d.pred / 1e3, s=8, alpha=0.6, label=name)
        lim = [min(preds.y.min(), 0) / 1e3, preds.y.max() / 1e3]
        ax.plot(lim, lim, "k--", lw=0.8)
        ax.set_xlabel("measured damage, kB over W10")
        ax.set_ylabel("predicted (held-out), kB")
        ax.legend()
        if a.synthetic:
            ax.set_title("SYNTHETIC")
        fig.tight_layout()
        fig.savefig(os.path.join(a.out, "damage_model.png"), dpi=150)
    return summ


if __name__ == "__main__":
    main()
