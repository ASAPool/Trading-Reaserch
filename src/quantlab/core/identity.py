"""Deterministic strategy identity (Phase 6).

Every backtest result records WHICH rules produced it: the strategy name, an
explicit version string and every setting. The identity is built from those
values only (never from a class name or a memory address), and its canonical
JSON text and SHA-256 are the same on every run and every computer.
"""

import hashlib
import json
import math
from dataclasses import dataclass


def _check_setting(key, value):
    if not isinstance(key, str) or not key:
        raise ValueError(f"setting names must be non-empty strings, got {key!r}")
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise ValueError(f"setting {key!r} must be a string, integer, finite number, "
                     f"boolean or None, got {value!r}")


@dataclass(frozen=True)
class StrategyIdentity:
    name: str
    version: str
    settings: tuple          # sorted ((name, value), ...): hashable and order-independent
    purpose: str = ""

    def __init__(self, name: str, version: str, settings: dict, purpose: str = ""):
        for label, v in (("name", name), ("version", version)):
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"strategy {label} must be a non-empty string")
        if not isinstance(settings, dict):
            raise ValueError("settings must be a dict")
        for k, v in settings.items():
            _check_setting(k, v)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "settings", tuple(sorted(settings.items())))
        object.__setattr__(self, "purpose", purpose)

    def canonical_json(self) -> str:
        return json.dumps({"name": self.name, "version": self.version,
                           "settings": dict(self.settings), "purpose": self.purpose},
                          sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "settings": dict(self.settings),
                "purpose": self.purpose, "sha256": self.sha256}
