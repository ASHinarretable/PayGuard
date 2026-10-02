"""python -m datagen --merchant all --days 30 --seed 42"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from datagen.attacks import plant_attacks
from datagen.generate import MerchantData, generate_merchant
from datagen.merchants import MERCHANTS
from datagen.truth import build_ground_truth


def _write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(row.model_dump_json() + "\n")


def _write(out: Path, merchants: list[MerchantData], attacks: list[dict], truth: dict) -> None:
    for m in merchants:
        d = out / m.profile.name
        d.mkdir(parents=True, exist_ok=True)
        _write_jsonl(d / "payments.jsonl", m.payments)
        _write_jsonl(d / "refunds.jsonl", m.refunds)
        _write_jsonl(d / "settlements.jsonl", m.settlements)
        _write_jsonl(d / "customers.jsonl", m.customers)
    (out / "attacks.json").write_text(json.dumps(attacks, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "ground_truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(prog="datagen", description="Generate synthetic merchant payment data.")
    parser.add_argument("--merchant", default="all", choices=["all", *MERCHANTS])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args()

    names = list(MERCHANTS) if args.merchant == "all" else [args.merchant]
    merchants = [generate_merchant(MERCHANTS[n], args.days, args.seed) for n in names]
    attacks = plant_attacks(merchants, args.seed)
    truth = build_ground_truth(merchants, args.seed, args.days)
    _write(args.out, merchants, attacks, truth)

    for name, s in truth["summary"].items():
        print(f"{name:13} attempts={s['attempts']:6} failed={s['failed']:5} "
              f"lost=₹{s['lost_paise_total'] // 100:,}")
    for o in truth["outages"]:
        print(f"outage {o['type']:4} {o['start'][:16]} failed={o['failed_attempts']}")
    print(f"duplicates={len(truth['duplicates'])} "
          f"(planted {sum(d['planted'] for d in truth['duplicates'])}) "
          f"recoverable_carts={len(truth['recoverable_carts'])} attacks={len(attacks)}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
