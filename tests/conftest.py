import pytest

from datagen.__main__ import _write
from datagen.attacks import plant_attacks
from datagen.generate import generate_merchant
from datagen.merchants import MERCHANTS
from datagen.truth import build_ground_truth

MERCHANT = "demo-learn"  # smallest merchant keeps the tests fast


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("generated")
    merchants = [generate_merchant(MERCHANTS[MERCHANT], 30, 42)]
    _write(out, merchants, plant_attacks(merchants, 42), build_ground_truth(merchants, 42, 30))
    return out
