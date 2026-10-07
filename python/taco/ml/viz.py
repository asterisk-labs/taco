"""Draw one sample: every slot, rendered according to what the contract says it is.

The layout is the same for every dataset, which is the point -- a collection you
have never seen renders the same way as one you know. Inputs are drawn first,
then targets, so a figure reads in the order a model consumes it.
"""

from __future__ import annotations

import numpy as np

from ..metadata.ml import Calibration, Modality, SlotKind
from .dataset import SlotValue

#: Panels are drawn at this many columns unless told otherwise.
COLUMNS = 4

#: Rasters larger than this are subsampled for display only.
MAX_DISPLAY_PX = 1_200

#: Frames drawn per series. A dataset can hold dozens of dates across dozens of
#: slots, and drawing every one produces a figure tens of thousands of pixels
#: tall. What is left out is reported, never dropped in silence.
MAX_FRAMES = 4

#: A legend never exceeds this many entries, or this many characters per entry.
#: What is dropped is counted, so a panel says how much it is not showing.
LEGEND_MAX_ENTRIES = 8
LEGEND_MAX_CHARS = 22

#: Up to this many entries a legend is drawn inside its panel; beyond it, beside.
LEGEND_INSIDE_MAX = 6

#: Colormaps by modality, so the same quantity looks the same across datasets.
COLORMAPS = {
    Modality.SAR: "gray",
    Modality.THERMAL: "inferno",
    Modality.ELEVATION: "terrain",
    Modality.ATMOSPHERIC: "magma",
    Modality.BIOPHYSICAL: "YlGn",
}

GEOMETRY = (SlotKind.BBOX_2D, SlotKind.BBOX_OBB, SlotKind.POLYGON, SlotKind.POINT_2D)

#: Numbers per object, by kind. Geometry is stored flat, so this is what turns a
#: run of coordinates back into objects. A polygon has no fixed stride: its rings
#: are segmented by the lengths in the slot's `counts_field`.
STRIDE = {SlotKind.BBOX_2D: 4, SlotKind.BBOX_OBB: 5, SlotKind.POINT_2D: 2}
SERIES = (SlotKind.RASTER_SERIES, SlotKind.MASK_SERIES)


def _one_line(text: object, width: int) -> str:
    """One line, cut with an ellipsis, so a label cannot grow past its panel."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: max(1, width - 1)].rstrip() + "…"


def _wrap(text: str, width: int, lines: int) -> str:
    """Wrap to `width` and cut to `lines`, saying how much was dropped."""
    import textwrap

    out: list[str] = []
    for paragraph in str(text).split("\n"):
        out.extend(textwrap.wrap(paragraph, width) or [""])
    if len(out) <= lines:
        return "\n".join(out)
    kept = out[: max(1, lines - 1)]
    return "\n".join([*kept, f"[+{len(out) - len(kept)} lines]"])


def _subsample(array: np.ndarray) -> np.ndarray:
    """Thin a large raster for display. Never used for statistics."""
    height, width = array.shape[-2:]
    step = max(1, int(max(height, width) / MAX_DISPLAY_PX))
    return array[..., ::step, ::step] if step > 1 else array


def _stretch(array: np.ndarray, low: float = 2.0, high: float = 98.0) -> np.ndarray:
    """Percentile stretch to 0..1, for display only."""
    finite = np.asarray(array, dtype="float64")
    valid = finite[np.isfinite(finite)]
    if valid.size == 0:
        return np.zeros_like(finite)
    lo, hi = np.percentile(valid, [low, high])
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((finite - lo) / (hi - lo), 0.0, 1.0)


def _rgb_bands(slot) -> list[int] | None:
    """Band indices to show as red, green and blue, if the slot names them."""
    wanted = ("red", "green", "blue")
    found = {band.common_name: band.index for band in slot.bands if band.common_name}
    if all(name in found for name in wanted):
        return [found[name] for name in wanted]
    return None


#: Height over width for a panel of text. A square row around three lines of
#: text is mostly blank, and a figure of scalars was more gap than content.
TEXT_ASPECT = 0.45


def _aspect(value: SlotValue) -> float:
    """Height over width of the panel a slot draws."""
    try:
        if value.kind not in (SlotKind.RASTER, SlotKind.MASK, SlotKind.MASK_SET, *SERIES):
            return TEXT_ASPECT
        shape = np.shape(value.array)
        if len(shape) < 2 or not shape[-1]:
            return 1.0
        return min(max(shape[-2] / shape[-1], 0.35), 2.2)
    except Exception:
        return 1.0


def _summary(array: np.ndarray, unit: str | None) -> str:
    """Range and missingness, in whatever unit the panel is showing."""
    finite = np.asarray(array, dtype="float64")
    valid = finite[np.isfinite(finite)]
    if valid.size == 0:
        return "all missing"
    text = f"{valid.min():.3g} .. {valid.max():.3g}"
    if unit:
        text += f" {unit}"
    missing = finite.size - valid.size
    if missing:
        text += f", {100 * missing / finite.size:.2g}% missing"
    return text


def _legend(axis, handles, total: int) -> None:
    """Place a legend where it will not cover the next panel, capped in both axes."""
    from matplotlib.patches import Patch

    if total > LEGEND_MAX_ENTRIES:
        handles = [*handles[: LEGEND_MAX_ENTRIES - 1],
                   Patch(facecolor="none", edgecolor="none",
                         label=f"[+{total - LEGEND_MAX_ENTRIES + 1} classes]")]
    if len(handles) <= LEGEND_INSIDE_MAX:
        axis.legend(handles=handles, fontsize=6, loc="upper right", frameon=True,
                    framealpha=0.78, borderpad=0.3, handlelength=1.1,
                    labelspacing=0.25).set_zorder(5)
        return
    offset = 1.28 if getattr(axis, "_taco_colorbar", False) else 1.01
    axis.legend(handles=handles, fontsize=6, loc="upper left",
                bbox_to_anchor=(offset, 1.0), frameon=False)


def _draw_mask(value: SlotValue, axis) -> str:
    """A class raster, with a legend of the classes actually present."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    array = np.ma.masked_invalid(np.asarray(value.array))
    names = value.classes or ()
    shown = _subsample(np.ma.getdata(array))
    present = sorted(int(v) for v in np.unique(np.ma.getdata(array))
                     if not np.ma.is_masked(v))
    span = max(len(names) - 1, 1)
    colormap = plt.get_cmap("tab20", max(len(names), 2))
    axis.imshow(colormap(np.clip(shown, 0, span) / span), interpolation="nearest")
    _legend(axis, [Patch(facecolor=colormap(v / span),
                         label=_one_line(f"{v} {names[v]}" if v < len(names) else v,
                                         LEGEND_MAX_CHARS))
                   for v in present], total=len(present))
    axis.set_xticks([])
    axis.set_yticks([])
    return f"{len(names)} classes" if names else "mask"


