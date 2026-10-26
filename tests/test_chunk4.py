"""analysis/chunk4.py on the synthetic campaign of analysis/chunk4_fixture.py:
every planted damage term, exclusion, feature and per-run sum must come back.
Venv only (numpy, pandas, scipy, matplotlib), no Mininet; builds the fixture
once, about a minute."""
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from analysis import chunk4  # noqa: E402
from experiments.moves import flow_key  # noqa: E402

SEEDS = 5          # patterns A-D carry the code paths, E has no reroute (damage 0)
TERMS = ["moved_mbps_W%g", "bystanders_mbps_W%g", "dest_total_mbps_W%g", "all_total_mbps_W%g"]


class TestFixtureEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        fx = os.path.join(cls.tmp.name, "fx")
        cls.figs = os.path.join(cls.tmp.name, "figures")
        subprocess.run([sys.executable, os.path.join(REPO, "analysis", "chunk4_fixture.py"),
                        "--out", fx, "--seeds", str(SEEDS), "--mice-step", "20"], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run([sys.executable, os.path.join(REPO, "analysis", "chunk4.py"),
                        "--compare", os.path.join(fx, "compare"), "--damage",
                        os.path.join(fx, "damage"), "--figures", cls.figs], check=True,
                       stdout=subprocess.DEVNULL)
        cls.out = os.path.join(fx, "compare")
        with open(os.path.join(fx, "planted.json")) as f:
            cls.planted = json.load(f)
        cls.events = pd.read_csv(os.path.join(cls.out, "chunk4_events.csv"))
        cls.perrun = pd.read_csv(os.path.join(cls.out, "chunk4_run_damage.csv"))
        cls.contrasts = pd.read_csv(os.path.join(cls.out, "chunk4_contrasts.csv"))
        cls.runs = pd.read_csv(os.path.join(cls.out, "runs.csv"))
        with open(os.path.join(cls.out, "chunk4_summary.md")) as f:
            cls.summary = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def event(self, key):
        live, _, rnd = key.rpartition(" round ")
        e = self.events[(self.events.live_run == live) & (self.events["round"] == int(rnd))]
        return e.iloc[0] if len(e) else None

    def test_synthetic_figures_stay_with_the_tables(self):
        self.assertIn("SYNTHETIC", self.summary)
        self.assertFalse(os.path.exists(self.figs))
        for f in ("chunk4_comparison.png", "chunk4_damage.png", "chunk4_tradeoff.png"):
            self.assertTrue(os.path.exists(os.path.join(self.out, f)), f)

    def test_damage_terms(self):
        ok = {k: v for k, v in self.planted["events"].items() if not v["broken"]}
        self.assertEqual(len(self.events), len(ok))
        for key, p in ok.items():
            r = self.event(key)
            self.assertIsNotNone(r, key)
            self.assertEqual(r.n_moved, len(p["moved"]), key)
            self.assertEqual(r.n_bystanders, len(p["bystanders"]), key)
            self.assertEqual(r.n_left_behind, p["n_left_behind"], key)
            self.assertEqual(r.events_W2_T, p["events_W2_T"], key)
            for t in TERMS:
                for w in chunk4.WINDOWS:
                    self.assertAlmostEqual(r[t % w], p[t % w], places=4,
                                           msg="%s %s" % (key, t % w))
            for k in ("pre_goodput_T_mbps", "pre_goodput_C_mbps"):
                self.assertAlmostEqual(r[k], p[k], places=4, msg="%s %s" % (key, k))

    def test_broken_pairs_are_excluded_and_listed(self):
        broken = {k: v["broken"] for k, v in self.planted["events"].items() if v["broken"]}
        self.assertEqual(sorted(broken.values()), ["error", "missing", "path"])
        why = {"error": "C: run has errors", "missing": "only arm ['T'] present",
               "path": "C: E3 data on [2, 3], expected [2]"}
        for key, how in broken.items():
            self.assertIsNone(self.event(key), key)
            live, _, rnd = key.rpartition(" round ")
            line = [x for x in self.summary.splitlines() if "%s round %s:" % (live, rnd) in x]
            self.assertEqual(len(line), 1, key)
            self.assertIn(why[how], line[0])

    def test_features_come_from_the_first_moved_elephant(self):
        ok = {k: v for k, v in self.planted["events"].items() if not v["broken"]}
        self.assertTrue(any(v["mouse_moved_first_in_round"] for v in ok.values()),
                        "the fixture must exercise a round whose first move is a mouse")
        for key, p in ok.items():
            r = self.event(key)
            for k, v in p["features"].items():
                if v is None:
                    self.assertTrue(pd.isna(r["feat_" + k]), "%s %s" % (key, k))
                else:
                    self.assertAlmostEqual(float(r["feat_" + k]), v, places=6,
                                           msg="%s %s" % (key, k))

    def test_damage_per_run(self):
        d = self.perrun.set_index("live_run")
        self.assertEqual(set(d.controller), {"hedera", "cooldown"})
        by_run = {}
        for key, p in self.planted["events"].items():
            by_run.setdefault(p["live_run"], []).append(p)
        for run, row in d.iterrows():
            evs = by_run.get(run, [])
            if any(p["broken"] for p in evs):
                self.assertTrue(np.isnan(row.dest_total), run)       # incomplete: left out
            else:                                                   # 0 when there is no event
                want = sum(p["dest_total_mbps_W10"] * 10 / 8 * 1e6 for p in evs)
                self.assertAlmostEqual(row.dest_total, want, delta=2.0, msg=run)
            self.assertEqual(row.n_events, len(evs), run)
        self.assertEqual(d.loc["hedera-r05", "dest_total"], 0.0)

    def test_live_reroute_columns(self):
        r = self.runs.set_index("run_id")
        for run, p in self.planted["runs"].items():
            for k in ("n_reroutes", "n_reroutes_other", "n_flaps", "n_reroute_rounds",
                      "snd_reorder_events", "snd_TcpRetransSegs"):
                self.assertEqual(r.loc[run, k], p[k], "%s %s" % (run, k))

    def test_contrasts(self):
        c = self.contrasts
        p1 = c[c.id == "P1"].iloc[0]
        self.assertEqual(p1.n_pairs, SEEDS)
        d = [self.planted["runs"]["hedera-r%02d" % s]["eleph_goodput_mean"] -
             self.planted["runs"]["ecmp-r%02d" % s]["eleph_goodput_mean"]
             for s in range(1, SEEDS + 1)]
        self.assertAlmostEqual(p1["mean"], np.mean(d), places=3)
        self.assertEqual(p1.outcome, "supported")
        s1 = c[c.id == "S1"]
        self.assertEqual(len(s1), 3)
        failed = (s1.outcome == "monitoring changes the result").any()
        self.assertEqual(c.reference.eq("ecmp_mon").any(), failed)


class TestUnits(unittest.TestCase):
    def runs(self, mon_shift):
        rng = np.random.default_rng(0)
        rows = []
        for s in range(1, 7):
            for c, g in (("ecmp", 6.0), ("ecmp_mon", 6.0 + mon_shift), ("hedera", 8.0),
                         ("cooldown", 7.8)):
                r = {k: np.nan for k, _, _ in chunk4.METRICS}
                r.update(run_id="%s-r%02d" % (c, s), controller=c, seed=s,
                         eleph_goodput_mean=g + 0.3 * (s % 3) + rng.normal(0, 0.01),
                         fct_p50_ms=50 + s, fct_p99_ms=1000 + s)
                rows.append(r)
        return pd.DataFrame(rows)

    def test_monitor_only_reference_when_s1_fails(self):
        runs = self.runs(1.0)
        con, L = chunk4.contrasts_table(runs, chunk4.controllers_in(runs))
        s1 = con[(con.id == "S1") & (con.metric == "eleph_goodput_mean")].iloc[0]
        self.assertEqual(s1.outcome, "monitoring changes the result")
        mon = con[con.reference == "ecmp_mon"]
        self.assertEqual(set(mon.controller), {"hedera", "cooldown"})
        self.assertEqual(set(mon[mon.controller == "hedera"].id),
                         {"P1 vs monitor-only", "P2 vs monitor-only"})
        p1m = mon[mon.id == "P1 vs monitor-only"].iloc[0]
        self.assertAlmostEqual(p1m["mean"], 1.0, delta=0.05)

    def test_no_monitor_only_block_when_s1_holds(self):
        runs = self.runs(0.0)
        con, L = chunk4.contrasts_table(runs, chunk4.controllers_in(runs))
        self.assertFalse(con.reference.eq("ecmp_mon").any())

    def test_ss_socket_counts(self):
        """Per-socket W2 counters: only the given local port, missing fields are 0."""
        log = ("T 100.00\n"
               "ESTAB 0 0 10.0.0.1:40001 10.0.0.5:5201\n"
               "\t skmem:(r0) cubic cwnd:20 ssthresh:17 retrans:0/3 dsack_dups:1\n"
               "ESTAB 0 0 10.0.0.1:43000 10.0.0.5:5201\n"
               "\t skmem:(r0) cubic cwnd:2 retrans:0/50 reord_seen:9\n"
               "T 101.00\n"
               "ESTAB 0 0 10.0.0.1:40001 10.0.0.5:5201\n"
               "\t skmem:(r0) cubic cwnd:10 retrans:1/7 reord_seen:2 dsack_dups:4\n"
               "T 103.50\n"
               "ESTAB 0 0 10.0.0.1:40001 10.0.0.5:5201\n"
               "\t skmem:(r0) cubic cwnd:12 retrans:0/9 reord_seen:5 dsack_dups:4\n")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "ss_h1.log"), "w") as f:
                f.write(log)
            s = chunk4.ss_socket(d, "h1", 40001, 90.0)
            self.assertEqual([t for t, _ in s], [10.0, 11.0, 13.5])
            self.assertEqual(s[0][1], dict(retrans=3, reord_seen=0, dsack_dups=1))
            self.assertEqual(chunk4.ss_delta(s, 10.5, 12.5),
                             dict(retrans=4, reord_seen=2, dsack_dups=3))
            self.assertEqual(chunk4.ss_delta(s, 5.0, 9.0),
                             dict(retrans=0, reord_seen=0, dsack_dups=0))
            self.assertEqual(chunk4.ss_socket(d, "h2", 40001, 90.0), [])

    def test_goodput_ignores_the_receivers_closing_rsts(self):
        """RSTs without the ACK flag (ack 0 in the trace) acknowledge nothing."""
        rows = ["t_us,dir,seq,ack,len,flags,sack"]
        for i in range(1, 31):                       # an ACK of 1000 bytes every 0.1 s
            rows.append("%d,1,1,%d,0,16," % (i * 100000, i * 1000))
        for i in range(31, 41):                      # then the closing RSTs
            rows.append("%d,1,2,0,0,4," % (i * 100000))
        with tempfile.TemporaryDirectory() as d:
            with gzip.open(os.path.join(d, "recv_E1_trace.csv.gz"), "wt") as f:
                f.write("\n".join(rows) + "\n")
            g = chunk4.goodput_bytes(d, "E1", 2.05)
            self.assertEqual(g["pre"], 20000.0)         # ACKs at 0.1 .. 2.0 s
            for w in chunk4.WINDOWS:                    # ACKs at 2.1 .. 3.0 s, then RSTs
                self.assertEqual(g[w], 10000.0, w)

    def test_event_features_skip_a_mouse_moved_first(self):
        def rec(sport, dport, rate):
            return dict(flow=dict(src="10.0.0.1", dst="10.0.0.5", sport=sport, dport=dport),
                        rate_mbps=rate, why="gain>0", reply=dict(ok=True))
        moves = [rec(23456, 8000, 1.4), rec(40001, 5201, 2.9), rec(40002, 5202, 4.4)]
        f = chunk4.event_features(moves, flow_key(moves[1]["flow"]))
        self.assertEqual(f["rate_mbps"], 2.9)
        self.assertEqual(f["n_moves_in_round"], 3)
        f = chunk4.event_features([], flow_key(moves[1]["flow"]))
        self.assertTrue(np.isnan(f["n_moves_in_round"]))


if __name__ == "__main__":
    unittest.main()
