"""Read one sample as typed arrays, using the collection's ``ml:contract``."""

from __future__ import annotations

import json
import math
import re
import struct
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from os import PathLike
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from ..metadata.ml import SCHEME_CLASSES, Calibration, MLContract, Slot, SlotKind

CONTRACT_KEY = "ml:contract"
DATA_DIR = "DATA"
SAMPLE_LEVEL = "sample"

#: `prefix*[a,b].ext`, the spec's declaration of a leaf whose file count varies
#: from sample to sample. The bracket is the permitted RANGE of that count, not
#: a character class.
VARIABLE_LEAF = re.compile(r"(?P<stem>.*?)\*\[\d+\s*,\s*\d+\](?P<ext>\.[^.]+)")

#: How many numbers make one object, for geometry stored as a flat list.
GEOMETRY_WIDTH = {SlotKind.BBOX_2D: 4, SlotKind.BBOX_OBB: 5, SlotKind.POINT_2D: 2}

#: Kinds whose values are pixel positions in the raster beside them.
PIXEL_GEOMETRY = (SlotKind.BBOX_2D, SlotKind.BBOX_OBB, SlotKind.POLYGON, SlotKind.POINT_2D)

#: Kinds that cover the picture, and so must keep lining up with each other.
SPATIAL = (SlotKind.RASTER, SlotKind.RASTER_SERIES, SlotKind.MASK, SlotKind.MASK_SERIES,
           SlotKind.MASK_SET, SlotKind.INSTANCE_ID)

#: Kinds whose metadata value is a list that is really an array.
ARRAY_FIELDS = (SlotKind.CLASS_MULTIHOT, SlotKind.CLASS_SEQUENCE, SlotKind.CLASS_DISTRIBUTION,
                SlotKind.POINT_2D, SlotKind.BBOX_2D, SlotKind.BBOX_OBB, SlotKind.POLYGON)


@dataclass
class SlotValue:
    """One named piece of a sample, together with what it means.

    A slot is one part of a sample: an image, a mask, a set of boxes, a caption.
    This pairs the decoded array with the slot's declaration, so the numbers can
    be read without going back to the contract -- what they measure, which unit
    they are in, and what the class indices stand for.
    """

    slot: Slot
    array: Any
    sample: int
    counts: list[int] | None = None
    """How many values belong to each object, where the slot declares a
    ``counts_field``. Geometry is stored as one flat run of coordinates, and this
    is what divides it back into objects."""

    members: list[str] | None = None
    """What each member of a set is, where the slot declares a ``members_field``.
    A mask set holds one mask per object referred to, and ``classes`` names the
    pixel values rather than the members, so without this a member is only an
    index."""

    tasks: list[str] | None = None
    """The task of each text item, where the slot declares a ``task_field``. One
    list of questions may mix counting, grounding and plain answers."""

    times: list[Any] | None = None
    """When each frame was acquired, where the slot declares a ``time_field``."""

    frames: int | None = None
    """How many frames a video holds, from the slot's ``frames_field``. The video
    is returned undecoded, so this is the only way to know before opening it."""

    sample_rate: int | None = None
    """Samples per second, for audio."""

    @property
    def name(self) -> str:
        return self.slot.name

    @property
    def kind(self) -> SlotKind:
        return self.slot.kind

    @property
    def role(self) -> str | None:
        return self.slot.role

    @property
    def classes(self) -> tuple[str, ...] | None:
        """Class names as a model sees them: descriptions where they exist."""
        names = self.slot.classes
        if names is None:
            return None
        described = self.slot.class_descriptions or []
        return tuple(str(described[i]) if i < len(described) and described[i] else name
                     for i, name in enumerate(names))

    @property
    def scheme_classes(self) -> tuple[str, ...] | None:
        """The classes of the slot's shared scheme, when it names one we know."""
        return SCHEME_CLASSES.get(self.slot.class_scheme) if self.slot.class_scheme else None

    @property
    def in_scheme(self):
        """The labels moved onto the slot's shared scheme through its ``class_map``.

        A mask or class index holds the dataset's own class numbers. Two datasets
        can only be trained together once both use the same numbers, and
        ``class_map`` says which shared class each of this dataset's classes is.
        Without a map the labels are returned as they are.
        """
        if self.slot.class_map is None:
            return self.array
        lookup = np.asarray(self.slot.class_map)
        stored = np.ma.getdata(self.array)
        moved = lookup[np.clip(stored, 0, len(lookup) - 1)]
        return np.ma.array(moved, mask=np.ma.getmaskarray(self.array)) \
            if np.ma.isMaskedArray(self.array) else moved

    @property
    def valid(self):
        """True where a pixel holds an observation, False where it is nodata or ignored."""
        if not hasattr(self.array, "shape"):
            return None
        return ~np.ma.getmaskarray(self.array)

    @property
    def physical(self):
        """The array converted to the unit the slot declares, such as reflectance
        or kelvin.

        Raises unless `scale_factor` and `scale_offset` invert the transform the
        values were stored with. They do not when that transform was non-linear
        or computed per image -- a quantile stretch, a gamma curve, an 8-bit
        quicklook -- nor when the stored value is already an index such as NDVI.
        Those slots are `render`, and no multiplication recovers the quantity.

        Pixels equal to the slot's ``nodata``, or to their band's, are not observations, so they come
        back as NaN rather than as the sentinel scaled into a plausible value.
        When the dataset was opened with ``masked=True`` the result is a masked
        array as well, masking those pixels and whatever was already masked.
        """
        if self.slot.calibration in (Calibration.RENDER, Calibration.DIGITAL_NUMBER,
                                     Calibration.REQUANTISED, Calibration.UNDECLARED):
            raise ValueError(
                f"{self.name}: calibration is {self.slot.calibration.value!r}, so the "
                f"values do not convert to {self.slot.units or 'a physical unit'}")
        scale = 1.0 if self.slot.scale_factor is None else self.slot.scale_factor
        offset = 0.0 if self.slot.scale_offset is None else self.slot.scale_offset
        stored = np.asarray(np.ma.getdata(self.array))
        values = np.array(stored, dtype="float64") * scale + offset
        invalid = np.ma.getmaskarray(self.array).copy() if np.ma.isMaskedArray(self.array) \
            else np.zeros(values.shape, bool)
        # The sentinel must be found in the stored values, before scaling moves it.
        invalid |= _nodata_pixels(stored, self.slot)
        values[invalid] = np.nan
        if np.ma.isMaskedArray(self.array):
            return np.ma.array(values, mask=invalid | np.isnan(values))
        return values[()] if values.ndim == 0 else values

    def __repr__(self) -> str:
        shape = getattr(self.array, "shape", None)
        return (f"<SlotValue {self.name} {self.kind.value}"
                + (f" {tuple(shape)}" if shape else "")
                + (f" [{self.role}]" if self.role else "") + ">")


