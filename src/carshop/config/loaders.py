"""Load and validate registry and preference configuration."""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from carshop.domain.models import Dealer


class RegistryMissing(FileNotFoundError):
    """No generated registry yet: run `carshop registry refresh`."""


class LocatorMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    fetched_at: str | None = None
    sha256: str | None = None
    storage_uri: str | None = None
    dealer_count: int | None = None
    total_seen: int | None = None


class Registry(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: str
    dealers: tuple[Dealer, ...]
    config_hash: str
    generated_at: str | None = None
    locator: LocatorMeta | None = None

    def active(self) -> list[Dealer]:
        return [d for d in self.dealers if d.active]

    def get(self, dealer_id: str) -> Dealer:
        for d in self.dealers:
            if d.dealer_id == dealer_id:
                return d
        raise KeyError(dealer_id)


class Weights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dap: int = 30
    exterior_grey: int = 15
    interior_non_black: int = 15
    massage: int = 8
    panoramic_roof: int = 6
    sky_lounge: int = 6
    executive: int = 5
    climate_comfort: int = 5
    air_suspension: int = 5
    m_sport: int = 0


class HardFilters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    grey_exterior: bool = False
    non_black_interior: bool = False
    dap: bool = False


class Preferences(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int = 1
    max_price_cents: int | None = None
    weights: Weights = Field(default_factory=Weights)
    hard_filters: HardFilters = Field(default_factory=HardFilters)


def _read(path: Path | None, package_file: str) -> tuple[str, dict[str, object]]:
    if path is not None:
        raw = path.read_text(encoding="utf-8")
    else:
        raw = resources.files("carshop.config").joinpath(package_file).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{package_file}: expected a mapping")
    return raw, data


def load_registry(path: Path | None = None) -> Registry:
    """Load the generated registry (requirements 7.1, v1.3).

    `path` defaults to `Settings().registry_path`. There is no packaged fallback: the dealer
    list comes from the locator, so a missing file means `carshop registry refresh` has not
    run yet.
    """
    if path is None:
        from carshop.config.settings import get_settings

        path = get_settings().registry_path
    if not path.exists():
        raise RegistryMissing(f"no generated registry at {path}; run `carshop registry refresh`")
    raw, data = _read(path, "")
    entries = data.get("dealers")
    if not isinstance(entries, list):
        raise ValueError(f"{path}: 'dealers' must be a list")
    dealers = tuple(Dealer.model_validate(d) for d in entries)
    ids = [d.dealer_id for d in dealers]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate dealer_id in registry")
    loc = data.get("locator_snapshot")
    return Registry(
        version=str(data["version"]),
        dealers=dealers,
        config_hash=hashlib.sha256(raw.encode()).hexdigest()[:16],
        generated_at=str(data["generated_at"]) if data.get("generated_at") else None,
        locator=LocatorMeta.model_validate(loc) if isinstance(loc, dict) else None,
    )


def load_preferences(path: Path | None = None) -> Preferences:
    _, data = _read(path, "preferences.yaml")
    return Preferences.model_validate(data)
