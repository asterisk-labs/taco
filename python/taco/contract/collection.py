from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from ..errors import CollectionError
from .contract import Contract

__all__ = ["KNOWN_TASKS", "SEMVER", "Collection", "Curator", "Extent", "Provider"]

SEMVER = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

KNOWN_TASKS = frozenset(
    {
        "regression",
        "classification",
        "scene-classification",
        "detection",
        "object-detection",
        "segmentation",
        "semantic-segmentation",
        "instance-segmentation",
        "panoptic-segmentation",
        "change-detection",
        "change_detection",
        "similarity-search",
        "generative",
        "image-captioning",
        "super-resolution",
        "denoising",
        "inpainting",
        "colorization",
        "style-transfer",
        "deblurring",
        "dehazing",
        "foundation-model",
        "other",
    }
)

_CORE_KEYS = frozenset(
    {
        "id",
        "dataset_version",
        "description",
        "licenses",
        "providers",
        "tasks",
        "title",
        "curators",
        "keywords",
        "extent",
        "taco:structure",
        "taco:metadata",
        "taco:sources",
    }
)


def _string_list(values: Any, *, name: str, required: bool) -> tuple[str, ...]:
    if values is None:
        if required:
            raise CollectionError(f"{name} is required")
        return ()
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise CollectionError(f"{name} must be a list of strings")
    result = tuple(values)
    if required and not result:
        raise CollectionError(f"{name} must not be empty")
    for item in result:
        if not isinstance(item, str) or not item.strip():
            raise CollectionError(f"{name} entries must be non-empty strings, got {item!r}")
    return result


