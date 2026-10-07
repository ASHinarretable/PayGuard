"""payguard command line.

    payguard run --adapter razorpay --upstream mock --merchant demo-fashion
    payguard run --adapter razorpay --upstream docker          # real Razorpay, test keys from .env
    payguard audit --db payguard.db                            # print the audit log

stdout carries the MCP protocol in `run`, so everything human-readable goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

import anyio
from mcp import StdioServerParameters

from payguard.adapters import ADAPTERS
from payguard.core.audit import AuditLog
from payguard.core.policy import Policy
from payguard.env import load_env

DOCKER_IMAGE = "razorpay-mcp:main"


def _upstream(args: argparse.Namespace, env: dict[str, str]) -> StdioServerParameters:
    if args.upstream == "mock":
        cmd = [sys.executable, "-m", "mock", "--merchant", args.merchant, "--data", str(Path(args.data).resolve())]
        if args.ledger:
            cmd += ["--ledger", str(Path(args.ledger).resolve())]
        return StdioServerParameters(command=cmd[0], args=cmd[1:], cwd=str(Path.cwd()))
    if args.upstream == "docker":
        keys = {k: env.get(k, "") for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")}
        return StdioServerParameters(
            command="docker",
            args=["run", "--rm", "-i", "-e", "RAZORPAY_KEY_ID", "-e", "RAZORPAY_KEY_SECRET", args.image],
            env={**os.environ, **keys},
        )
    command, *rest = args.upstream.split()
    return StdioServerParameters(command=command, args=rest)


def cmd_run(args: argparse.Namespace) -> None:
    env = load_env()
    policy = Policy.load(args.policy)
    if policy.adapter != args.adapter:
        sys.exit(f"policy {args.policy} is for adapter {policy.adapter!r}, not {args.adapter!r}")
    session_id = args.session or f"ses_{secrets.token_hex(4)}"
    print(f"payguard: adapter={args.adapter} upstream={args.upstream} session={session_id} db={args.db}",
          file=sys.stderr)
    from payguard.interfaces.mcp_server import run_stdio_proxy

    anyio.run(lambda: run_stdio_proxy(
        upstream=_upstream(args, env), adapter=args.adapter, policy=policy, db_url=f"sqlite:///{args.db}",
        session_id=session_id, key_id=env.get("RAZORPAY_KEY_ID") if args.upstream == "docker" else None,
    ))


def cmd_audit(args: argparse.Namespace) -> None:
    for e in AuditLog(f"sqlite:///{args.db}").events(args.session)[-args.limit:]:
        amount = f" {e.currency} {e.amount_minor / 100:,.2f}" if e.amount_minor is not None else ""
        rules = ",".join(json.loads(e.rules_fired))
        print(f"{e.ts:%Y-%m-%d %H:%M:%S} {e.session_id} {e.verdict:8} {e.tool}{amount}"
              f"{f' [{rules}]' if rules else ''}{f'  {e.reason}' if e.reason else ''}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="payguard")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run PayGuard as an MCP server over stdio")
    run.add_argument("--adapter", default="razorpay", choices=sorted(ADAPTERS))
    run.add_argument("--upstream", default="mock",
                     help="'mock', 'docker' (real provider MCP server) or a full command line")
    run.add_argument("--merchant", default="demo-fashion", help="mock upstream only")
    run.add_argument("--data", default="data/generated", help="mock upstream only: generated data directory")
    run.add_argument("--ledger", help="mock upstream only: SQLite path for the mock's write ledger")
    run.add_argument("--image", default=DOCKER_IMAGE, help="docker upstream only")
    run.add_argument("--policy", default="policies/default.yaml")
    run.add_argument("--db", default="payguard.db")
    run.add_argument("--session", help="session id (default: random per process)")
    run.set_defaults(func=cmd_run)

    audit = sub.add_parser("audit", help="print the audit log")
    audit.add_argument("--db", default="payguard.db")
    audit.add_argument("--session")
    audit.add_argument("--limit", type=int, default=50)
    audit.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
