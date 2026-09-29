"""Lightweight console dispatch for commands with strict startup budgets."""

from __future__ import annotations

import argparse
import sys


def _run_client(argv: list[str]) -> int:
    from hive._client import run_client
    from hive._endpoint import DEFAULT_HOST

    parser = argparse.ArgumentParser(prog="hive client")
    parser.add_argument("--host", default=DEFAULT_HOST)
    options = parser.parse_args(argv)
    return run_client(host=options.host)


def main() -> None:
    """Dispatch `hive client` without importing the full server graph."""
    argv = sys.argv[1:]
    if argv and argv[0] == "client":
        raise SystemExit(_run_client(argv[1:]))
    from hive.server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