def _draw_raster(value: SlotValue, axis) -> str:
    """An image: RGB where the bands say so, otherwise a single band with a bar."""
    import matplotlib.pyplot as plt

    array = np.asarray(value.array)
    if array.ndim == 2:
        array = array[None]
    if array.shape[-1] * array.shape[-2] <= 4:
        # A one- or few-pixel raster carries its information in the VALUES, not
        # in a picture: drawn as an image it is a flat rectangle of colour.
        per_band = array.reshape(array.shape[0], -1).mean(axis=1)
        axis.text(0.02, 0.98,
                  "\n".join(f"band {i}: {v:.4g}" for i, v in enumerate(per_band)),
                  ha="left", va="top", fontsize=7, transform=axis.transAxes)
        axis.set_axis_off()
        return f"{array.shape[0]} bands, {array.shape[-2]}x{array.shape[-1]} px"
    shown = _subsample(array)
    detail: list[str] = []
    rgb = _rgb_bands(value.slot)
    if rgb and shown.shape[0] > max(rgb):
        axis.imshow(np.dstack([_stretch(shown[i]) for i in rgb]))
        detail.append("RGB red/green/blue")
    elif shown.shape[0] >= 3:
        axis.imshow(np.dstack([_stretch(shown[i]) for i in range(3)]))
        detail.append("first three bands")
    else:
        image = axis.imshow(shown[0], cmap=COLORMAPS.get(value.slot.modality, "viridis"))
        bar = plt.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
        bar.ax.tick_params(labelsize=6)
        if value.slot.units:
            bar.set_label(value.slot.units, fontsize=6)
        axis._taco_colorbar = True
    if value.slot.calibration and value.slot.calibration is not Calibration.PHYSICAL:
        detail.append(value.slot.calibration.value)
    detail.append(_summary(array, value.slot.units))
    axis.set_xticks([])
    axis.set_yticks([])
    return ", ".join(detail)