def _nodata_pixels(stored: np.ndarray, slot: Slot) -> np.ndarray:
    """True where a stored value is the slot's nodata, or its own band's.

    The slot's ``nodata`` applies to every band; a band's ``nodata`` to that band
    only, on the band axis (third from last: ``(C, H, W)`` or ``(T, C, H, W)``).
    """
    def equal(values, fill):
        # A NaN sentinel marks every non-finite value, as masked_invalid does.
        return ~np.isfinite(values) if math.isnan(fill) else values == fill

    stored = np.asarray(stored)
    invalid = np.zeros(stored.shape, bool)
    if stored.dtype.kind not in "biuf":
        return invalid
    if slot.nodata is not None:
        invalid |= equal(stored, slot.nodata)
    fills = [band.nodata for band in slot.bands]
    if all(fill is None for fill in fills):
        return invalid
    if stored.ndim >= 3 and stored.shape[-3] == len(fills):
        for position, fill in enumerate(fills):
            if fill is not None:
                invalid[..., position, :, :] |= equal(stored[..., position, :, :], fill)
    elif len(fills) == 1:
        # One band, stored without a band axis: a scalar or an (H, W) plane.
        invalid |= equal(stored, fills[0])
    return invalid


def _absent(value: Any) -> bool:
    """Whether a metadata value says "nothing here": null, or NaN in a float column."""
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)


#: Which partition a TACOCAT row came from (spec 7.5).
SOURCE_COLUMN = "internal:source_file"


def _tacocat(source: str) -> Path | None:
    """The `.tacocat/` directory `source` names, directly or as its dataset directory."""
    path = Path(source)
    for candidate in (path, path / ".tacocat"):
        document = candidate / "COLLECTION.json"
        if candidate.is_dir() and document.is_file() and "taco:sources" in json.loads(document.read_text()):
            return candidate
    return None


