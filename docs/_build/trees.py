"""Write docs/assets/svg/dataset-trees.svg: the sample tree of thirteen datasets, one wheel per library.

The trees keep the real depth of each sample and fewer files. They follow the figure of the
TACO IGARSS 2027 paper, drawn in the colours of this site.
"""

from math import cos, degrees, radians, sin
from pathlib import Path

F, D = "file", "folder"

# (library, [(dataset, children), ...]). Every sample is a folder holding at least one file.
WHEELS = [
    ("TorchGeo", [
        ("BioMassters", [(D, [(D, [F, F]), (D, [F, F])]), (D, [(D, [F, F]), (D, [F, F])]), F]),
        ("SeCo", [(D, [F, F]), (D, [F, F]), (D, [F, F]), (D, [F, F])]),
        ("OSCD", [(D, [F, F, F]), (D, [F, F, F]), F]),
        ("EuroSAT", [F, F]),
    ]),
    ("TerraTorch", [
        ("OpenSentinelMap", [F, F, F, F, F, F]),
        ("BurnIntensity", [F, F, F, F]),
        ("Sen4AgriNet", [F]),
    ]),
    ("torchange", [
        ("S2Looking", [F, F, F, F, F]),
        ("SECOND", [F, F, F, F]),
        ("xView2", [F, F, F, F]),
    ]),
    ("Ai2", [
        ("rslearn", [F, F, (D, [(D, [(D, [F]), (D, [F])]), (D, [(D, [F]), (D, [F])]), (D, [F])])]),
        ("SatlasPretrain", [(D, [(D, [F, F]), (D, [F, F])]), (D, [(D, [F, F])]), F, F, F]),
        ("OlmoEarth", [(D, [F, F, F]), (D, [F, F]), (D, [F]), (D, [F]), (D, [F]), (D, [F])]),
    ]),
]

CELL_W, CELL_H = 330, 300  # one wheel per cell, two by two
RADII = [26, 50, 72, 94, 116]  # sample ring, then one ring per depth, in px
SCALE = {"TorchGeo": 1.0, "TerraTorch": 0.95, "torchange": 0.95, "Ai2": 0.86}
LEAF_ARC = 11.5  # px between two neighbouring files of one dataset
PADDING = 30  # degrees of extra room around each dataset

INK, SAMPLE, FILE = "#000000", "#492ae8", "#f2c230"
FOLDER_FILL, FOLDER_LINE = "#fdf6dc", "#b8941e"
RING, EDGE, TEXT, FAINT = "#f1f0ec", "#c4c2bc", "#1d2025", "#5c6370"
MONO = "'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace"
SANS = "'League Spartan', Inter, -apple-system, 'Segoe UI', sans-serif"


def tree(children):
    nodes, edges = [["sample", 0.0, 0]], []

    def walk(items, parent, depth):
        for item in items:
            kind, sub = item if isinstance(item, tuple) else (item, None)
            nodes.append([kind, None, depth])
            index = len(nodes) - 1
            edges.append((parent, index))
            if sub:
                walk(sub, index, depth + 1)

    walk(children, 0, 1)
    leaves = []

    def collect(index):
        kids = [c for p, c in edges if p == index]
        if not kids:
            leaves.append(index)
        for kid in kids:
            collect(kid)

    collect(0)
    return nodes, edges, leaves


def gaps(nodes, leaves, radii):
    out = []
    for a, b in zip(leaves, leaves[1:]):
        radius = radii[max(1, min(nodes[a][2], nodes[b][2]) - 1)]
        out.append(degrees(LEAF_ARC / radius))
    return out


def place(nodes, edges, leaves, center, steps):
    angle = center + sum(steps) / 2
    for index, step in zip(leaves, [*steps, 0.0]):
        nodes[index][1] = angle
        angle -= step

    def mean(index):
        kids = [c for p, c in edges if p == index]
        if kids:
            nodes[index][1] = sum(mean(kid) for kid in kids) / len(kids)
        return nodes[index][1]

    mean(0)
    nodes[0][1] = center


def point(cx, cy, angle, radius):
    return cx + radius * cos(radians(angle)), cy - radius * sin(radians(angle))


