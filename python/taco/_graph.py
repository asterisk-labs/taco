from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from html import escape
from pathlib import PurePosixPath

_NODE_HEIGHT = 40
_LEVEL_GAP = 70
_SIBLING_GAP = 18
_PADDING = 16
_MIN_WIDTH = 360


@dataclass(slots=True)
class _Node:
    label: str
    kind: str
    children: list[_Node] = field(default_factory=list)
    width: int = 0
    span: int = 0
    x: float = 0
    y: float = 0


def structure_graph(structure: tuple[str, ...] | None) -> str:
    root = _tree(structure)
    _measure(root)
    width = max(_MIN_WIDTH, root.span + 2 * _PADDING)
    _place(root, (width - root.span) / 2, 0)
    height = _height(root)
    edges = "".join(_edges(root))
    nodes = "".join(_nodes(root))
    return (
        '<div class="taco-structure-graph">'
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'role="img" aria-label="Sample structure">'
        f"{edges}{nodes}</svg></div>"
    )


def _tree(structure: tuple[str, ...] | None) -> _Node:
    if structure is None:
        return _Node("sample file", "file")

    root = _Node("sample", "sample")
    for declaration in structure:
        parent = root
        parts = PurePosixPath(declaration).parts
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            kind = "variable" if last and "*" in part else "file" if last else "folder"
            child = next((item for item in parent.children if item.label == part), None)
            if child is None:
                child = _Node(part, kind)
                parent.children.append(child)
            parent = child
    return root


def _shown(label: str) -> str:
    return label if len(label) <= 28 else label[:27] + "…"


def _label(node: _Node) -> str:
    return node.label + "/" if node.kind == "folder" else node.label


def _measure(node: _Node) -> int:
    node.width = max(84, min(224, len(_shown(_label(node))) * 7 + 28))
    if not node.children:
        node.span = node.width
        return node.span
    children = sum(_measure(child) for child in node.children)
    children += _SIBLING_GAP * (len(node.children) - 1)
    node.span = max(node.width, children)
    return node.span


def _place(node: _Node, left: float, depth: int) -> None:
    node.x = left + node.span / 2
    node.y = _PADDING + depth * (_NODE_HEIGHT + _LEVEL_GAP)
    if not node.children:
        return
    children = sum(child.span for child in node.children)
    children += _SIBLING_GAP * (len(node.children) - 1)
    cursor = left + (node.span - children) / 2
    for child in node.children:
        _place(child, cursor, depth + 1)
        cursor += child.span + _SIBLING_GAP


def _height(root: _Node) -> int:
    def depth(node: _Node) -> int:
        return 0 if not node.children else 1 + max(depth(child) for child in node.children)

    return 2 * _PADDING + _NODE_HEIGHT + depth(root) * (_NODE_HEIGHT + _LEVEL_GAP)


def _edges(node: _Node) -> Iterator[str]:
    for child in node.children:
        middle = (node.y + _NODE_HEIGHT + child.y) / 2
        yield (
            f'<path class="taco-graph-edge" d="M {node.x:g} {node.y + _NODE_HEIGHT:g} '
            f'V {middle:g} H {child.x:g} V {child.y:g}"/>'
        )
        yield from _edges(child)


def _nodes(node: _Node) -> Iterator[str]:
    left = node.x - node.width / 2
    display = _label(node)
    label = escape(_shown(display))
    full_label = escape(display)
    kind = "root" if node.kind == "sample" else node.kind
    yield (
        f'<g class="taco-graph-node taco-graph-{node.kind}">'
        f"<title>{full_label} ({kind})</title>"
        f'<rect x="{left:g}" y="{node.y:g}" width="{node.width}" height="{_NODE_HEIGHT}" rx="7"/>'
        f'<text class="taco-graph-label" x="{node.x:g}" y="{node.y + 17:g}" '
        f'text-anchor="middle">{label}</text>'
        f'<text class="taco-graph-kind" x="{node.x:g}" y="{node.y + 31:g}" '
        f'text-anchor="middle">{kind}</text></g>'
    )
    for child in node.children:
        yield from _nodes(child)


__all__ = ["structure_graph"]
