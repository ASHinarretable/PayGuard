"""payguard command line.

    payguard run --adapter razorpay --upstream mock --merchant demo-fashion
    payguard run --adapter razorpay --upstream docker          # real Razorpay, test keys from .env
    payguard audit --db payguard.db                            # print the audit log
    payguard approvals list | approve apr_x | reject apr_x     # decide escalated calls
    payguard dashboard --db payguard.db                        # http://127.0.0.1:8787

stdout carries the MCP protocol in `run`, so everything human-readable goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

import anyio
from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment

from payguard.adapters import ADAPTERS
from payguard.core.approvals import ApprovalQueue
from payguard.core.audit import AuditLog
from payguard.core.policy import Policy
from payguard.env import load_env, read_env_value

DOCKER_IMAGE = "razorpay-mcp:main"


PROVIDER_ENV = ".env.razorpay"  # RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET only; handed to Docker, never read by PayGuard


def _provider_key_id(args: argparse.Namespace, env: dict[str, str]) -> str | None:
    """PayGuard needs only the key id (its rzp_live_/rzp_test_ prefix decides R10), never the secret."""
    path = Path(args.provider_env)
    if path.exists():
        return read_env_value(path, "RAZORPAY_KEY_ID")
    return env.get("RAZORPAY_KEY_ID")


def _upstream(args: argparse.Namespace, env: dict[str, str]) -> StdioServerParameters:
    if args.upstream == "mock":
        cmd = [sys.executable, "-m", "mock", "--merchant", args.merchant, "--data", str(Path(args.data).resolve())]
        if args.ledger:
            cmd += ["--ledger", str(Path(args.ledger).resolve())]
        return StdioServerParameters(command=cmd[0], args=cmd[1:], cwd=str(Path.cwd()))
    if args.upstream == "docker":
        # The Docker client gets a minimal environment, never PayGuard's own (which may hold model keys).
        base_env = get_default_environment()
        path = Path(args.provider_env)
        if path.exists():
            # Docker reads the secret straight from the file; it never enters PayGuard's process.
            return StdioServerParameters(command="docker", env=base_env,
                                         args=["run", "--rm", "-i", "--env-file", str(path.resolve()), args.image])
        print(f"payguard: {args.provider_env} not found; passing Razorpay keys from .env. Put them in "
              f"{args.provider_env} so PayGuard never holds the secret.", file=sys.stderr)
        keys = {k: env.get(k, "") for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")}
        return StdioServerParameters(
            command="docker", env={**base_env, **keys},
            args=["run", "--rm", "-i", "-e", "RAZORPAY_KEY_ID", "-e", "RAZORPAY_KEY_SECRET", args.image],
        )
    command, *rest = args.upstream.split()
    return StdioServerParameters(command=command, args=rest)


def cmd_run(args: argparse.Namespace) -> None:
    env = load_env()
    policy = Policy.load(args.policy)
    if policy.adapter != args.adapter:
        sys.exit(f"policy {args.policy} is for adapter {policy.adapter!r}, not {args.adapter!r}")
    session_id = args.session or f"ses_{secrets.token_hex(4)}"
    from payguard.intent_llm import checker_from_env
    from payguard.interfaces.mcp_server import run_stdio_proxy

    intent = None if args.no_intent else checker_from_env(env)
    print(f"payguard: adapter={args.adapter} upstream={args.upstream} session={session_id} db={args.db} "
          f"mandate={'yes' if args.mandate else 'none'} intent={intent.model if intent else 'off'}",
          file=sys.stderr)
    anyio.run(lambda: run_stdio_proxy(
        upstream=_upstream(args, env), adapter=args.adapter, policy=policy, db_url=f"sqlite:///{args.db}",
        session_id=session_id, key_id=_provider_key_id(args, env) if args.upstream == "docker" else None,
        mandate=args.mandate, intent=intent,
    ))


def cmd_audit(args: argparse.Namespace) -> None:
    for e in AuditLog(f"sqlite:///{args.db}").events(args.session)[-args.limit:]:
        amount = f" {e.currency} {e.amount_minor / 100:,.2f}" if e.amount_minor is not None else ""
        rules = ",".join(json.loads(e.rules_fired))
        print(f"{e.ts:%Y-%m-%d %H:%M:%S} {e.session_id} {e.verdict:8} {e.tool}{amount}"
              f"{f' [{rules}]' if rules else ''}{f'  {e.reason}' if e.reason else ''}")


def cmd_approvals(args: argparse.Namespace) -> None:
    queue = ApprovalQueue(AuditLog(f"sqlite:///{args.db}"))
    now = datetime.now(UTC)
    if args.action == "list":
        queue.expire_due(now)
        rows = queue.list(None if args.all else "pending")
        if not rows:
            print("no pending approvals" if not args.all else "no approvals")
        for a in rows:
            print(f"{a.id}  {a.status:20} {a.session_id}  {a.tool} {a.args_json}")
            print(f"    rules: {', '.join(json.loads(a.rules_fired))}  |  {a.reason}")
            if a.intent_check:
                print(f"    intent: {a.intent_check}")
            if a.state_json:
                print(f"    state when escalated: {a.state_json}")
            if a.result_text:
                print(f"    result: {a.result_text[:200]}")
        return
    if not args.id:
        sys.exit(f"payguard approvals {args.action} needs an approval id")
    try:
        a = queue.decide(args.id, approve=args.action == "approve", by=args.by, now=now)
    except (KeyError, ValueError) as exc:
        sys.exit(str(exc))
    note = " (the running PayGuard will re-check and execute it within a second)" if a.status == "approved" else ""
    print(f"{a.id}: {a.status}{note}")


def cmd_resume(args: argparse.Namespace) -> None:
    AuditLog(f"sqlite:///{args.db}").resume(args.session)
    print(f"{args.session}: resumed")


def cmd_dashboard(args: argparse.Namespace) -> None:
    import uvicorn

    from payguard.interfaces.api import create_app

    # The eval summary and Doctor code live in the repo (evals/, doctor/), not the installed package;
    # the console script doesn't put the working directory on the path, so add it.
    sys.path.insert(0, str(Path.cwd()))

    app = create_app(f"sqlite:///{args.db}", Path(args.reports), Path(args.results), Path(args.static))
    print(f"payguard dashboard: http://{args.host}:{args.port}/  (audit db {args.db})", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


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
    run.add_argument("--provider-env", default=PROVIDER_ENV,
                     help="docker upstream only: env file with the provider's keys, read by Docker, not PayGuard")
    run.add_argument("--policy", default="policies/default.yaml")
    run.add_argument("--db", default="payguard.db")
    run.add_argument("--session", help="session id (default: random per process)")
    run.add_argument("--mandate", help="the human's task for this session, for R8 and the intent check")
    run.add_argument("--no-intent", action="store_true", help="skip the LLM intent check")
    run.set_defaults(func=cmd_run)

    approvals = sub.add_parser("approvals", help="list, approve or reject escalated calls")
    approvals.add_argument("action", choices=["list", "approve", "reject"])
    approvals.add_argument("id", nargs="?")
    approvals.add_argument("--all", action="store_true", help="list: include decided approvals")
    approvals.add_argument("--by", default=os.environ.get("USERNAME") or os.environ.get("USER") or "cli")
    approvals.add_argument("--db", default="payguard.db")
    approvals.set_defaults(func=cmd_approvals)

    dash = sub.add_parser("dashboard", help="serve the dashboard and its API")
    dash.add_argument("--db", default="payguard.db")
    dash.add_argument("--reports", default="reports")
    dash.add_argument("--results", default="evals/results")
    dash.add_argument("--static", default="web/out", help="the built dashboard (cd web && npm run build)")
    dash.add_argument("--host", default="127.0.0.1")
    dash.add_argument("--port", type=int, default=8787)
    dash.set_defaults(func=cmd_dashboard)

    resume = sub.add_parser("resume", help="resume a session the circuit breaker paused")
    resume.add_argument("session")
    resume.add_argument("--db", default="payguard.db")
    resume.set_defaults(func=cmd_resume)

    audit = sub.add_parser("audit", help="print the audit log")
    audit.add_argument("--db", default="payguard.db")
    audit.add_argument("--session")
    audit.add_argument("--limit", type=int, default=50)
    audit.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