def wheel(out, w, library, datasets):
    cx = (w % 2) * CELL_W + CELL_W / 2
    cy = (w // 2) * CELL_H + 138
    built = [tree(children) for _, children in datasets]
    depth = max(d for nodes, _, _ in built for _, _, d in nodes)
    radii = [r * SCALE[library] for r in RADII]
    steps = [gaps(nodes, leaves, radii) for nodes, _, leaves in built]
    weights = [sum(s) + PADDING for s in steps]
    spans = [360 * wt / sum(weights) for wt in weights]
    out.append(f'  <g aria-label="{library}">')
    for radius in radii[: depth + 1]:
        out.append(f'    <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.1f}" fill="none" stroke="{RING}" stroke-width="13"/>')
    start = 90.0
    marks, labels = [], []
    for (name, _), (nodes, edges, leaves), span, step in zip(datasets, built, spans, steps):
        place(nodes, edges, leaves, start - span / 2, step)
        xy = [point(cx, cy, angle, radii[d]) for _, angle, d in nodes]
        x0, y0 = xy[0]
        out.append(f'    <path d="M{cx:.1f} {cy:.1f}L{x0:.1f} {y0:.1f}" stroke="{EDGE}" stroke-width="1"/>')
        for p, c in edges:
            out.append(f'    <path d="M{xy[p][0]:.1f} {xy[p][1]:.1f}L{xy[c][0]:.1f} {xy[c][1]:.1f}" stroke="{EDGE}" stroke-width="1"/>')
        for (kind, _, _), (x, y) in zip(nodes, xy):
            if kind == "sample":
                marks.append(f'    <circle cx="{x:.1f}" cy="{y:.1f}" r="5.6" fill="{SAMPLE}"/>')
            elif kind == "folder":
                marks.append(f'    <circle cx="{x:.1f}" cy="{y:.1f}" r="3.8" fill="{FOLDER_FILL}" stroke="{FOLDER_LINE}" stroke-width="0.9" stroke-dasharray="1.6 1.2"/>')
            else:
                marks.append(f'    <circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{FILE}"/>')
        mid = start - span / 2
        lx, ly = point(cx, cy, mid, radii[depth] + 14)
        c = cos(radians(mid))
        anchor = "start" if c > 0.3 else "end" if c < -0.3 else "middle"
        ly += 4 if abs(c) > 0.3 else (10 if sin(radians(mid)) < 0 else -2)
        labels.append(f'    <text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-family="{MONO}" font-size="9.5" fill="{FAINT}">{name.upper()}</text>')
        start -= span
    out.extend(marks)
    out.append(f'    <circle cx="{cx:.1f}" cy="{cy:.1f}" r="9" fill="{INK}"/>')
    out.extend(labels)
    out.append(f'    <text x="{cx:.1f}" y="{cy + radii[depth] + 42:.1f}" text-anchor="middle" font-family="{SANS}" font-size="15" font-weight="600" fill="{TEXT}">{library}</text>')
    out.append("  </g>")


def main() -> None:
    height = 2 * CELL_H + 30
    out = [
        "<!-- Generated by docs/_build/trees.py. Edit the script, not this file. -->",
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {2 * CELL_W} {height}" width="{2 * CELL_W}" height="{height}">',
        f'  <rect width="{2 * CELL_W}" height="{height}" fill="#ffffff"/>',
    ]
    for w, (library, datasets) in enumerate(WHEELS):
        wheel(out, w, library, datasets)
    y = height - 18
    x = CELL_W - 200
    legend = [
        (f'<circle cx="{{x}}" cy="{y - 4}" r="5.6" fill="{SAMPLE}"/>', "sample"),
        (f'<circle cx="{{x}}" cy="{y - 4}" r="3.8" fill="{FOLDER_FILL}" stroke="{FOLDER_LINE}" stroke-width="0.9" stroke-dasharray="1.6 1.2"/>', "folder"),
        (f'<circle cx="{{x}}" cy="{y - 4}" r="3.2" fill="{FILE}"/>', "file"),
        (f'<rect x="{{x0}}" y="{y - 9}" width="20" height="10" fill="{RING}"/>', "one depth"),
    ]
    for symbol, text in legend:
        out.append("  " + symbol.format(x=x, x0=x - 10))
        out.append(f'  <text x="{x + 16}" y="{y}" font-family="{MONO}" font-size="11" fill="{TEXT}">{text}</text>')
        x += 100
    out.append("</svg>")
    target = Path(__file__).resolve().parent.parent / "assets" / "svg" / "dataset-trees.svg"
    target.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
