"""Cooldown-only ablation (Chunk 4; CHUNK0_REVIEW 1.7): the Hedera-style
controller (controllers/hedera.py) plus a per-flow cooldown and a fixed
hysteresis margin, no damage estimate. A flow moves only if the load it
would see drops by more than MARGIN_MBPS (1 Mbit/s, 10 % of the link rate)
and it was not moved in the last COOLDOWN_S (5 s); a flow never moved is
eligible at once. Everything else (monitoring, elephant threshold, 1 s
polling, least-loaded target, logging) is the Hedera-style controller's.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers.hedera import HederaController  # noqa: E402


class CooldownController(HederaController):
    APP_NAME = "cooldown"
    POLICY = "cooldown"
