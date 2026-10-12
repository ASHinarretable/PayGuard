"""Evals.

    python -m evals run --model gemini-flash                 # the full matrix for one model (resumable)
    python -m evals run --model gpt-oss-120b --only A-07,F-01 --setups payguard
    python -m evals report                                   # results table -> evals/results/RESULTS.md
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anyio
import yaml

from evals import report
from evals.agent import LLM
from evals.harness import SETUPS, RunConfig, run_matrix
from evals.scenario import load
from payguard.core.policy import Policy
from payguard.env import load_env

ROOT = Path("evals")
SCENARIOS = ROOT / "scenarios" / "razorpay"
RESULTS = ROOT / "results"


def _keys() -> None:
    for k, v in load_env().items():
        if k.endswith("_API_KEY") and v and not v.endswith("xxx"):
            os.environ.setdefault(k, v)


async def cmd_run(args: argparse.Namespace) -> int:
    _keys()
    cfg_file = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    m = cfg_file["models"][args.model]
    scenarios = load(SCENARIOS)
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s.id in wanted]
    elif m["scenarios"] == "subset":
        scenarios = [s for s in scenarios if s.id in set(cfg_file["subset"])]
    setups = args.setups.split(",") if args.setups else m["setups"]
    unknown = set(setups) - set(SETUPS)
    if unknown:
        sys.exit(f"unknown setups {unknown}")
    cfg = RunConfig(data_dir=Path(args.data), policy=Policy.load(args.policy),
                    intent_model=None if args.no_intent else cfg_file["intent_model"],
                    max_turns=cfg_file.get("max_turns", 10))
    llm = LLM(m["id"], ROOT / "cache" / args.model, rpm=m["rpm"])
    out = Path(args.out) if args.out else RESULTS / f"{args.model}.jsonl"
    await run_matrix(cfg, llm, scenarios, setups, args.trials or m["trials"], out)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    md = report.render(Path(args.results), len(load(SCENARIOS)))
    out = Path(args.results) / "RESULTS.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--model", required=True, help="a key under models: in evals/config.yaml")
    run.add_argument("--only", help="comma-separated scenario ids")
    run.add_argument("--setups", help=f"comma-separated, from {', '.join(SETUPS)}")
    run.add_argument("--trials", type=int)
    run.add_argument("--no-intent", action="store_true", help="PayGuard without the LLM intent check")
    run.add_argument("--data", default="data/generated")
    run.add_argument("--policy", default="policies/default.yaml")
    run.add_argument("--out", help="results file (default evals/results/<model>.jsonl)")
    rep = sub.add_parser("report")
    rep.add_argument("--results", default=str(RESULTS))
    args = parser.parse_args()
    if args.cmd == "run":
        sys.exit(anyio.run(cmd_run, args))
    sys.exit(cmd_report(args))


if __name__ == "__main__":
    main()
