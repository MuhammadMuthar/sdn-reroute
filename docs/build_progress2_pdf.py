#!/usr/bin/env python3
"""Builds the second progress report PDF (after Stage 2), A4.

  python3 analysis/progress2_figure.py              # figures/progress2_regime.png
  python3 docs/build_progress2_pdf.py docs/EE353_Progress_Report_2_Mutahar_Uswa.pdf

Every number in the text is in results/regime/summary/regime_cells.md,
flap_cells.csv or crosscheck.md (analysis/regime.py, regime_crosscheck.py). Needs reportlab and the
Liberation fonts (apt: fonts-liberation).
"""

import os
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = sys.argv[1]
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FD = "/usr/share/fonts/truetype/liberation/"
for name, f in [("Body", "LiberationSerif-Regular.ttf"), ("Body-B", "LiberationSerif-Bold.ttf"),
                ("Body-I", "LiberationSerif-Italic.ttf"), ("Body-BI", "LiberationSerif-BoldItalic.ttf")]:
    pdfmetrics.registerFont(TTFont(name, FD + f))
addMapping("Body", 0, 0, "Body"); addMapping("Body", 1, 0, "Body-B")
addMapping("Body", 0, 1, "Body-I"); addMapping("Body", 1, 1, "Body-BI")

SZ = 9.4
P = ParagraphStyle("p", fontName="Body", fontSize=SZ, leading=SZ * 1.2, alignment=TA_JUSTIFY,
                   spaceAfter=2.6)
H = ParagraphStyle("h", parent=P, fontName="Body-B", fontSize=10.4, spaceBefore=4.5, spaceAfter=1.8,
                   alignment=0)
T = ParagraphStyle("t", parent=P, fontName="Body-B", fontSize=14.5, leading=17, alignment=TA_CENTER,
                   spaceAfter=3)
C = ParagraphStyle("c", parent=P, alignment=TA_CENTER, spaceAfter=1.5)
CAP = ParagraphStyle("cap", parent=P, fontSize=8.5, leading=10.2, spaceAfter=3)
CELL = ParagraphStyle("cell", parent=P, fontSize=8.5, leading=10, alignment=0, spaceAfter=0)

REPO_URL = "https://github.com/MuhammadMuthar/sdn-reroute"


def table(rows, widths, head=True):
    data = [[Paragraph(str(c), CELL) for c in r] for r in rows]
    t = Table(data, colWidths=widths)
    st = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
          ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    if head:
        st.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ececec")))
    t.setStyle(TableStyle(st))
    return t


S = []
S.append(Paragraph("Learning the Cost of Rerouting: Progress Report 2", T))
S.append(Paragraph("<b>EE-353 Computer Networks. Work after the Stage 2 milestone: where does a "
                   "reroute really hurt?</b> (14 November 2026)", C))
S.append(table([["Name", "CMS ID", "Section"],
                ["Mutahar", "540395", "BESE-16 A"],
                ["Uswa Fatima", "575207", "BESE-16 A"]],
               [45 * mm, 30 * mm, 30 * mm]))
S.append(Paragraph("Source code and all raw results: <font color='#1a4fa0'>%s</font>, branch "
                   "<i>claude/chunk7-regime</i>" % REPO_URL, C))

S.append(Paragraph("1. Why this step", H))
S.append(Paragraph(
    "The Stage 2 report ended with an honest problem. Rerouting big flows beats ECMP by 2.59 "
    "Mbit/s per flow, but on our 10 Mbit/s network one reroute costs almost nothing, so our "
    "damage predictor had nothing to predict and our controller only matched the Hedera-style "
    "baseline. The proposal (Section V-A) had also promised a pilot under TCP Cubic and BBR, "
    "each with RACK loss detection on and off, which was still open. This step does both: it "
    "runs that pilot and searches for the conditions where a reroute really costs throughput.",
    P))

S.append(Paragraph("2. What we ran", H))
S.append(Paragraph(
    "One overnight campaign on the laptop (11 h 45 min, 13 to 14 November). We varied four "
    "things: link rate (10 and 100 Mbit/s), congestion control (Cubic and BBR), RACK (on and "
    "off) and the delay of the slowest path (10 ms as before, or 50 ms; the fastest path is "
    "1 ms). That gives 16 settings. In each setting we tested four kinds of move: to a faster "
    "path or to a slower path, and onto an empty path or onto a path that already carries "
    "another big flow. Each kind was run as 5 matched pairs: the same traffic once with the "
    "move, and once with the flow on the destination path from the start. The damage of a move "
    "is the goodput the destination path loses against that matched run, in the 10 s after the "
    "move. We also moved one flow back and forth every 0.5 s and every 2 s for 10 s, because an "
    "eager controller does that. In total 640 runs (320 matched pairs), 30 back-and-forth runs and 16 check "
    "runs. All ran without error and none had to be excluded. Every run recorded that the TCP "
    "settings were really in effect on all 8 hosts. The plan and the rule for calling something "
    "damage (confidence interval above zero and at least 1 % of the link) were written into the "
    "lab log before the first run.", P))

S.append(Paragraph("3. Results", H))
S.append(Image(os.path.join(REPO, "figures", "progress2_regime.png"), width=165 * mm,
               height=165 * mm * 3.5 / 7.4))
