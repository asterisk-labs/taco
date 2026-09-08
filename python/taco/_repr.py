from __future__ import annotations

import itertools
import json
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._graph import structure_graph

if TYPE_CHECKING:
    from .dataset import Dataset
    from .schema import Field

_counter = itertools.count()

_CSS = """
#ID{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 color:inherit;display:block;max-width:760px}
#ID *{box-sizing:border-box}
#ID .taco-frame{border:1px solid rgba(128,128,128,.28);border-radius:9px;
 background:rgba(128,128,128,.035)}
#ID .taco-head{display:grid;grid-template-columns:minmax(0,1fr) 132px;
 gap:12px;align-items:center;padding:14px 16px 12px}
#ID .taco-class{opacity:.58}
#ID .taco-title{font-size:15px;font-weight:700;margin-left:7px}
#ID .taco-description{font-family:ui-sans-serif,system-ui,sans-serif;
 opacity:.7;margin-top:5px;max-width:560px}
#ID .taco-facts{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
#ID .taco-fact{border:1px solid rgba(128,128,128,.24);border-radius:999px;
 padding:1px 7px;font-size:11px;white-space:nowrap}
#ID .taco-store{width:126px;height:104px;justify-self:end}
#ID details{border-top:1px solid rgba(128,128,128,.22)}
#ID summary{display:flex;align-items:center;gap:7px;padding:7px 13px;
 cursor:pointer;list-style:none;user-select:none}
#ID summary::-webkit-details-marker{display:none}
#ID summary:before{content:'>';display:inline-block;font-size:13px;line-height:1;
 opacity:.55;transition:transform .12s ease}
#ID details[open]>summary:before{transform:rotate(90deg)}
#ID details:not([open])>.taco-content{display:none}
#ID .taco-section-name{font-weight:700}
#ID .taco-count{opacity:.5}
#ID .taco-content{padding:2px 16px 11px 35px}
#ID .taco-row{display:grid;grid-template-columns:115px minmax(0,1fr);gap:12px;
 padding:2px 0}
#ID .taco-key{opacity:.52}
#ID .taco-value{min-width:0;overflow-wrap:anywhere}
#ID .taco-path{display:flex;gap:8px;align-items:flex-start;padding:2px 0}
#ID .taco-path:before{content:'●';color:#ef9f27;font-size:8px;margin-top:5px}
#ID .taco-structure-graph{overflow-x:auto;padding:4px 0 6px}
#ID .taco-structure-graph svg{display:block;margin:0 auto;max-width:none}
#ID .taco-graph-edge{fill:none;stroke:currentColor;stroke-opacity:.26;stroke-width:1.25}
#ID .taco-graph-node rect{stroke-width:1.1}
#ID .taco-graph-sample rect{fill:rgba(245,158,11,.16);stroke:#d97706}
#ID .taco-graph-folder rect{fill:rgba(59,130,246,.12);stroke:#3b82f6}
#ID .taco-graph-file rect{fill:rgba(128,128,128,.06);stroke:currentColor;stroke-opacity:.3}
#ID .taco-graph-variable rect{fill:rgba(139,92,246,.12);stroke:#8b5cf6;stroke-dasharray:4 3}
#ID .taco-graph-label{fill:currentColor;font-size:11px;font-weight:700}
#ID .taco-graph-kind{fill:currentColor;font-size:8px;opacity:.48;letter-spacing:.08em}
#ID .taco-level{display:grid;grid-template-columns:145px minmax(0,1fr);
 gap:9px;padding:4px 0}
#ID .taco-level-name{font-weight:700;overflow-wrap:anywhere}
#ID .taco-fields{display:flex;gap:4px;flex-wrap:wrap}
#ID .taco-field{position:relative;background:rgba(59,130,246,.1);
 border:1px solid rgba(59,130,246,.2);border-radius:4px;padding:0 5px;cursor:help}
#ID .taco-field-info{position:absolute;z-index:20;visibility:hidden;opacity:0;
 top:calc(100% + 7px);left:50%;width:max-content;min-width:210px;max-width:320px;
 padding:8px 10px;border:1px solid #374151;border-radius:6px;background:#111827;color:#f9fafb;
 box-shadow:0 5px 18px rgba(0,0,0,.35);transform:translate(-50%,-3px);
 transition:opacity .1s ease,transform .1s ease;pointer-events:none;font-weight:400}
#ID .taco-field:hover>.taco-field-info{
 visibility:visible;opacity:1;transform:translate(-50%,0)}
#ID .taco-field-property{display:grid;grid-template-columns:58px minmax(0,1fr);gap:8px}
#ID .taco-field-property>span{opacity:.62}
#ID .taco-field-description{display:block;margin-top:6px;padding-top:6px;
 border-top:1px solid rgba(255,255,255,.18);font-family:ui-sans-serif,system-ui,sans-serif;
 line-height:1.35;white-space:normal}
#ID .taco-derived{background:rgba(139,92,246,.12);border-color:rgba(139,92,246,.25)}
#ID .taco-empty{opacity:.48;font-style:italic}
@media(max-width:560px){
 #ID .taco-head{grid-template-columns:1fr}
 #ID .taco-store{display:none}
 #ID .taco-row,#ID .taco-level{grid-template-columns:1fr;gap:1px}
 #ID .taco-content{padding-left:20px}
}
"""


