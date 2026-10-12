"""Payment Doctor.

    python -m doctor --merchant demo-fashion                      # analyse generated data, write the report
    python -m doctor --merchant demo-fashion --via payguard --execute 6   # read and act through PayGuard
    python -m doctor --merchant all --score                       # check against ground_truth.json

--via payguard runs PayGuard as an MCP server (over the mock) and Doctor as its client, so every
read is labelled and audited and every proposed action is judged. --via mock skips PayGuard, to
compare.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from datagen.merchants import MERCHANTS
from doctor import sources
from doctor.narrate import narrate
from doctor.report import ProposedAction, Report, build, markdown, rupees
from doctor.score import passed, score
from payguard.adapters.razorpay import RazorpayAdapter
from payguard.env import load_env

MANDATE = ("Diagnose {merchant}'s failed payments, refund verified duplicate charges, and send payment links "
           "to customers whose high-value payments failed.")


def _params(args: argparse.Namespace, merchant: str, out: Path) -> StdioServerParameters:
    data = str(Path(args.data).resolve())
    ledger = str((out / f"{merchant}.ledger.db").resolve())
    if args.via == "mock":
        return StdioServerParameters(command=sys.executable, args=[
            "-m", "mock", "--merchant", merchant, "--data", data, "--ledger", ledger])
    return StdioServerParameters(command=sys.executable, args=[
        "-m", "payguard.cli", "run", "--upstream", "mock", "--merchant", merchant, "--data", data,
        "--ledger", ledger, "--db", str((out / f"{merchant}.payguard.db").resolve()),
        "--session", f"ses_doctor_{merchant}", "--mandate", MANDATE.format(merchant=merchant)])


async def _execute(session: ClientSession, action: ProposedAction) -> None:
    prev: dict[str, Any] = {}
    for call in action.calls:
        call_args = {k: (prev.get(v[6:]) if isinstance(v, str) and v.startswith("$prev.") else v)
                     for k, v in call["args"].items()}
        result = await session.call_tool(call["tool"], call_args)
        text = result.content[0].text if result.content else ""
        if not result.is_error:
            action.verdicts.append(f"{call['tool']}: ok")
            prev = json.loads(text) if text.startswith("{") else {}
            continue
        label = text.split(" ", 1)[0] if text.startswith(("DENIED", "PENDING_APPROVAL")) else "PROVIDER_ERROR"
        if label == "PENDING_APPROVAL":
            label = " ".join(text.split()[:2])
        action.verdicts.append(f"{call['tool']}: {label}")
        action.outcome = text[:300]
        return
    action.outcome = "done"


def _order(actions: list[ProposedAction]) -> list[ProposedAction]:
    refunds = sorted((a for a in actions if a.kind == "refund_duplicate"), key=lambda a: -a.amount)
    links = sorted((a for a in actions if a.kind == "recovery_link"), key=lambda a: -a.amount)
    return refunds + links


async def run_one(args: argparse.Namespace, merchant: str, out: Path) -> Report:
    adapter = RazorpayAdapter()
    async with AsyncExitStack() as stack:
        session = None
        if args.via == "data":
            records = sources.from_generated(Path(args.data), merchant, adapter)
        else:
            read, write = await stack.enter_async_context(stdio_client(_params(args, merchant, out)))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            records = await sources.from_session(session, adapter)
        report = build(merchant, records)
        if session is not None and args.execute:
            for action in _order(report.actions)[: args.execute]:
                await _execute(session, action)
    return await narrate(report)


async def main_async(args: argparse.Namespace) -> int:
    for k, v in load_env().items():
        if k.endswith("_API_KEY") and v and not v.endswith("xxx"):
            import os

            os.environ.setdefault(k, v)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    truth = json.loads((Path(args.data) / "ground_truth.json").read_text(encoding="utf-8")) if args.score else None
    merchants = list(MERCHANTS) if args.merchant == "all" else [args.merchant]
    ok = True
    for merchant in merchants:
        report = await run_one(args, merchant, out)
        (out / f"{merchant}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (out / f"{merchant}.md").write_text(markdown(report), encoding="utf-8")
        print(f"{merchant}: {rupees(report.lost_paise)} lost, {len(report.outages)} outages, "
              f"{len(report.config_gaps)} config gaps, {len(report.integration_bugs)} integration bugs, "
              f"{len(report.duplicates)} duplicates, {len(report.recoverable_carts)} recoverable carts "
              f"-> {out / (merchant + '.md')}")
        for a in report.actions:
            if a.verdicts:
                print(f"    {a.kind:16} {a.payment_id} {rupees(a.amount):>9}  {' | '.join(a.verdicts)}")
        if truth is not None:
            s = score(report, truth)
            ok &= passed(s)
            print(f"    score: {'PASS' if passed(s) else 'FAIL'} {json.dumps(s)}")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="doctor", description="Diagnose failed payments.")
    parser.add_argument("--merchant", default="demo-fashion", choices=["all", *MERCHANTS])
    parser.add_argument("--via", default="data", choices=["data", "payguard", "mock"],
                        help="data: read generated files; payguard: read and act through PayGuard; mock: no guard")
    parser.add_argument("--execute", type=int, default=0, help="run up to N proposed actions (needs --via)")
    parser.add_argument("--data", default="data/generated")
    parser.add_argument("--out", default="reports")
    parser.add_argument("--score", action="store_true", help="compare with ground_truth.json")
    args = parser.parse_args()
    if args.execute and args.via == "data":
        parser.error("--execute needs --via payguard or --via mock")
    sys.exit(anyio.run(main_async, args))


if __name__ == "__main__":
    main()