S.append(Paragraph(
    "Figure 1. Goodput one reroute costs the destination path, mean and 95 % confidence "
    "interval over 5 matched pairs per point. Shaded columns are the settings with the 50 ms "
    "slow path. Only one kind of move stands out: onto a slower, empty path.", CAP))
S.append(table([
    ["Move onto a slower, empty path; goodput lost in 10 s, % of link (mean ± 95 % CI, n = 5)",
     "Cubic, RACK on", "Cubic, RACK off", "BBR, RACK on", "BBR, RACK off"],
    ["100 Mbit/s, slow path 50 ms", "<b>54.9 ± 9.3</b>", "<b>48.7 ± 23.7</b>", "<b>77.1 ± 9.6</b>",
     "<b>47.6 ± 45.8</b>"],
    ["10 Mbit/s, slow path 50 ms", "<b>25.9 ± 10.8</b>", "<b>19.3 ± 13.4</b>", "<b>31.8 ± 29.2</b>",
     "<b>33.7 ± 27.5</b>"],
    ["100 Mbit/s, slow path 10 ms", "4.0 ± 5.5", "1.4 ± 1.4", "12.3 ± 20.5", "10.6 ± 24.3"],
    ["10 Mbit/s, slow path 10 ms", "0.05 ± 0.20", "0.09 ± 0.28", "1.0 ± 2.1", "2.2 ± 3.8"],
], [72 * mm, 27 * mm, 27 * mm, 27 * mm, 27 * mm]))
S.append(Paragraph("Table 1. The one kind of move that costs something. Bold cells meet the "
                   "damage rule. The other 56 of the 64 tested cases do not.", CAP))
S.append(Paragraph(
    "<b>Reordering is not the cost.</b> Moves to a faster path reorder packets heavily (up to "
    "325 reordering events and 555 retransmissions in 2 s, as averages per case), yet the path "
    "loses nothing measurable, in every setting. Switching RACK off did not change this, and "
    "BBR was not gentler than Cubic. Moving a flow back and forth every 0.5 s cost it less than "
    "1 % of its goodput (it kept 99.6 % at 100 Mbit/s and 99.2 % at 10 Mbit/s). Three things we "
    "expected to hurt therefore did not, and we report that as measured.", P))
S.append(Paragraph(
    "<b>The cost is arriving on a long, empty path with a sending window sized for the short "
    "one.</b> When a flow moves from the 1 ms path to an empty 50 ms path, its window is far too "
    "small for the new path and takes many seconds to grow (Cubic), or its delay estimate is "
    "stale (BBR). The path then carries 19 to 77 % less than it would have, for 10 s. At 100 "
    "Mbit/s with Cubic that is 68.6 MB lost by a single move. With the 10 ms slow path the same "
    "move shows a smaller effect that does not meet our rule (4.0 ± 5.5 % at 100 Mbit/s).", P))
S.append(Paragraph(
    "<b>A busy destination hides the cost.</b> Onto a path that already carries a big flow, the "
    "same move costs the path nothing (+0.10 ± 0.34 % at 100 Mbit/s, Cubic, 50 ms). The moved "
    "flow itself still loses (about 36 % of the link in that case), but the flow already there "
    "takes what it leaves.", P))
S.append(Paragraph(
    "<b>Check.</b> We recomputed the table a second way, from iperf3's own receiver reports "
    "instead of packet traces, with separate code, for the 12 of these 16 cases whose raw runs "
    "are uploaded so far. The same 6 cases show damage and the largest difference between the "
    "two methods is 2.0 percentage points.", P))

S.append(Paragraph("4. What this means for our improvement", H))
S.append(Paragraph(
    "This is the first setting where the proposal's idea has something to work with. A "
    "Hedera-style controller always picks the least-loaded path, and the least-loaded path is "
    "exactly the empty one. If that path is much longer, the move throws away up to half of "
    "the link for several seconds, and the controller never sees why. The damage also depends "
    "on things the controller already knows before it moves a flow: how much longer the new "
    "path is, whether it is empty, and how fast the flow is sending. So a cost estimate can "
    "tell a harmless move from a costly one, which was not possible on the first network where "
    "every move was harmless.", P))
S.append(Paragraph(
    "Two limits, stated plainly. First, this damage is not the reordering damage the proposal "
    "expected; it is a ramp-up cost, and the paper will say so. Second, a 1 ms against 50 ms "
    "path difference is typical of a wide-area network, not of a data-centre fabric. With "
    "data-centre-like differences (1 to 10 ms) we still find no damage.", P))

S.append(Paragraph("5. Next steps", H))
S.append(Paragraph(
    "(1) Run the five-controller comparison (ECMP, Hedera-style, cooldown-only, cost-aware with "
    "formula, cost-aware with ML) in the setting where damage exists: 100 Mbit/s, paths of very "
    "different length, traffic in which the least-loaded path is often the long empty one. Add "
    "three inputs to the cost model: the delay ratio of new to old path, the flow's window "
    "against what the new path needs, and whether the new path is empty. (2) Build the k=4 "
    "fat-tree and the AI-training traffic from the proposal. (3) Train on one network and test "
    "on another. Not started yet: the comparison in the new setting, the fat-tree, the "
    "AI-training traffic and the margin-only ablation.", P))

SimpleDocTemplate(OUT, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm,
                  bottomMargin=12 * mm, title="EE-353 Progress Report 2",
                  author="Mutahar, Uswa Fatima").build(S)