def dataset_html(dataset: Dataset) -> str:
    uid = f"taco-dataset-{next(_counter)}"
    css = _CSS.replace("#ID", f"#{uid}")
    collection = dataset.collection
    title = escape(collection.title or collection.id)
    description = escape(collection.description)
    kind = _kind(dataset)
    facts = _facts(dataset, kind)
    return (
        f'<div id="{uid}" class="taco-dataset-repr"><style>{css}</style>'
        '<div class="taco-frame">'
        '<div class="taco-head"><div>'
        f'<div><span class="taco-class">taco.Dataset</span><span class="taco-title">{title}</span></div>'
        f'<div class="taco-description">{description}</div>'
        f'<div class="taco-facts">{facts}</div>'
        "</div>"
        f'<div class="taco-store">{_storage(kind, _source_count(dataset), uid)}</div>'
        "</div>"
        f"{_section('Structure', _structure_count(dataset), _structure(dataset), open_=True)}"
        f"{_section('Metadata', _metadata_count(dataset), _metadata(dataset))}"
        f"{_section('Collection', collection.id, _collection(dataset))}"
        f"{_section('Sources', _source_label(dataset), _sources(dataset), open_=len(dataset.sources) > 1)}"
        "</div></div>"
    )


def _facts(dataset: Dataset, kind: str) -> str:
    contract = dataset.contract
    fields = sum(len(level) for level in contract.metadata.values())
    shape = "single file" if contract.structure is None else f"{len(contract.structure)} leaves"
    values = [kind, shape, f"{len(contract.levels)} levels"]
    if fields:
        values.append(f"{fields} fields")
    samples = _sample_count(dataset)
    if samples is not None:
        values.insert(1, f"{samples:,} samples")
    return "".join(f'<span class="taco-fact">{escape(value)}</span>' for value in values)


def _section(name: str, count: str, content: str, *, open_: bool = False) -> str:
    opened = " open" if open_ else ""
    return (
        f'<details{opened}><summary><span class="taco-section-name">{escape(name)}</span>'
        f'<span class="taco-count">{escape(count)}</span></summary>'
        f'<div class="taco-content">{content}</div></details>'
    )


def _kind(dataset: Dataset) -> str:
    if len(dataset.sources) > 1:
        return "PARTITIONS"
    if dataset.collection.sources is not None:
        return "TACOCAT"
    path = dataset.sources[0]
    if isinstance(path, Path):
        return "FOLDER" if path.is_dir() else "ZIP"
    lowered = path.rstrip("/").lower()
    if lowered.endswith(".zip"):
        return "ZIP"
    if lowered.endswith(".tacocat"):
        return "TACOCAT"
    return "FOLDER"


def _source_count(dataset: Dataset) -> int:
    sources = dataset.collection.sources
    if sources is not None:
        partitions = sources.get("partitions")
        if isinstance(partitions, list):
            return len(partitions)
    return len(dataset.sources)


def _sample_count(dataset: Dataset) -> int | None:
    sources = dataset.collection.sources
    if sources is None:
        return None
    samples = sources.get("samples")
    return samples if isinstance(samples, int) and not isinstance(samples, bool) else None


def _source_label(dataset: Dataset) -> str:
    count = _source_count(dataset)
    if dataset.collection.sources is not None:
        return f"{count} partition{'s' if count != 1 else ''}"
    return f"{count} source{'s' if count != 1 else ''}"


def _structure_count(dataset: Dataset) -> str:
    structure = dataset.contract.structure
    if structure is None:
        return "single file"
    return f"{len(structure)} leaves"


def _metadata_count(dataset: Dataset) -> str:
    fields = sum(len(level) for level in dataset.contract.metadata.values())
    return f"{fields} fields"


def _structure(dataset: Dataset) -> str:
    return structure_graph(dataset.contract.structure)


def _metadata(dataset: Dataset) -> str:
    rows = []
    for level, fields in dataset.contract.metadata.items():
        produced = {field for group in dataset.contract.derived.get(level, {}).values() for field in group["produces"]}
        chips = "".join(_metadata_field(name, field, derived=name in produced) for name, field in fields.items())
        content = chips or '<span class="taco-empty">no fields</span>'
        rows.append(
            f'<div class="taco-level"><div class="taco-level-name">{escape(level)}</div>'
            f'<div class="taco-fields">{content}</div></div>'
        )
    return "".join(rows)


