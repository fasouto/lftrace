from pathlib import Path

import pytest

from lftrace.fetch import load_file
from lftrace.tree import Observation, build_tree

FIXTURE = Path(__file__).parent / "fixtures" / "trace.json"


@pytest.fixture
def observations() -> list[Observation]:
    return [Observation.from_dict(o) for o in load_file(FIXTURE)]


@pytest.fixture
def roots(observations: list[Observation]) -> list[Observation]:
    return build_tree(observations)


def names(observations: list[Observation]) -> list[str | None]:
    return [o.name for o in observations]