def _draw_text(value: SlotValue, axis) -> str:
    """Anything whose answer is words or a class name."""
    array = value.array
    names = value.classes
    if value.kind is SlotKind.CLASS_INDEX and names:
        body = f"{int(np.asarray(array))}  {names[int(np.asarray(array))]}"
    elif value.kind is SlotKind.CLASS_MULTIHOT and names:
        on = [names[i] for i, flag in enumerate(np.atleast_1d(array)) if flag]
        body = "\n".join(on) or "(none)"
    elif value.kind is SlotKind.CLASS_SEQUENCE and names:
        items = [names[int(i)] if int(i) < len(names) else int(i)
                 for i in np.atleast_1d(array)]
        body = f"{len(items)} items\n" + "\n".join(
            f"{i + 1}. {item}" for i, item in enumerate(items))
    elif isinstance(array, (list, tuple, np.ndarray)) and not np.isscalar(array):
        items = list(np.atleast_1d(array))
        body = f"{len(items)} items\n" + "\n".join(
            f"{i + 1}. {item}" for i, item in enumerate(items))
    elif np.isscalar(array) or np.ndim(array) == 0:
        number = np.asarray(array).item()
        body = (f"{number:.4g}" if isinstance(number, float) else str(number))
        if value.slot.units:
            body += f" {value.slot.units}"
    else:
        body = str(array)
    axis.text(0.02, 0.98, _wrap(body, 46, 14), ha="left", va="top", fontsize=7,
              linespacing=1.4, transform=axis.transAxes)
    axis.set_axis_off()
    return value.kind.value


def _rings(value: SlotValue) -> list[np.ndarray]:
    """Split a flat run of polygon coordinates into one array of vertices per ring."""
    flat = np.asarray(value.array, dtype="float64").ravel()
    lengths = list(value.counts or [])
    # A count may be given in vertices or in coordinates; the totals say which.
    if lengths and sum(lengths) * 2 == flat.size:
        lengths = [n * 2 for n in lengths]
    elif not lengths or sum(lengths) != flat.size:
        lengths = [flat.size]
    out, start = [], 0
    for length in lengths:
        chunk = flat[start:start + length]
        if chunk.size >= 6 and chunk.size % 2 == 0:
            out.append(chunk.reshape(-1, 2))
        start += length
    return out


def _overlay(axis, value: SlotValue, labels: SlotValue | None) -> None:
    """Draw boxes, oriented boxes, polygons or points over the panel beneath."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon, Rectangle

    flat = np.asarray(value.array, dtype="float64").ravel()
    if flat.size == 0:
        return
    classes = None if labels is None else np.atleast_1d(np.asarray(labels.array))
    palette = plt.get_cmap("tab10")

    if value.kind is SlotKind.POLYGON:
        for position, ring in enumerate(_rings(value)):
            index = int(classes[position]) if classes is not None and position < len(classes) else 0
            axis.add_patch(Polygon(ring, closed=True, fill=False, lw=0.6,
                                   edgecolor=palette(index % 10)))
        return

    stride = STRIDE.get(value.kind, 4)
    if flat.size % stride:
        return
    for position, row in enumerate(flat.reshape(-1, stride)):
        index = int(classes[position]) if classes is not None and position < len(classes) else 0
        colour = palette(index % 10)
        if value.kind is SlotKind.BBOX_OBB:
            cx, cy, width, height, angle = row[:5]
            cos, sin = np.cos(angle), np.sin(angle)
            corners = np.array([[-width / 2, -height / 2], [width / 2, -height / 2],
                                [width / 2, height / 2], [-width / 2, height / 2]])
            spun = corners @ np.array([[cos, sin], [-sin, cos]]) + [cx, cy]
            axis.add_patch(Polygon(spun, closed=True, fill=False, lw=0.6, edgecolor=colour))
        elif value.kind is SlotKind.POINT_2D:
            axis.plot(row[0], row[1], marker="+", color=colour, markersize=4)
        else:
            x0, y0, x1, y1 = row[:4]
            axis.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False,
                                     lw=0.6, edgecolor=colour))


def _count(value: SlotValue) -> int:
    """How many objects a geometry slot holds."""
    if value.kind is SlotKind.POLYGON:
        return len(_rings(value))
    flat = np.asarray(value.array).ravel()
    return int(flat.size // STRIDE.get(value.kind, 4))


def _panels(sample: dict[str, SlotValue], max_frames: int | None) -> list[SlotValue]:
    """Slots to draw, with a series unrolled into one panel per frame."""
    out: list[SlotValue] = []
    for value in sample.values():
        if value.kind not in SERIES:
            out.append(value)
            continue
        frames = np.asarray(value.array)
        keep = len(frames) if max_frames is None else min(len(frames), max_frames)
        if keep < len(frames):
            print(f"plot_sample: {value.name} has {len(frames)} frames, showing "
                  f"the first {keep} (pass max_frames=None for all)")
        for index in range(keep):
            out.append(SlotValue(slot=value.slot.model_copy(
                update={"name": f"{value.name}[{index}]",
                        "kind": SlotKind.RASTER if value.kind is SlotKind.RASTER_SERIES
                        else SlotKind.MASK}),
                array=frames[index], sample=value.sample))
    return out


def _draw_mask_set(value: SlotValue, axis) -> str:
    """(N, H, W) binary masks, drawn as which member covers each pixel.

    Members frequently partition the frame, in which case a count of how many
    cover each pixel is 1 everywhere and says nothing. What distinguishes them
    is WHICH one, so the panel is an index map with each member's share of the
    frame in the legend. Overlap is reported in the title, where it exists.

    The legend names a member where the slot declares a ``members_field``, and
    falls back to its index where it does not.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    array = np.asarray(value.array)
    if array.ndim == 2:
        array = array[None]
    binary = array != 0
    members = binary.shape[0]
    counts = binary.sum(axis=0)
    first = np.where(counts > 0, binary.argmax(axis=0), -1)
    shown = _subsample(first)

    span = max(members - 1, 1)
    colormap = plt.get_cmap("tab20", max(members, 2))
    rgba = colormap(np.clip(shown, 0, span) / span)
    rgba[shown < 0] = (0.0, 0.0, 0.0, 0.0)
    axis.imshow(rgba, interpolation="nearest")
    shares = [float(m.mean()) for m in binary]
    names = value.members or [str(i) for i in range(members)]
    order = sorted(range(members), key=lambda i: -shares[i])
    _legend(axis, [Patch(facecolor=colormap(i / span),
                         label=_one_line(f"{names[i]} ({100 * shares[i]:.3g}%)",
                                         LEGEND_MAX_CHARS))
                   for i in order], total=members)
    axis.set_xticks([])
    axis.set_yticks([])
    uncovered = float((counts == 0).mean())
    overlap = float((counts > 1).mean())
    detail = f"{members} mask(s)"
    if overlap:
        detail += f", {100 * overlap:.3g}% overlapping"
    if uncovered:
        detail += f", {100 * uncovered:.3g}% uncovered"
    else:
        detail += ", partitioning the frame"
    return detail