def _metadata_field(name: str, field: Field, *, derived: bool) -> str:
    classes = "taco-field taco-derived" if derived else "taco-field"
    description = field.description or "No description"
    nullable = "true" if field.nullable else "false"
    return (
        f'<span class="{classes}">{escape(name)}'
        '<span class="taco-field-info" role="tooltip">'
        f'<span class="taco-field-property"><span>type</span><code>{escape(field.type)}</code></span>'
        f'<span class="taco-field-property"><span>nullable</span><code>{nullable}</code></span>'
        f'<span class="taco-field-description">{escape(description)}</span>'
        "</span></span>"
    )


def _collection(dataset: Dataset) -> str:
    collection = dataset.collection
    rows: list[tuple[str, Any]] = [
        ("id", collection.id),
        ("version", collection.dataset_version),
        ("tasks", list(collection.tasks)),
        ("licenses", list(collection.licenses)),
        ("providers", [provider.name for provider in collection.providers]),
    ]
    if collection.extent is not None:
        rows.append(("extent", collection.extent.to_dict()))
    if collection.metadata is not None:
        rows.append(("metadata", sorted(collection.metadata.flatten())))
    return "".join(
        f'<div class="taco-row"><div class="taco-key">{escape(name)}</div>'
        f'<div class="taco-value">{escape(_short(value))}</div></div>'
        for name, value in rows
    )


def _sources(dataset: Dataset) -> str:
    rows = [str(path) for path in dataset.sources]
    sources = dataset.collection.sources
    if sources is not None and isinstance(sources.get("partitions"), list):
        rows.extend(
            str(partition["file"])
            for partition in sources["partitions"]
            if isinstance(partition, dict) and "file" in partition
        )
    shown = rows[:8]
    content = "".join(f'<div class="taco-path"><code>{escape(path)}</code></div>' for path in shown)
    if len(rows) > len(shown):
        content += f'<div class="taco-empty">and {len(rows) - len(shown)} more</div>'
    return content


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if not isinstance(value, str) else value
    return text if len(text) <= 140 else text[:137] + "..."


def _storage(kind: str, count: int, uid: str) -> str:
    if kind == "FOLDER":
        return _folder()
    return _cylinder(kind, count, uid)


def _folder() -> str:
    return (
        '<svg viewBox="0 0 160 125" width="100%" role="img" aria-label="TACO folder storage">'
        '<path d="M19 38c0-6 5-11 11-11h36l12 14h52c6 0 11 5 11 11v49c0 7-5 12-12 12H31c-7 0-12-5-12-12z" '
        'fill="#FAC775" stroke="#854F0B" stroke-width="1.4"/>'
        '<path d="M20 50h120" fill="none" stroke="#854F0B" stroke-width="1.2" opacity=".55"/>'
        '<text x="80" y="83" text-anchor="middle" fill="#633806" font-size="12" font-weight="700">FOLDER</text>'
        "</svg>"
    )


def _cylinder(kind: str, count: int, uid: str) -> str:
    label = escape(kind)
    gradient = f"{uid}-body"
    badge = (
        f'<g><circle cx="130" cy="22" r="15" fill="#633806"/>'
        f'<text x="130" y="26" text-anchor="middle" fill="#fff" font-size="11">x{count}</text></g>'
        if count > 1
        else ""
    )
    return (
        '<svg viewBox="0 0 160 125" width="100%" role="img" '
        f'aria-label="TACO {label.lower()} storage">'
        f'<defs><linearGradient id="{gradient}" x1="0" x2="1">'
        '<stop offset="0" stop-color="#FAEEDA"/><stop offset=".55" stop-color="#FAC775"/>'
        '<stop offset="1" stop-color="#EF9F27"/></linearGradient></defs>'
        f'<path d="M25 28v66c0 11 25 20 55 20s55-9 55-20V28" fill="url(#{gradient})" '
        'stroke="#854F0B" stroke-width="1.4"/>'
        '<ellipse cx="80" cy="28" rx="55" ry="19" fill="#FAEEDA" stroke="#854F0B" stroke-width="1.4"/>'
        '<path d="M25 61c0 11 25 20 55 20s55-9 55-20M25 85c0 11 25 20 55 20s55-9 55-20" '
        'fill="none" stroke="#854F0B" stroke-width=".7" opacity=".45"/>'
        f'<text x="80" y="67" text-anchor="middle" fill="#633806" font-size="12" font-weight="700">{label}</text>'
        f"{badge}</svg>"
    )


__all__ = ["dataset_html"]
