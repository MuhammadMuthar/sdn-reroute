#!/usr/bin/env python3
"""Launcher for os-ken controller apps (replaces the osken-manager CLI).

os-ken 4.x (the versions that support Python 3.12/3.13) removed the
`osken-manager` console script and the `os_ken.cmd` package, keeping only
the library. This file is the ~30 lines of that CLI we need, adapted from
os_ken/cmd/manager.py in os-ken 3.1.1 (Apache-2.0, NTT / Isaku Yamahata).

Usage (from the repo root, inside the venv):
    python controllers/run.py controllers/learning_switch.py
    python controllers/run.py --ofp-tcp-listen-port 6653 controllers/ecmp.py
Any osken-manager / ryu-manager flag works (--observe-links, --verbose, ...).
"""

import logging
import sys

from os_ken.lib import hub

hub.patch(thread=False)

from os_ken import cfg, log  # noqa: E402
from os_ken import __version__ as version  # noqa: E402

log.early_init_log(logging.DEBUG)

from os_ken import flags  # noqa: E402,F401  (registers controller CLI opts)
from os_ken.base.app_manager import AppManager  # noqa: E402
from os_ken.controller import controller  # noqa: E402,F401
from os_ken.topology import switches  # noqa: E402,F401

CONF = cfg.CONF
CONF.register_cli_opts([
    cfg.MultiStrOpt("app", positional=True, default=[],
                    help="application module name or .py file to run"),
    cfg.StrOpt("pid-file", default=None, help="pid file name"),
])


def main(args=None):
    CONF(args=args, prog="osken-run", project="os_ken",
         version="osken-run (os-ken %s)" % version)
    log.init_log()
    logger = logging.getLogger("osken-run")
    hub.patch(thread=True)

    if CONF.pid_file:
        with open(CONF.pid_file, "w") as f:
            import os
            f.write(str(os.getpid()))

    app_lists = CONF.app or ["os_ken.controller.ofp_handler"]
    app_mgr = AppManager.get_instance()
    app_mgr.load_apps(app_lists)
    contexts = app_mgr.create_contexts()
    services = app_mgr.instantiate_apps(**contexts)
    logger.info("os-ken %s listening on %s:%d, apps=%s", version,
                CONF.ofp_listen_host or "0.0.0.0", CONF.ofp_tcp_listen_port,
                app_lists)
    try:
        hub.joinall(services)
    except KeyboardInterrupt:
        pass
    finally:
        app_mgr.close()


if __name__ == "__main__":
    main(sys.argv[1:])
