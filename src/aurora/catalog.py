"""Read-only access to the condominium files in dados/."""

from __future__ import annotations

import json
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Mapping

from aurora.config import DATA_DIR


def load_json(file_name: str) -> list[dict[str, Any]]:
    with (DATA_DIR / file_name).open(encoding="utf-8") as data_file:
        return json.load(data_file)


@lru_cache(maxsize=1)
def area_fees() -> Mapping[str, float]:
    """Returns the immutable fee of each exact area id."""
    return MappingProxyType(
        {area["id"]: float(area["taxa"]) for area in load_json("areas.json")}
    )


@lru_cache(maxsize=1)
def area_names() -> Mapping[str, str]:
    return MappingProxyType({area["id"]: area["nome"] for area in load_json("areas.json")})


@lru_cache(maxsize=1)
def apartment_numbers() -> frozenset[str]:
    return frozenset(apartment["numero"] for apartment in load_json("apartamentos.json"))
