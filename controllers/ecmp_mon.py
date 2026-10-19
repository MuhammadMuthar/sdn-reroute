"""ECMP plus the Hedera-style controller's monitoring, never moving a flow
(Chunk 4). Same select-group forwarding as controllers/ecmp.py; adds the SYN
copy to the controller, the per-flow counting rules and the 1 s stats polling
of controllers/hedera.py. Run next to plain ECMP, it shows whether the
monitoring itself changes anything the comparison measures.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers.hedera import HederaController  # noqa: E402


class ECMPMonController(HederaController):
    APP_NAME = "ecmp_mon"
    POLICY = "none"