class Dataset:
    """A TACO collection read as model-ready samples.

    Indexing returns one sample as a mapping of slot name to :class:`SlotValue`,
    with rasters decoded to arrays and metadata columns read alongside them. The
    collection must declare an ``ml:contract``, which is what says how to
    interpret each slot.

    With ``masked=True`` rasters come back as masked arrays: pixels equal to the
    slot's ``nodata`` or their band's ``nodata`` and, for labels, to its ``ignore_index`` or one of its
    ``ignore_classes`` are masked, so a
    loss or a statistic never counts them by accident.

    With ``max_pixels``, a raster larger than that is read at a half, a quarter
    or an eighth of its size, the first that fits; a JPEG does this inside its
    decoder, so the full picture is never held in memory. It only happens where
    nothing else in the sample depends on the raster's size: no slot holds pixel
    coordinates, and no other slot is a picture that would stop lining up.
    """

    def __init__(self, source: str | PathLike[str]
                 | Sequence[str | PathLike[str]], *, masked: bool = False,
                 max_pixels: int | None = None) -> None:
        from ..reader.dataset import Dataset as _Reader

        # One archive, or the parts of one collection that was split across
        # several. Parts are what a large collection is written as, and each
        # numbers its own samples from 0; the reader merges their tables into
        # one, ordered part by part, and `_locate` routes a row back to the
        # archive that holds its payloads.
        if isinstance(source, (str, PathLike)):
            parts = (str(source),)
        else:
            parts = tuple(str(part) for part in source)
        if not parts:
            raise ValueError("taco.ml.Dataset needs at least one archive")
        # A TACOCAT (spec 7.5): the `.tacocat/` directory, or the dataset directory
        # holding it. Its consolidated tables are read once, through the catalog,
        # and payloads come from the partitions it names.
        catalog = _tacocat(parts[0]) if len(parts) == 1 else None
        self.masked = masked
        self.max_pixels = max_pixels
        self._catalog = catalog
        if catalog is not None:
            document = json.loads((catalog / "COLLECTION.json").read_text())
            if CONTRACT_KEY not in document:
                raise ValueError(f"{catalog} declares no {CONTRACT_KEY}; "
                                 f"taco.ml needs one to type its samples")
            names = [entry["file"] for entry in document["taco:sources"]["partitions"]]
            self.parts = tuple(str(catalog.parent / name) for name in names)
            self.path = str(catalog)
            self.reader = _Reader(str(catalog))
            self._files = dict(zip(names, self.parts, strict=True))
            self._init_contract(document)
            return
        names = [Path(part).name for part in parts]
        if len(set(names)) != len(names):
            raise ValueError(f"parts must have distinct file names, got {names}")
        self.parts = parts
        self.path = parts[0]
        self.reader = _Reader(list(parts) if len(parts) > 1 else parts[0])
        self._files = dict(zip(names, parts, strict=True))
        documents = []
        for part in parts:
            if Path(part).is_dir():               # a FOLDER container
                documents.append(json.loads((Path(part) / "COLLECTION.json").read_text()))
                continue
            with zipfile.ZipFile(part) as archive:
                documents.append(json.loads(archive.read("COLLECTION.json")))
        document = documents[0]
        if CONTRACT_KEY not in document:
            raise ValueError(f"{self.path} declares no {CONTRACT_KEY}; "
                             f"taco.ml needs one to type its samples")
        for name, other in zip(names[1:], documents[1:], strict=True):
            if other.get(CONTRACT_KEY) != document[CONTRACT_KEY]:
                raise ValueError(f"{name} declares a different {CONTRACT_KEY} "
                                 f"from {names[0]}; they are not parts of one "
                                 f"collection")
        self._init_contract(document)

    def _init_contract(self, document: dict[str, Any]) -> None:
        self.collection = document
        self.contract = MLContract.model_validate(document[CONTRACT_KEY])
        slots = list(self.contract.inputs) + list(self.contract.targets)
        self._shrinkable = (not any(slot.kind in PIXEL_GEOMETRY for slot in slots)
                            and sum(slot.kind in SPATIAL for slot in slots) == 1)
        self._levels: dict[str, pa.Table] = {}
        self._parents: dict[str, dict[tuple[str, int], list[int]]] = {}

    # -- metadata -----------------------------------------------------------
    @cached_property
    def table(self) -> pa.Table:
        """One row per sample: the sample level's metadata."""
        return self.level(SAMPLE_LEVEL)

    def level(self, name: str) -> pa.Table:
        """One metadata level's rows, read once and kept."""
        if name not in self._levels:
            self._levels[name] = self.reader.level(name)
        return self._levels[name]

    def metadata(self, index: int) -> dict[str, Any]:
        """The sample level's row for one sample, as a dict."""
        return {name: self.table.column(name)[index].as_py() for name in self.table.column_names}

    @cached_property
    def _rows(self) -> list[tuple[str, int]]:
        """`(part, local sample id)` for each sample."""
        if self._catalog is not None:
            # A catalog renumbers rows globally but keeps each partition's own
            # relative paths, whose first component is the local sample id.
            return list(zip(self.table.column(SOURCE_COLUMN).to_pylist(),
                            (int(path.split("/")[0]) for path
                             in self.table.column("internal:relative_path").to_pylist()),
                            strict=True))
        ids = self.table.column("internal:current_id").to_pylist()
        if len(self.parts) == 1:
            return [(next(iter(self._files)), int(i)) for i in ids]
        return list(zip(self.table.column("source_file").to_pylist(), (int(i) for i in ids), strict=True))

    def _locate(self, index: int) -> tuple[str, int]:
        """The archive holding row `index`, and the sample's id inside it."""
        return self._rows[index]

    def _children(self, level: str) -> dict[tuple[str, int], list[int]]:
        """For a level below `sample`: `(part, parent id)` -> its rows, in stored order."""
        if level not in self._parents:
            table = self.level(level)
            parts = (table.column(SOURCE_COLUMN).to_pylist() if self._catalog is not None
                     else table.column("source_file").to_pylist() if len(self.parts) > 1
                     else [next(iter(self._files))] * table.num_rows)
            index: dict[tuple[str, int], list[int]] = {}
            for row, (part, parent) in enumerate(zip(parts, table.column("internal:parent_id").to_pylist(),
                                                     strict=True)):
                index.setdefault((part, int(parent)), []).append(row)
            self._parents[level] = index
        return self._parents[level]

    def _level_rows(self, index: int, level: str) -> list[int]:
        """The rows of `level` that belong to one sample, walking down from the sample."""
        part, _ = self._locate(index)
        steps = level.split("/")
        # Start from the sample row's own id, the numbering its children's
        # `internal:parent_id` use. A TACOCAT consolidated by taco 0.14 numbers rows
        # across all partitions, so the local id from the relative path would match
        # only in the first partition.
        found = [int(self.table.column("internal:current_id")[index].as_py())]
        for depth in range(1, len(steps) + 1):
            step = "/".join(steps[:depth])
            table = self.level(step)
            ids = table.column("internal:current_id").to_pylist()
            rows = [row for parent in found for row in self._children(step).get((part, parent), ())]
            if step == level:
                return rows
            found = [int(ids[row]) for row in rows]
        return []

    def _reference(self, spec: str) -> tuple[str, str]:
        """Split a companion reference `[level:]column` into its level and column.

        The part before the colon is a level only when it names one of this
        collection's metadata levels; otherwise the colon belongs to the column
        name, as in `ml:split`.
        """
        level, colon, column = spec.partition(":")
        if colon and level in self.reader.contract.levels:
            return level, column
        return SAMPLE_LEVEL, spec

    @staticmethod
    def _column(table: pa.Table, name: str) -> str | None:
        """The stored column for a contract name: as written, or in the `ml` namespace."""
        for candidate in (name, f"ml:{name}"):
            if candidate in table.column_names:
                return candidate
        return None

    def lookup(self, index: int, reference: str) -> Any:
        """Read the metadata a contract reference names, for one sample.

        A reference is `[level:]column`, the form a slot's `field` and its
        `*_field` companions take. At the sample level it gives one value; at a
        level below it, one value per row belonging to the sample, in stored order.
        """
        level, name = self._reference(reference)
        table = self.level(level)
        column = self._column(table, name)
        if column is None:
            raise KeyError(f"{reference!r} is not a column of level {level!r}")
        if level == SAMPLE_LEVEL:
            return table.column(column)[index].as_py()
        values = table.column(column)
        return [values[row].as_py() for row in self._level_rows(index, level)]

    def column(self, reference: str) -> pa.ChunkedArray:
        """A whole sample-level column, named as a contract reference names it."""
        level, name = self._reference(reference)
        column = self._column(self.level(level), name) if level == SAMPLE_LEVEL else None
        if column is None:
            raise KeyError(f"{reference!r} is not a column of the sample level")
        return self.table.column(column)

    def _lookup(self, index: int, reference: str, *, slot: Slot) -> Any:
        try:
            return self.lookup(index, reference)
        except KeyError as error:
            raise KeyError(f"slot {slot.name!r}: {error.args[0]}") from None

    # -- payloads -----------------------------------------------------------
    @cached_property
    def _payloads(self) -> dict[str, dict[str, tuple[int, int]]]:
        """Each part's payload paths, mapped to their byte ranges in it.

        Covers every metadata level below `sample`, because a sample may nest: a
        group of files is a row with no byte range of its own, and its members
        are described one level further down.
        """
        import io

        import pyarrow.parquet as pq

        out: dict[str, dict[str, tuple[int, int]]] = {}
        if self._catalog is not None:
            # One read of the consolidated tables, which carry every partition's
            # offsets beside the partition they belong to.
            out = {part: {} for part in self._files}
            for level in sorted(self._catalog.glob("*.parquet")):
                if level.name == "sample.parquet":
                    continue
                table = pq.read_table(level)
                if "internal:offset" not in table.column_names:
                    continue
                for part, relative, offset, size in zip(
                        table.column(SOURCE_COLUMN).to_pylist(),
                        table.column("internal:relative_path").to_pylist(),
                        table.column("internal:offset").to_pylist(),
                        table.column("internal:size").to_pylist(), strict=True):
                    if offset is not None:
                        out[part][relative] = (offset, size)
            return out
        for part, path in self._files.items():
            found = out[part] = {}
            if Path(path).is_dir():
                # A FOLDER container stores no byte ranges: a payload is the file
                # `DATA/<relative_path>` itself (spec 3.2), read whole. The paths come
                # from the metadata, not from stat-ing millions of files.
                for level in sorted((Path(path) / "METADATA").glob("*.parquet")):
                    if level.name == "sample.parquet":
                        continue
                    table = pq.read_table(level, columns=["internal:relative_path"])
                    found.update((relative, (0, -1)) for relative
                                 in table.column("internal:relative_path").to_pylist()
                                 if relative)
                continue
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if not (name.startswith("METADATA/") and name.endswith(".parquet")):
                        continue
                    if name == "METADATA/sample.parquet":
                        continue
                    table = pq.read_table(io.BytesIO(archive.read(name)))
                    if "internal:offset" not in table.column_names:
                        continue
                    paths = table.column("internal:relative_path").to_pylist()
                    offsets = table.column("internal:offset").to_pylist()
                    sizes = table.column("internal:size").to_pylist()
                    found.update({p: (o, s) for p, o, s in zip(paths, offsets, sizes, strict=True)
                                  if o is not None})
        return out

    def __len__(self) -> int:
        return self.table.num_rows

    def __repr__(self) -> str:
        return (f"taco.ml.Dataset({self.collection.get('id')!r}, {len(self)} samples, "
                f"{len(self.contract.inputs)} inputs, {len(self.contract.targets)} targets)")

    def _resolve(self, part: str, local: int, pattern: str) -> list[str]:
        """Payload names for one slot of one sample.

        A slot holding a variable number of files is declared `prefix*[a,b].ext`
        (spec 5.2). The `*` is NOT a filesystem glob: it is a cardinal index, so
        the files are `prefix0.ext`, `prefix1.ext`, ... `prefix(k-1).ext` with
        `a <= k <= b` for this sample. Reading it as a glob matches only the
        names whose digits happen to fall in the bracket -- `m*[3,9].tif`
        against `m0..m7` finds one file of eight, and against `m0..m2` none.
        """
        payloads = self._payloads[part]
        prefix = f"{local}/"
        literal = prefix + pattern
        if literal in payloads:
            return [literal]
        match = VARIABLE_LEAF.fullmatch(pattern)
        if match is None:
            return [literal]                      # let `_where` raise with the name
        stem, extension = match.group("stem"), match.group("ext")
        found, position = [], 0
        while f"{prefix}{stem}{position}{extension}" in payloads:
            found.append(f"{prefix}{stem}{position}{extension}")
            position += 1
        return found

    def _where(self, part: str, relative_path: str) -> tuple[int, int]:
        where = self._payloads[part].get(relative_path)
        if where is None:
            raise KeyError(f"{relative_path}: no payload in {self._files[part]}")
        return where

    def _blob(self, part: str, relative_path: str) -> bytes:
        offset, size = self._where(part, relative_path)
        with Path(self._holder(part, relative_path)).open("rb") as handle:
            handle.seek(offset)
            return handle.read(size)          # a folder's payload: offset 0, size -1

    def _is_folder(self, part: str) -> bool:
        return Path(self._files[part]).is_dir()

    def _holder(self, part: str, relative_path: str) -> str:
        """The file a payload's bytes live in: the archive, or `DATA/<path>` of a folder."""
        if self._is_folder(part):
            return str(Path(self._files[part]) / "DATA" / relative_path)
        return self._files[part]

    def _gdal_path(self, part: str, relative_path: str) -> str:
        """A path GDAL opens for one payload: a byte range of the archive, or the file."""
        offset, size = self._where(part, relative_path)
        if self._is_folder(part):
            return self._holder(part, relative_path)
        return f"/vsisubfile/{offset}_{size},{self._files[part]}"

    def _raster(self, part: str, relative_path: str, *, bands: int | None = None):
        """Decode one raster payload, reading it in place inside the archive.

        `bands` is how many channels the slot declares. A file may carry more
        -- a PNG saved with an alpha channel the publisher never used -- and
        those are dropped. A palette image is expanded to its colours first,
        because its single band holds palette positions, not the picture.
        """
        import warnings

        import rasterio
        from rasterio.errors import NotGeoreferencedWarning

        # A PNG or JPEG has no map grid, and that is normal for a picture.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(self._gdal_path(part, relative_path)) as source:
                shrink = self._shrink(source.width, source.height)
                array = source.read(masked=self.masked, out_shape=(
                    source.count, source.height // shrink, source.width // shrink))
            if bands and bands > 1 and source.count == 1 and _has_palette(source):
                palette = source.colormap(1)
                lookup = np.array([palette.get(i, (0, 0, 0, 0)) for i in range(256)], dtype="uint8")
                array = np.moveaxis(lookup[np.asarray(array[0], dtype="uint8")], -1, 0)
        if bands:
            if array.shape[0] < bands:
                raise ValueError(f"{relative_path}: {array.shape[0]} channels, but the "
                                 f"slot declares {bands}")
            array = array[:bands]
        return array

    def _shrink(self, width: int, height: int) -> int:
        """How much to reduce a raster when it is read: 1, 2, 4 or 8."""
        if self.max_pixels is None or not self._shrinkable:
            return 1
        factor = 1
        while factor < 8 and (width // factor) * (height // factor) > self.max_pixels:
            factor *= 2
        return factor

    def _payload_paths(self, part: str, local: int, slot: Slot) -> list[str]:
        paths = [slot.path] if isinstance(slot.path, str) else list(slot.path or ())
        return [name for pattern in paths for name in self._resolve(part, local, pattern)]

    def _mask_nodata(self, array, slot: Slot):
        """Mask the slot's declared nodata value, and each band's, on top of
        whatever the file tags."""
        if not self.masked or not hasattr(array, "shape") or (
                slot.nodata is None and all(band.nodata is None for band in slot.bands)):
            return array
        return np.ma.masked_where(_nodata_pixels(np.ma.getdata(array), slot), array)

    def _mask_ignored(self, array, slot: Slot):
        """Mask the label values a loss must not score: ``ignore_index`` and
        ``ignore_classes``."""
        ignored = [] if slot.ignore_index is None else [slot.ignore_index]
        ignored += list(slot.ignore_classes or ())
        if not self.masked or not ignored:
            return array
        if len(ignored) == 1:
            return np.ma.masked_equal(array, ignored[0])
        return np.ma.masked_where(np.isin(array, ignored), array)

    def _sample_shape(self, part: str, local: int) -> tuple[int, int]:
        """This sample's picture size, for a set or series that has no member here.

        An empty set still covers the image it would have marked, so its
        `(0, H, W)` takes H and W from the first fixed raster of the sample.
        """
        for slot in list(self.contract.inputs) + list(self.contract.targets):
            for pattern in ([slot.path] if isinstance(slot.path, str) else list(slot.path or ())):
                if "*" in pattern:
                    continue
                try:
                    array = self._raster(part, f"{local}/{pattern}")
                except (KeyError, ValueError):
                    continue
                if array.ndim >= 2:
                    return int(array.shape[-2]), int(array.shape[-1])
        return 0, 0

    # -- one slot -----------------------------------------------------------
    def _field(self, slot: Slot, index: int) -> Any:
        """A slot stored in the metadata rather than as a file."""
        value = self._lookup(index, slot.field, slot=slot)
        if slot.kind not in ARRAY_FIELDS or not isinstance(value, (list, tuple)):
            return value
        array = np.asarray(value)
        if slot.kind is SlotKind.CLASS_DISTRIBUTION:
            # One number per class of the legend. If the two lengths differ,
            # every value after the gap would be read as the wrong class.
            array = array.astype("float64", copy=False)
            if slot.classes is not None and array.size != len(slot.classes):
                raise ValueError(f"slot {slot.name!r}: {array.size} values for "
                                 f"{len(slot.classes)} classes")
            return array
        if slot.kind is SlotKind.POLYGON:
            return array.astype("float64", copy=False)
        width = GEOMETRY_WIDTH.get(slot.kind)
        if width and array.size:
            if array.size % width:
                raise ValueError(f"slot {slot.name!r}: {array.size} numbers do not make "
                                 f"whole objects of {width}")
            array = array.reshape(-1, width)
        return array

    def _file(self, slot: Slot, index: int, frames: Sequence[int] | None = None) -> Any:
        """A slot stored as one or more files of the sample.

        `frames` keeps only those positions of a series stored one file per frame.
        """
        part, local = self._locate(index)
        names = self._payload_paths(part, local, slot)
        if frames is not None:
            if slot.kind not in (SlotKind.MASK_SERIES, SlotKind.RASTER_SERIES) or slot.frames_field \
                    or not (isinstance(slot.path, str) and VARIABLE_LEAF.fullmatch(slot.path)):
                raise ValueError(f"slot {slot.name!r}: frames can be chosen only in a series "
                                 f"stored one file per frame")
            if any(not 0 <= int(k) < len(names) for k in frames):
                raise IndexError(f"slot {slot.name!r}: frames {list(frames)} outside 0..{len(names) - 1}")
            names = [names[int(k)] for k in frames]
        bands = len(slot.bands) or None

        if slot.kind is SlotKind.VIDEO:
            # Returned as the file's bytes. Decoding every frame would take many
            # times the stored size, and which frames to use is the reader's call.
            return self._blob(part, names[0])

        if slot.kind is SlotKind.AUDIO:
            return _wave(self._blob(part, names[0]), names[0])        # (samples, rate)

        if slot.kind is SlotKind.MASK_SET:
            if isinstance(slot.path, str) and not VARIABLE_LEAF.fullmatch(slot.path):
                # One file with one band per class: a set whose size is fixed by
                # the legend rather than by the sample.
                array = np.asarray(self._raster(part, names[0]))
                if slot.classes and array.shape[0] != len(slot.classes):
                    raise ValueError(f"slot {slot.name!r}: {array.shape[0]} bands for "
                                     f"{len(slot.classes)} classes")
                return array.astype(bool)
            members = [np.asarray(self._raster(part, name)) for name in names]
            if not members:
                return np.zeros((0, *self._sample_shape(part, local)), dtype=bool)
            return np.stack([m.reshape(m.shape[-2:]).astype(bool) for m in members])

        if slot.kind in (SlotKind.MASK_SERIES, SlotKind.RASTER_SERIES) and slot.frames_field:
            # All frames in one file, stacked on the band axis. The frame count
            # comes from the metadata, so a truncated file is caught rather than
            # silently reshaped into fewer frames.
            array = self._raster(part, names[0])
            frames = int(self._lookup(index, slot.frames_field, slot=slot))
            channels = 1 if slot.kind is SlotKind.MASK_SERIES else (bands or 1)
            if array.shape[0] != frames * channels:
                raise ValueError(f"slot {slot.name!r}: {array.shape[0]} bands, but "
                                 f"{slot.frames_field!r} says {frames} frames of {channels}")
            if slot.kind is SlotKind.MASK_SERIES:
                return self._mask_ignored(array, slot)
            return self._mask_nodata(array.reshape(frames, channels, *array.shape[1:]), slot)

        if slot.kind in (SlotKind.MASK_SERIES, SlotKind.RASTER_SERIES) or len(names) > 1:
            label = slot.kind is SlotKind.MASK_SERIES
            frames = [self._raster(part, name, bands=None if label else bands) for name in names]
            if label:
                frames = [self._mask_ignored(f.reshape(f.shape[-2:]), slot) for f in frames]
            else:
                frames = [self._mask_nodata(f, slot) for f in frames]
            if not frames:
                height, width = self._sample_shape(part, local)
                shape = (0, height, width) if label else (0, bands or 1, height, width)
                return np.zeros(shape, dtype="float32")
            # Frames of one series may differ in size as the publisher shipped
            # them. They are returned as a list then; bringing them to one size
            # is the consumer's decision.
            if len({f.shape for f in frames}) > 1:
                return frames
            stack = np.ma.stack if self.masked else np.stack
            return stack(frames)

        if not names:
            return None
        array = self._raster(part, names[0], bands=bands)
        if slot.kind in (SlotKind.MASK, SlotKind.INSTANCE_ID):
            # A mask is (H, W); a raster keeps its band axis.
            array = array[0] if array.ndim == 3 and array.shape[0] == 1 else array
            return self._mask_ignored(array, slot)
        return self._mask_nodata(array, slot)

    def _counts(self, slot: Slot, index: int, value: Any) -> list[int] | None:
        """The per-object lengths a slot points at, read from the metadata."""
        if not slot.counts_field:
            return None
        counts = self._lookup(index, slot.counts_field, slot=slot)
        if counts is None:
            return None
        counts = [int(n) for n in np.atleast_1d(counts)]
        # For boxes and mask sets a count is how many objects one query names,
        # so the counts must add up to the objects there are.
        if slot.kind in (SlotKind.BBOX_2D, SlotKind.BBOX_OBB, SlotKind.MASK_SET):
            objects = 0 if value is None else len(value)
            if sum(counts) != objects:
                raise ValueError(f"slot {slot.name!r}: {objects} objects, but "
                                 f"{slot.counts_field!r} adds up to {sum(counts)}")
        return counts

    def _members(self, slot: Slot, index: int, value: Any) -> list[str] | None:
        """The names a set's members carry, read from the slot's `members_field`."""
        if not slot.members_field:
            return None
        names = self._lookup(index, slot.members_field, slot=slot)
        if names is None:
            return None
        names = [str(name) for name in np.atleast_1d(names)]
        members = len(value) if value is not None else len(names)
        if len(names) != members:
            raise ValueError(
                f"slot {slot.name!r}: {slot.members_field!r} holds "
                f"{len(names)} names for {members} members, and it is "
                f"declared to hold one per member")
        return names

    def _tasks(self, slot: Slot, index: int, value: Any) -> list[str] | None:
        """The task of each text item, from the slot's `task_field`."""
        if not slot.task_field:
            return None
        tasks = self._lookup(index, slot.task_field, slot=slot)
        if slot.kind is SlotKind.TEXT:
            return [str(tasks)] if tasks else None
        tasks = [str(task) for task in (tasks or ())]
        if value is not None and len(tasks) != len(value):
            raise ValueError(f"slot {slot.name!r}: {len(value)} text items, but "
                             f"{slot.task_field!r} names {len(tasks)} tasks")
        return tasks

    def _times(self, slot: Slot, index: int, value: Any,
               frames: Sequence[int] | None = None) -> list[Any] | None:
        """When each frame was acquired, from the slot's `time_field`."""
        if not slot.time_field:
            return None
        level, _ = self._reference(slot.time_field)
        times = self._lookup(index, slot.time_field, slot=slot)
        if level != SAMPLE_LEVEL and isinstance(slot.path, str) and VARIABLE_LEAF.fullmatch(slot.path):
            # One date per frame file. Put them in the frames' own order, which
            # is the number in each file name, not the order the rows were stored.
            times = self._in_frame_order(index, level, slot.path, times)
        times = list(times) if isinstance(times, (list, tuple)) else [times]
        if frames is not None:
            times = [times[int(k)] for k in frames]
        expected = len(value) if slot.kind is SlotKind.RASTER_SERIES and value is not None else 1
        if len(times) != expected:
            raise ValueError(f"slot {slot.name!r}: {expected} frames, but "
                             f"{slot.time_field!r} holds {len(times)} times")
        return times

    def _in_frame_order(self, index: int, level: str, pattern: str, values: list[Any]) -> list[Any]:
        match = VARIABLE_LEAF.fullmatch(pattern)
        stem = Path(match.group("stem")).name
        numbered = re.compile(re.escape(stem) + r"(\d+)" + re.escape(match.group("ext")) + "$")
        paths = self.level(level).column("internal:relative_path")
        keys = []
        for row in self._level_rows(index, level):
            found = numbered.search(paths[row].as_py())
            keys.append(int(found.group(1)) if found else -1)
        return [value for _, value in sorted(zip(keys, values, strict=True), key=lambda pair: pair[0])]

    def __getitem__(self, index: int) -> dict[str, SlotValue]:
        return self.read(index)

    def read(self, index: int, slots: Sequence[str] | None = None,
             frames: Mapping[str, Sequence[int]] | None = None) -> dict[str, SlotValue]:
        """One sample, decoding only the slots named, or all of them.

        Decoding is most of the cost of a sample, so a caller that needs the
        label alone should not pay for a 13-band image. `frames` maps a series
        stored one file per frame to the frame positions to decode, so one crop
        of a sample holding thousands is one file read.
        """
        frames = dict(frames or {})
        if index < 0:
            index += len(self)
        declared = list(self.contract.inputs) + list(self.contract.targets)
        if unknown := set(frames) - {slot.name for slot in declared}:
            raise KeyError(f"no slot(s) {sorted(unknown)} in {self.path}")
        if slots is not None:
            unknown = set(slots) - {slot.name for slot in declared}
            if unknown:
                raise KeyError(f"no slot(s) {sorted(unknown)} in {self.path}")
            declared = [slot for slot in declared if slot.name in slots]
        sample: dict[str, SlotValue] = {}
        for slot in declared:
            try:
                value = (self._field(slot, index) if slot.field
                         else self._file(slot, index, frames.get(slot.name)))
            except KeyError:
                if slot.optional:
                    continue
                raise
            if _absent(value):
                continue
            rate = None
            if slot.kind is SlotKind.AUDIO:
                value, rate = value
            sample[slot.name] = SlotValue(
                slot=slot, array=value, sample=index,
                counts=self._counts(slot, index, value),
                members=self._members(slot, index, value),
                tasks=self._tasks(slot, index, value),
                times=self._times(slot, index, value, frames.get(slot.name)),
                frames=(int(self._lookup(index, slot.frames_field, slot=slot))
                        if slot.kind is SlotKind.VIDEO and slot.frames_field else None),
                sample_rate=rate)
        return sample


def _has_palette(source) -> bool:
    from rasterio.enums import ColorInterp

    return source.colorinterp[0] == ColorInterp.palette


def _wave(blob: bytes, name: str) -> tuple[np.ndarray, int]:
    """Decode a WAV file to (channels, samples) floats in [-1, 1), and its sample rate.

    Python's `wave` module reads integer PCM only, and field recordings are
    often stored as floats, so the chunks are walked here.
    """
    if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError(f"{name}: not a WAV file")
    fmt = data = None
    at = 12
    while at + 8 <= len(blob) and (fmt is None or data is None):
        chunk, size = blob[at:at + 4], int.from_bytes(blob[at + 4:at + 8], "little")
        body = blob[at + 8:at + 8 + size]
        if chunk == b"fmt ":
            tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and size >= 40:            # WAVE_FORMAT_EXTENSIBLE
                tag = int.from_bytes(body[24:26], "little")
            fmt = (tag, channels, rate, bits)
        elif chunk == b"data":
            data = body
        at += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ValueError(f"{name}: no fmt or data chunk")
    tag, channels, rate, bits = fmt
    if tag == 3 and bits in (32, 64):
        samples = np.frombuffer(data, dtype=f"<f{bits // 8}").astype("float32")
    elif tag == 1 and bits == 8:                        # 8-bit PCM is unsigned
        samples = (np.frombuffer(data, dtype="u1").astype("float32") - 128.0) / 128.0
    elif tag == 1 and bits in (16, 32):
        samples = np.frombuffer(data, dtype=f"<i{bits // 8}").astype("float32") / float(2 ** (bits - 1))
    else:
        raise ValueError(f"{name}: unsupported WAV encoding (format {tag}, {bits} bits)")
    return samples.reshape(-1, channels).T.copy(), int(rate)


__all__ = ["Dataset", "SlotValue"]
