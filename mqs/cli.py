import argparse
import sys


def main():
    # Imports are lazy: `install` must run on a bare python, before the deps exist.
    if len(sys.argv) == 3 and sys.argv[2].startswith("mqs://"):
        from . import helper
        return helper.main()  # git runs the bundle as git-remote-mqs <remote> <url>
    parser = argparse.ArgumentParser(prog="mqs")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="set up this machine: deps, key, git config, PATH")
    sub.add_parser("uninstall", help="undo install: remove git config, PATH entry, .venv/.env, mirrors")
    sub.add_parser("keygen", help="print a new key for a local <-> gateway pair")
    srv = sub.add_parser("serve", help="run the gateway on the machine that reaches the git server")
    srv.add_argument("--idle", type=float, default=2, metavar="MIN",
                     help="close the connection after MIN idle minutes, 0 = never (default 2)")
    args = parser.parse_args()
    if args.cmd in ("install", "uninstall"):
        from . import install
        (install.main if args.cmd == "install" else install.uninstall)()
    elif args.cmd == "keygen":
        from . import crypto
        print(crypto.keygen())
    else:
        from .serve import serve
        serve(args.idle)