def _parse_iso(value: Any, *, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise CollectionError(f"{name} must be an ISO 8601 datetime, got {value!r}") from exc
    else:
        raise CollectionError(f"{name} must be an ISO 8601 string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    text = value.isoformat()
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text


@dataclass(frozen=True)
class Provider:
    """STAC-style provider entry."""

    name: str
    roles: tuple[str, ...] | None = None
    url: str | None = None
    links: tuple[dict[str, Any], ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise CollectionError("provider name is required")
        if self.roles is not None:
            object.__setattr__(self, "roles", _string_list(self.roles, name="provider roles", required=False))
        if self.url is not None and not self.url.startswith(("http://", "https://")):
            raise CollectionError(f"provider url must start with http(s)://, got {self.url!r}")
        if self.links is not None:
            object.__setattr__(self, "links", tuple(dict(link) for link in self.links))

    @classmethod
    def from_any(cls, value: Any) -> Provider:
        if isinstance(value, Provider):
            return value
        if isinstance(value, str):
            return cls(name=value)
        if isinstance(value, Mapping):
            known = {key: value[key] for key in ("name", "roles", "url", "links") if key in value}
            if "name" not in known:
                raise CollectionError("provider entries need a name")
            return cls(**known)
        raise CollectionError(f"invalid provider {value!r}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name}
        if self.roles is not None:
            data["roles"] = list(self.roles)
        if self.url is not None:
            data["url"] = self.url
        if self.links is not None:
            data["links"] = [dict(link) for link in self.links]
        return data


@dataclass(frozen=True)
class Curator:
    """Person or organization maintaining the dataset."""

    name: str | None = None
    organization: str | None = None
    email: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        if not self.name and not self.organization:
            raise CollectionError("a curator needs a name or an organization")
        if self.email is not None and "@" not in self.email:
            raise CollectionError(f"invalid curator email {self.email!r}")

    @classmethod
    def from_any(cls, value: Any) -> Curator:
        if isinstance(value, Curator):
            return value
        if isinstance(value, str):
            return cls(name=value)
        if isinstance(value, Mapping):
            known = {key: value[key] for key in ("name", "organization", "email", "role") if key in value}
            return cls(**known)
        raise CollectionError(f"invalid curator {value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in vars(self).items() if value is not None}


@dataclass(frozen=True)
class Extent:
    """Spatial ``[west, south, east, north]`` (EPSG:4326) and temporal bounds.

    ``west > east`` denotes an antimeridian crossing (STAC convention).
    ``temporal`` is ``None`` for atemporal datasets or ``(start, end)`` ISO
    8601 strings in UTC.
    """

    spatial: tuple[float, float, float, float]
    temporal: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        spatial = self.spatial
        if isinstance(spatial, str) or not isinstance(spatial, Sequence) or len(spatial) != 4:
            raise CollectionError("extent.spatial must be [west, south, east, north]")
        try:
            west, south, east, north = (float(value) for value in spatial)
        except (TypeError, ValueError) as exc:
            raise CollectionError("extent.spatial values must be numbers") from exc
        if not (-180 <= west <= 180 and -180 <= east <= 180):
            raise CollectionError("extent longitudes must be within [-180, 180]")
        if not (-90 <= south <= 90 and -90 <= north <= 90):
            raise CollectionError("extent latitudes must be within [-90, 90]")
        if south > north:
            raise CollectionError("extent south must not exceed north")
        object.__setattr__(self, "spatial", (west, south, east, north))

        temporal = self.temporal
        if temporal is not None:
            if isinstance(temporal, str) or not isinstance(temporal, Sequence) or len(temporal) != 2:
                raise CollectionError("extent.temporal must be [start, end]")
            start = _parse_iso(temporal[0], name="extent.temporal[0]")
            end = _parse_iso(temporal[1], name="extent.temporal[1]")
            if start > end:
                raise CollectionError("extent.temporal start must not exceed end")
            object.__setattr__(self, "temporal", (format_iso(start), format_iso(end)))

    @classmethod
    def from_any(cls, value: Any) -> Extent:
        if isinstance(value, Extent):
            return value
        if isinstance(value, Mapping):
            if "spatial" not in value:
                raise CollectionError("extent needs a spatial bounding box")
            return cls(spatial=value["spatial"], temporal=value.get("temporal"))
        raise CollectionError(f"invalid extent {value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "spatial": list(self.spatial),
            "temporal": None if self.temporal is None else list(self.temporal),
        }

    @property
    def crosses_antimeridian(self) -> bool:
        return self.spatial[0] > self.spatial[2]

    @staticmethod
    def union(extents: Sequence[Extent]) -> Extent | None:
        """Merge partition extents into a global extent (TACOCAT rule)."""
        if not extents:
            return None
        wests = [item.spatial[0] for item in extents]
        souths = [item.spatial[1] for item in extents]
        easts = [item.spatial[2] for item in extents]
        norths = [item.spatial[3] for item in extents]
        spatial = (min(wests), min(souths), max(easts), max(norths))
        temporals = [item.temporal for item in extents if item.temporal is not None]
        temporal = None
        if temporals:
            starts = [_parse_iso(item[0], name="temporal") for item in temporals]
            ends = [_parse_iso(item[1], name="temporal") for item in temporals]
            temporal = (format_iso(min(starts)), format_iso(max(ends)))
        return Extent(spatial=spatial, temporal=temporal)


@dataclass(frozen=True)
class Collection:
    """``COLLECTION.json``: dataset-level descriptive fields plus the contract."""

    contract: Contract
    id: str
    dataset_version: str
    description: str
    licenses: tuple[str, ...]
    providers: tuple[Provider, ...]
    tasks: tuple[str, ...]
    title: str | None = None
    curators: tuple[Curator, ...] | None = None
    keywords: tuple[str, ...] | None = None
    extent: Extent | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.contract, Contract):
            raise CollectionError("contract must be a Contract")
        if not isinstance(self.id, str) or not self.id.strip():
            raise CollectionError("collection id is required")
        if any(char in self.id for char in "/\\:\x00") or self.id != self.id.strip():
            raise CollectionError(f"collection id {self.id!r} contains forbidden characters")
        if not isinstance(self.dataset_version, str) or not SEMVER.match(self.dataset_version):
            raise CollectionError(f"dataset_version must be SemVer (X.Y.Z), got {self.dataset_version!r}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise CollectionError("collection description is required")
        object.__setattr__(self, "licenses", _string_list(self.licenses, name="licenses", required=True))
        object.__setattr__(self, "tasks", _string_list(self.tasks, name="tasks", required=True))

        providers = self.providers
        if isinstance(providers, (str, Mapping, Provider)) or not isinstance(providers, Sequence):
            raise CollectionError("providers must be a list")
        if not providers:
            raise CollectionError("providers must not be empty")
        object.__setattr__(self, "providers", tuple(Provider.from_any(item) for item in providers))

        if self.title is not None and (not isinstance(self.title, str) or len(self.title) > 250):
            raise CollectionError("title must be a string of at most 250 characters")
        if self.curators is not None:
            curators = self.curators
            if isinstance(curators, (str, Mapping, Curator)) or not isinstance(curators, Sequence):
                raise CollectionError("curators must be a list")
            object.__setattr__(self, "curators", tuple(Curator.from_any(item) for item in curators))
        if self.keywords is not None:
            object.__setattr__(self, "keywords", _string_list(self.keywords, name="keywords", required=False))
        if self.extent is not None:
            object.__setattr__(self, "extent", Extent.from_any(self.extent))

        extra = self.extra
        if extra is None:
            extra = {}
        if not isinstance(extra, Mapping):
            raise CollectionError("extra must be a mapping of additional COLLECTION.json keys")
        for key in extra:
            if not isinstance(key, str) or not key:
                raise CollectionError("extra keys must be non-empty strings")
            if key in _CORE_KEYS or key.startswith("internal:") or key.startswith("taco:"):
                raise CollectionError(f"extra key {key!r} is reserved")
        try:
            json.dumps(dict(extra))
        except (TypeError, ValueError) as exc:
            raise CollectionError(f"extra values must be JSON serializable: {exc}") from exc
        object.__setattr__(self, "extra", dict(extra))

    def replace(self, **changes: Any) -> Collection:
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "dataset_version": self.dataset_version,
            "description": self.description,
            "licenses": list(self.licenses),
            "providers": [item.to_dict() for item in self.providers],
            "tasks": list(self.tasks),
        }
        if self.title is not None:
            data["title"] = self.title
        if self.curators is not None:
            data["curators"] = [item.to_dict() for item in self.curators]
        if self.keywords is not None:
            data["keywords"] = list(self.keywords)
        if self.extent is not None:
            data["extent"] = self.extent.to_dict()
        data.update(self.contract.to_dict())
        for key, value in self.extra.items():
            data[key] = value
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Collection:
        if not isinstance(data, Mapping):
            raise CollectionError("COLLECTION.json must be a JSON object")
        missing = [
            key for key in ("id", "dataset_version", "description", "licenses", "providers", "tasks") if key not in data
        ]
        if missing:
            raise CollectionError(f"COLLECTION.json is missing required fields {missing}")
        contract = Contract.from_dict(data)
        extra = {key: value for key, value in data.items() if key not in _CORE_KEYS and not key.startswith("taco:")}
        return cls(
            contract=contract,
            id=data["id"],
            dataset_version=data["dataset_version"],
            description=data["description"],
            licenses=data["licenses"],
            providers=data["providers"],
            tasks=data["tasks"],
            title=data.get("title"),
            curators=data.get("curators"),
            keywords=data.get("keywords"),
            extent=data.get("extent"),
            extra=extra,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> Collection:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CollectionError(f"COLLECTION.json is not valid JSON: {exc}") from exc
        return cls.from_dict(data)
