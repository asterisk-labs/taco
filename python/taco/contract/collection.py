from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from ..errors import CollectionError
from ..schema import CollectionMetadata
from .contract import Contract

TACO_VERSION = "3.0.0"
SEMVER = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
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
        "taco:version",
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
        "taco:derived",
        "taco:sources",
    }
)


def _string_list(values: object, *, name: str, required: bool) -> tuple[str, ...]:
    if values is None:
        if required:
            raise CollectionError(f"{name} is required")
        return ()
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise CollectionError(f"{name} must be a list of strings")
    result = tuple(values)
    if required and not result:
        raise CollectionError(f"{name} must not be empty")
    if not all(isinstance(item, str) and item.strip() for item in result):
        raise CollectionError(f"{name} must contain non-empty strings")
    return result


def _parse_iso(value: object, *, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip().removesuffix("Z") + ("+00:00" if value.strip().endswith("Z") else "")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise CollectionError(f"{name} must be an ISO 8601 datetime") from exc
    else:
        raise CollectionError(f"{name} must be an ISO 8601 datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Provider:
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
            raise CollectionError("provider url must start with http:// or https://")
        if self.links is not None:
            if isinstance(self.links, (str, bytes)) or not isinstance(self.links, Sequence):
                raise CollectionError("provider links must be a list of objects")
            links = tuple(dict(link) for link in self.links if isinstance(link, Mapping))
            if len(links) != len(self.links):
                raise CollectionError("provider links must be a list of objects")
            object.__setattr__(self, "links", links)

    @classmethod
    def from_any(cls, value: object) -> Provider:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(value)
        if isinstance(value, Mapping):
            fields = {"name", "roles", "url", "links"}
            extra = sorted(set(value) - fields)
            if extra:
                raise CollectionError(f"provider has unknown fields {extra}")
            if "name" not in value:
                raise CollectionError("provider name is required")
            return cls(**{key: value[key] for key in fields if key in value})
        raise CollectionError(f"invalid provider {value!r}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name}
        for name in ("roles", "url", "links"):
            value = getattr(self, name)
            if value is not None:
                data[name] = list(value) if isinstance(value, tuple) else value
        return data


@dataclass(frozen=True)
class Curator:
    name: str | None = None
    organization: str | None = None
    email: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        if not self.name and not self.organization:
            raise CollectionError("a curator needs a name or an organization")
        if self.email is not None and "@" not in self.email:
            raise CollectionError("invalid curator email")

    @classmethod
    def from_any(cls, value: object) -> Curator:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(name=value)
        if isinstance(value, Mapping):
            fields = {"name", "organization", "email", "role"}
            extra = sorted(set(value) - fields)
            if extra:
                raise CollectionError(f"curator has unknown fields {extra}")
            return cls(**{key: value[key] for key in fields if key in value})
        raise CollectionError(f"invalid curator {value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {name: value for name, value in vars(self).items() if value is not None}


@dataclass(frozen=True)
class Extent:
    spatial: tuple[float, float, float, float]
    temporal: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.spatial, str) or not isinstance(self.spatial, Sequence) or len(self.spatial) != 4:
            raise CollectionError("extent.spatial must be [west, south, east, north]")
        try:
            west, south, east, north = (float(value) for value in self.spatial)
        except (TypeError, ValueError) as exc:
            raise CollectionError("extent.spatial must contain numbers") from exc
        if not all(math.isfinite(value) for value in (west, south, east, north)):
            raise CollectionError("extent.spatial values must be finite")
        if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
            raise CollectionError("extent.spatial is outside EPSG:4326 bounds")
        object.__setattr__(self, "spatial", (west, south, east, north))
        if self.temporal is not None:
            if isinstance(self.temporal, str) or len(self.temporal) != 2:
                raise CollectionError("extent.temporal must be [start, end]")
            start = _parse_iso(self.temporal[0], name="extent.temporal[0]")
            end = _parse_iso(self.temporal[1], name="extent.temporal[1]")
            if start > end:
                raise CollectionError("extent.temporal start must not exceed end")
            object.__setattr__(self, "temporal", (format_iso(start), format_iso(end)))

    @classmethod
    def from_any(cls, value: object) -> Extent:
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping) and "spatial" in value and not set(value) - {"spatial", "temporal"}:
            return cls(value["spatial"], value.get("temporal"))
        raise CollectionError("invalid extent")

    def to_dict(self) -> dict[str, Any]:
        return {"spatial": list(self.spatial), "temporal": None if self.temporal is None else list(self.temporal)}

    @property
    def crosses_antimeridian(self) -> bool:
        return self.spatial[0] > self.spatial[2]

    @staticmethod
    def union(extents: Sequence[Extent]) -> Extent | None:
        if not extents:
            return None
        south = min(item.spatial[1] for item in extents)
        north = max(item.spatial[3] for item in extents)
        intervals: list[tuple[float, float]] = []
        for item in extents:
            west, _, east, _ = item.spatial
            start, end = west + 180, east + 180
            if west <= east:
                intervals.append((start, end))
            else:
                intervals.extend(((0, end), (start, 360)))
        merged: list[list[float]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        gaps = [(merged[index][1], merged[index + 1][0]) for index in range(len(merged) - 1)]
        gaps.append((merged[-1][1], merged[0][0] + 360))
        gap_start, gap_end = max(gaps, key=lambda gap: gap[1] - gap[0])
        if gap_start == gap_end:
            west, east = -180.0, 180.0
        else:
            start = gap_end % 360
            end = gap_start % 360
            if math.isclose(start, end, abs_tol=1e-12):
                start = end
            west = start - 180
            east = 180.0 if end == 0 and start > 0 else end - 180
        temporal_values = [item.temporal for item in extents if item.temporal is not None]
        temporal = None
        if temporal_values:
            starts = [_parse_iso(value[0], name="temporal") for value in temporal_values]
            ends = [_parse_iso(value[1], name="temporal") for value in temporal_values]
            temporal = (format_iso(min(starts)), format_iso(max(ends)))
        return Extent((west, south, east, north), temporal)


@dataclass(frozen=True)
class Collection:
    contract: Contract
    id: str
    dataset_version: str
    description: str
    licenses: tuple[str, ...]
    providers: tuple[Provider, ...]
    tasks: tuple[str, ...]
    metadata: CollectionMetadata | None = None
    title: str | None = None
    curators: tuple[Curator, ...] | None = None
    keywords: tuple[str, ...] | None = None
    extent: Extent | None = None
    sources: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.contract, Contract):
            raise CollectionError("contract must be a Contract")
        if not isinstance(self.id, str) or not self.id.strip() or any(char in self.id for char in "/\\:\x00"):
            raise CollectionError("collection id is invalid")
        if not isinstance(self.dataset_version, str) or not SEMVER.fullmatch(self.dataset_version):
            raise CollectionError("dataset_version must follow Semantic Versioning")
        if not isinstance(self.description, str) or not self.description.strip():
            raise CollectionError("collection description is required")
        object.__setattr__(self, "licenses", _string_list(self.licenses, name="licenses", required=True))
        object.__setattr__(self, "tasks", _string_list(self.tasks, name="tasks", required=True))
        if isinstance(self.providers, (str, Mapping, Provider)) or not isinstance(self.providers, Sequence):
            raise CollectionError("providers must be a list")
        if not self.providers:
            raise CollectionError("providers must not be empty")
        object.__setattr__(self, "providers", tuple(Provider.from_any(value) for value in self.providers))
        if self.metadata is not None and not isinstance(self.metadata, CollectionMetadata):
            raise CollectionError("metadata must be taco.CollectionMetadata")
        if self.metadata is not None:
            try:
                json.dumps(self.metadata.flatten(), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise CollectionError("collection metadata must be JSON serializable") from exc
        if self.curators is not None:
            object.__setattr__(self, "curators", tuple(Curator.from_any(value) for value in self.curators))
        if self.keywords is not None:
            object.__setattr__(self, "keywords", _string_list(self.keywords, name="keywords", required=False))
        if self.extent is not None:
            object.__setattr__(self, "extent", Extent.from_any(self.extent))
        if self.sources is not None:
            try:
                json.dumps(self.sources, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise CollectionError("taco:sources must be JSON serializable") from exc

    def replace(self, **changes: Any) -> Collection:
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "taco:version": TACO_VERSION,
            "id": self.id,
            "dataset_version": self.dataset_version,
            "description": self.description,
            "licenses": list(self.licenses),
            "providers": [provider.to_dict() for provider in self.providers],
            "tasks": list(self.tasks),
            **self.contract.to_dict(),
        }
        if self.title is not None:
            data["title"] = self.title
        if self.curators is not None:
            data["curators"] = [curator.to_dict() for curator in self.curators]
        if self.keywords is not None:
            data["keywords"] = list(self.keywords)
        if self.extent is not None:
            data["extent"] = self.extent.to_dict()
        if self.sources is not None:
            data["taco:sources"] = self.sources
        if self.metadata is not None:
            data.update(self.metadata.flatten())
        json.dumps(data, allow_nan=False)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Collection:
        if not isinstance(data, Mapping):
            raise CollectionError("COLLECTION.json must be an object")
        required = (
            "taco:version",
            "id",
            "dataset_version",
            "description",
            "licenses",
            "providers",
            "tasks",
            "taco:structure",
            "taco:metadata",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise CollectionError(f"COLLECTION.json is missing required fields {missing}")
        if data["taco:version"] != TACO_VERSION:
            raise CollectionError(f"taco:version must be {TACO_VERSION}")
        providers = data["providers"]
        if not isinstance(providers, list) or not all(isinstance(value, Mapping) for value in providers):
            raise CollectionError("providers in COLLECTION.json must be a list of objects")
        curators = data.get("curators")
        if curators is not None and (
            not isinstance(curators, list) or not all(isinstance(value, Mapping) for value in curators)
        ):
            raise CollectionError("curators in COLLECTION.json must be a list of objects")
        unknown_reserved = sorted(
            key for key in data if key.startswith(("taco:", "internal:", "cozip:")) and key not in _CORE_KEYS
        )
        if unknown_reserved:
            raise CollectionError(f"COLLECTION.json uses unknown reserved keys {unknown_reserved}")
        extra = {key: value for key, value in data.items() if key not in _CORE_KEYS}
        unqualified = sorted(key for key in extra if ":" not in key)
        if unqualified:
            raise CollectionError(f"COLLECTION.json has unknown unqualified fields {unqualified}")
        return cls(
            contract=Contract.from_dict(data),
            id=data["id"],
            dataset_version=data["dataset_version"],
            description=data["description"],
            licenses=data["licenses"],
            providers=tuple(Provider.from_any(value) for value in providers),
            tasks=data["tasks"],
            metadata=CollectionMetadata.from_flat(extra) if extra else None,
            title=data.get("title"),
            curators=None if curators is None else tuple(Curator.from_any(value) for value in curators),
            keywords=data.get("keywords"),
            extent=data.get("extent"),
            sources=data.get("taco:sources"),
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> Collection:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CollectionError(f"COLLECTION.json is not valid JSON: {exc}") from exc
        return cls.from_dict(data)


__all__ = ["KNOWN_TASKS", "TACO_VERSION", "Collection", "Curator", "Extent", "Provider"]