def plot_slot(value: SlotValue, axis) -> str:
    """Render one slot into one axis and return the title it chose."""
    if value.kind is SlotKind.MASK_SET:
        return _draw_mask_set(value, axis)
    if value.kind in (SlotKind.MASK, SlotKind.INSTANCE_ID):
        return _draw_mask(value, axis)
    if value.kind is SlotKind.RASTER:
        return _draw_raster(value, axis)
    return _draw_text(value, axis)


def plot_sample(sample: dict[str, SlotValue], *, columns: int = COLUMNS,
                size: float = 3.0, title: str | None = None, dpi: float = 140,
                max_frames: int | None = MAX_FRAMES):
    """Draw every slot of one sample and return the figure.

    Geometry slots are drawn over the first image rather than in panels of their
    own, because a box means nothing away from the picture it marks.
    """
    import matplotlib.pyplot as plt

    geometry = [v for v in sample.values() if v.kind in GEOMETRY]
    labels = next((v for v in sample.values()
                   if v.kind is SlotKind.CLASS_SEQUENCE), None)
    drawn = {id(v) for v in geometry}
    panels = [v for v in _panels(sample, max_frames) if id(v) not in drawn]
    panels.sort(key=lambda v: v.role != "input")

    columns = min(columns, max(len(panels), 1))
    rows = -(-len(panels) // columns)
    heights = [max((_aspect(v) for v in panels[r * columns:(r + 1) * columns]),
                   default=1.0) for r in range(rows)]
    figure, axes = plt.subplots(rows, columns, dpi=dpi,
                                figsize=(size * columns * 1.02, size * sum(heights)),
                                gridspec_kw={"height_ratios": heights})
    axes = np.atleast_1d(axes).ravel()

    overlaid = False
    for axis, value in zip(axes, panels, strict=False):
        text = plot_slot(value, axis)
        axis.set_box_aspect(_aspect(value))
        if geometry and value.kind is SlotKind.RASTER and value.role == "input" \
                and not overlaid:
            for shape in geometry:
                _overlay(axis, shape, labels)
            overlaid = True
            text += "\n+ " + ", ".join(
                f"{_count(g)} {g.name} ({g.kind.value})" for g in geometry)
        marker = "[OUT] " if value.role == "target" else ""
        axis.set_title(_wrap(f"{marker}{value.name}\n{text}", 52, 4),
                       fontsize=7, loc="left")
    for axis in axes[len(panels):]:
        axis.set_axis_off()

    height = max(figure.get_figheight(), 1e-6)
    top = 1.0 - (0.34 / height if title else 0.0)
    figure.tight_layout(pad=0.30, w_pad=0.15, h_pad=0.45, rect=(0.0, 0.0, 1.0, top))
    if title:
        figure.suptitle(title, fontsize=9, y=1.0 - 0.06 / height, va="top")
    return figure


__all__ = ["plot_sample", "plot_slot"]
