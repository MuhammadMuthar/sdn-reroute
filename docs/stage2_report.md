# Stage 2 milestone report

The submitted report is `docs/EE353_Stage2_Milestone_Mutahar_Uswa.pdf` (2 pages).
Its text lives in `docs/build_stage2_pdf.py`. To rebuild:

```bash
.venv/bin/python analysis/stage2_report.py     # figures/stage2_results.png, docs/stage2_numbers.md
.venv/bin/python docs/build_stage2_pdf.py docs/EE353_Stage2_Milestone_Mutahar_Uswa.pdf   # needs reportlab
```

Where each number comes from:

| In the report | Source |
|---|---|
| Table 1, Figure 1, paired differences, the 60 reroutes, model error | `docs/stage2_numbers.md`, recomputed from the raw run files by `analysis/stage2_report.py` |
| ECMP goodput by elephants sharing an uplink (8.70 / 4.42 / 3.09 Mbit/s) | `results/ecmp/ecmp-2026-10-19/summary.md` |
| Cost-aware minus ECMP (+2.54, +2.41 Mbit/s) | `results/compare/five-way-2026-10-26/chunk6_summary.md` |
| Reordering events per run with and without monitoring (about 3 vs about 1000), short-flow median with monitoring only | `results/compare/compare-2026-10-26/chunk4_summary.md` |
| Decision time per flow (0.19 ms ML, 0.002 ms formula), 90 training rows | `results/model/model-2026-10-26/model_summary.md` |
| 100 Mbit/s pilot (16 trials) | `results/dataset/regime-2026-10-26/regime_pilot.md` |
| Damage and benefit per run in MB (0.03 ± 0.04, 38.9 ± 14.5) | `results/compare/compare-2026-10-26/chunk4_summary.md`, "Damage per run" table |
