import hashlib
import json

import pytest

from datagen.attacks import plant_attacks
from datagen.generate import BANK_OUTAGE, PSP_OUTAGE, generate_merchant
from datagen.merchants import MERCHANTS
from datagen.truth import build_ground_truth

SEED, DAYS = 42, 30


@pytest.fixture(scope="module")
def dataset():
    merchants = [generate_merchant(p, DAYS, SEED) for p in MERCHANTS.values()]
    attacks = plant_attacks(merchants, SEED)
    return merchants, attacks, build_ground_truth(merchants, SEED, DAYS)


def _digest(merchant) -> str:
    return hashlib.sha256("".join(p.model_dump_json() for p in merchant.payments).encode()).hexdigest()


def test_same_seed_same_data():
    first = generate_merchant(MERCHANTS["demo-learn"], DAYS, SEED)
    second = generate_merchant(MERCHANTS["demo-learn"], DAYS, SEED)
    assert _digest(first) == _digest(second)


def test_volumes_near_targets(dataset):
    merchants, _, _ = dataset
    for m in merchants:
        assert 0.85 * m.profile.attempts_30d < len(m.payments) < 1.2 * m.profile.attempts_30d


def test_ids_unique_and_prefixed(dataset):
    merchants, _, _ = dataset
    ids = [p.id for m in merchants for p in m.payments]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("pay_") and len(i) == 18 for i in ids)


@pytest.mark.parametrize("outage", [BANK_OUTAGE, PSP_OUTAGE])
def test_outage_failures_only_inside_window(dataset, outage):
    merchants, _, _ = dataset
    lo, hi = outage["window"].bounds()
    hits = [p for m in merchants for p in m.payments if p.failure and p.failure.reason == outage["reason"]]
    assert len(hits) >= 15
    assert all(lo <= p.created_at < hi for p in hits)


def test_planted_bugs_present(dataset):
    _, _, truth = dataset
    assert truth["config_gaps"][0]["failed_attempts"] > 30
    assert truth["integration_bugs"][0]["failed_attempts"] > 100
    assert sum(d["planted"] for d in truth["duplicates"]) == 40


def test_attacks(dataset):
    merchants, attacks, _ = dataset
    assert len(attacks) == 60
    by_id = {p.id: p for m in merchants for p in m.payments}
    for a in attacks:
        p = by_id[a["payment_id"]]
        field = a["field"]
        value = p.notes[field.split(".", 1)[1]] if field.startswith("notes.") else getattr(p, field)
        assert value == a["payload"]


def test_attacks_only_point_at_their_own_merchant(dataset):
    merchants, attacks, _ = dataset
    owner = {p.id: m.profile.name for m in merchants for p in m.payments}
    for a in attacks:
        target = a["goal"].get("payment_id")
        assert target is None or owner[target] == a["merchant"], a["id"]


def test_ground_truth_is_json(dataset):
    _, _, truth = dataset
    json.dumps(truth)
