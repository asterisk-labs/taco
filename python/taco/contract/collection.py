from __future__ import annotations

import difflib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..errors import CollectionError, ContractError
from .contract import Contract
from .schema import validate_qualified_field

TACO_VERSION = "3.0.0"
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


# Keyword arguments of Collection other than these are collection metadata groups.
_PARAMETERS = (
    "contract",
    "id",
    "description",
    "licenses",
    "providers",
    "tasks",
    "title",
    "curators",
    "keywords",
    "extent",
    "sources",
)


def _group_values(namespace: str, value: object) -> dict[str, Any]:
    """Check one collection metadata group and return its values as JSON."""
    # Mapping-only collections should not import Pydantic.
    pydantic = sys.modules.get("pydantic")
    model = pydantic.BaseModel if pydantic is not None else None
    if model is not None and isinstance(value, model):
        scopes: frozenset[str] = getattr(type(value), "__taco_scopes__", frozenset())
        if scopes and "collection" not in scopes:
            raise CollectionError(f"{type(value).__name__} is not collection metadata")
        try:
            values = value.model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            raise CollectionError(f"collection metadata group {namespace!r} must be JSON serializable") from exc
        if not isinstance(values, dict):
            raise CollectionError(f"collection metadata group {namespace!r} must serialize to a JSON object")
    elif isinstance(value, Mapping):
        values = dict(value)
    elif model is not None and isinstance(value, type) and issubclass(value, model):
        raise CollectionError(
            f"collection metadata group {namespace!r} needs an instance, such as {value.__name__}(...)"
        )
    else:
        # A misspelled Collection parameter arrives here as a group.
        close = difflib.get_close_matches(namespace, _PARAMETERS, n=1)
        hint = f"; did you mean {close[0]!r}?" if close else ""
        raise CollectionError(
            f"collection metadata group {namespace!r} must be a mapping or a Pydantic model, "
            f"got {type(value).__name__}{hint}"
        )
    if not values:
        raise CollectionError(f"collection metadata group {namespace!r} is empty")
    for name in values:
        if not isinstance(name, str):
            raise CollectionError(f"collection metadata group {namespace!r} has a non-string field {name!r}")
        try:
            validate_qualified_field(f"{namespace}:{name}")
        except ContractError as exc:
            raise CollectionError(str(exc)) from exc
    try:
        encoded = json.dumps(values, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CollectionError(f"collection metadata group {namespace!r} must be JSON serializable") from exc
    # Normalize tuples and detach nested values from the caller's objects.
    result: dict[str, Any] = json.loads(encoded)
    return result


@dataclass(frozen=True, init=False)
class Collection:
    """Dataset description and metadata stored in COLLECTION.json.

    Keyword arguments other than the named parameters are collection metadata
    groups, such as ``labels=taco.metadata.collection.Labels(...)`` or
    ``poi={"category": "volcano"}``. Each field ``x`` of group ``g`` is stored
    as ``g:x``. ``metadata`` contains the validated groups as dictionaries.
    Collections read from disk also include the groups written by extensions.
    """

    contract: Contract
    id: str
    description: str
    licenses: tuple[str, ...]
    providers: tuple[Provider, ...]
    tasks: tuple[str, ...] | None
    title: str | None
    curators: tuple[Curator, ...] | None
    keywords: tuple[str, ...] | None
    extent: Extent | None
    sources: dict[str, Any] | None
    metadata: dict[str, dict[str, Any]]

    def __init__(
        self,
        *,
        contract: Contract,
        id: str,
        description: str,
        licenses: Sequence[str],
        providers: Sequence[Provider | Mapping[str, Any] | str],
        tasks: Sequence[str] | None = None,
        title: str | None = None,
        curators: Sequence[Curator | Mapping[str, Any] | str] | None = None,
        keywords: Sequence[str] | None = None,
        extent: Extent | Mapping[str, Any] | None = None,
        sources: dict[str, Any] | None = None,
        **groups: Any,
    ) -> None:
        if "metadata" in groups:
            raise CollectionError(
                "pass collection metadata as groups, such as labels=... or poi={...}, instead of metadata="
            )
        parameters = {
            "contract": contract,
            "id": id,
            "description": description,
            "licenses": licenses,
            "providers": providers,
            "tasks": tasks,
            "title": title,
            "curators": curators,
            "keywords": keywords,
            "extent": extent,
            "sources": sources,
        }
        self._initialize(parameters, groups)

    @classmethod
    def _from_parts(cls, parameters: Mapping[str, Any], groups: Mapping[str, Any]) -> Collection:
        # A group read from COLLECTION.json may share a name with a parameter,
        # which keyword arguments cannot express.
        instance = object.__new__(cls)
        instance._initialize(parameters, groups)
        return instance

    def _initialize(self, parameters: Mapping[str, Any], groups: Mapping[str, Any]) -> None:
        for name in _PARAMETERS:
            object.__setattr__(self, name, parameters[name])
        metadata = {
            namespace: _group_values(namespace, value) for namespace, value in groups.items() if value is not None
        }
        object.__setattr__(self, "metadata", metadata)

        if not isinstance(self.contract, Contract):
            raise CollectionError("contract must be a Contract")
        if not isinstance(self.id, str) or not self.id.strip() or any(char in self.id for char in "/\\:\x00"):
            raise CollectionError("collection id is invalid")
        if not isinstance(self.description, str) or not self.description.strip():
            raise CollectionError("collection description is required")
        object.__setattr__(self, "licenses", _string_list(self.licenses, name="licenses", required=True))
        if self.tasks is not None:
            object.__setattr__(self, "tasks", _string_list(self.tasks, name="tasks", required=True))
        if isinstance(self.providers, (str, Mapping, Provider)) or not isinstance(self.providers, Sequence):
            raise CollectionError("providers must be a list")
        if not self.providers:
            raise CollectionError("providers must not be empty")
        object.__setattr__(self, "providers", tuple(Provider.from_any(value) for value in self.providers))
        supplied_metadata = self._flat_metadata()
        try:
            extension_metadata = json.loads(json.dumps(self.contract.extension_metadata(), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise CollectionError("extension collection metadata must be JSON serializable") from exc
        for name, value in extension_metadata.items():
            if name in supplied_metadata and supplied_metadata[name] != value:
                raise CollectionError(f"collection metadata {name!r} conflicts with the active extension")
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

    def _flat_metadata(self) -> dict[str, Any]:
        return {
            f"{namespace}:{name}": value
            for namespace, values in self.metadata.items()
            for name, value in values.items()
        }

    def replace(self, **changes: Any) -> Collection:
        """Return a copy with some parameters or groups replaced; a group set to None is removed."""
        if "metadata" in changes:
            raise CollectionError("replace collection metadata by group, such as labels=..., instead of metadata=")
        parameters = {name: getattr(self, name) for name in _PARAMETERS}
        groups: dict[str, Any] = dict(self.metadata)
        for name, value in changes.items():
            if name in _PARAMETERS:
                parameters[name] = value
            elif value is None:
                groups.pop(name, None)
            else:
                groups[name] = value
        return type(self)._from_parts(parameters, groups)

    def to_dict(self) -> dict[str, Any]:
        """Return an independent copy of the collection as JSON values."""
        data: dict[str, Any] = {
            "taco:version": TACO_VERSION,
            "id": self.id,
            "description": self.description,
            "licenses": list(self.licenses),
            "providers": [provider.to_dict() for provider in self.providers],
            **self.contract.to_dict(),
        }
        if self.tasks is not None:
            data["tasks"] = list(self.tasks)
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
        data.update(self._flat_metadata())
        data.update(self.contract.extension_metadata())
        result: dict[str, Any] = json.loads(json.dumps(data, allow_nan=False))
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Collection:
        if not isinstance(data, Mapping):
            raise CollectionError("COLLECTION.json must be an object")
        required = (
            "taco:version",
            "id",
            "description",
            "licenses",
            "providers",
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
        groups: dict[str, dict[str, Any]] = {}
        for key, value in extra.items():
            namespace, _, name = key.partition(":")
            groups.setdefault(namespace, {})[name] = value
        parameters = {
            "contract": Contract.from_dict(data),
            "id": data["id"],
            "description": data["description"],
            "licenses": data["licenses"],
            "providers": tuple(Provider.from_any(value) for value in providers),
            "tasks": data.get("tasks"),
            "title": data.get("title"),
            "curators": None if curators is None else tuple(Curator.from_any(value) for value in curators),
            "keywords": data.get("keywords"),
            "extent": data.get("extent"),
            "sources": data.get("taco:sources"),
        }
        return cls._from_parts(parameters, groups)

    @classmethod
    def from_json(cls, text: str | bytes) -> Collection:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CollectionError(f"COLLECTION.json is not valid JSON: {exc}") from exc
        return cls.from_dict(data)


__all__ = ["KNOWN_TASKS", "TACO_VERSION", "Collection", "Curator", "Extent", "Provider"]
