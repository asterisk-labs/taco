// The landing figure: five public datasets, each stored its own way, pass
// through TACO and come out with the same shape. Pointing at a dataset, or
// focusing it with the keyboard, lights its path and shows how it is stored
// today and how it reads as a TACO dataset. Nothing moves on its own.
//
// The facts under "today" come from the TorchGeo dataset documentation,
// docs.torchgeo.org, version 0.10.1: its summary tables and the page of each
// dataset. The "as TACO" side is one way to package each dataset, an example
// and not a published dataset.

(function (taco) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var W = 660;
  var H = 432;

  var SETS = [
    {
      key: 'eurosat', name: 'EuroSAT', tone: '#d8efd6', format: 'a GeoTIFF', format2: 'per patch',
      task: 'land cover classification, 10 classes',
      sensor: 'Sentinel-2, 13 bands, 10 m, 64 x 64 px',
      samples: '27,000', license: 'MIT',
      files: '13-channel GeoTIFFs; each label is a class value from 0 to 9',
      structure: ['image.tif'], fields: ['split:split', 'eurosat:label']
    },
    {
      key: 'bigearthnet', name: 'BigEarthNet', tone: '#f3e6b3', format: 'a GeoTIFF per', format2: 'band, JSON labels',
      task: 'classification, 19 or 43 classes',
      sensor: 'Sentinel-1 and 2, SAR and MSI, 10 m, 120 x 120 px',
      samples: '590,326', license: 'CDLA-Permissive-1.0',
      files: 'one single-channel GeoTIFF per band, the labels in one JSON file per image, and the Sentinel-1 to Sentinel-2 match inside the Sentinel-1 JSON files',
      structure: ['s1.tif', 's2.tif'], fields: ['split:split', 'bigearthnet:labels']
    },
    {
      key: 'sen12ms', name: 'SEN12MS', tone: '#d3e5f3', format: 'a tar.gz per', format2: 'season and sensor',
      task: 'segmentation, 33 classes',
      sensor: 'Sentinel-1, Sentinel-2 and MODIS, 10 m, 256 x 256 px',
      samples: '180,662', license: 'CC-BY-4.0',
      files: '16-bit GeoTIFF triplets, downloaded as one tar.gz per season and source, with the splits in train_list.txt and test_list.txt',
      structure: ['s1.tif', 's2.tif', 'landcover.tif'], fields: ['split:split', 'sen12ms:season']
    },
    {
      key: 'so2sat', name: 'So2Sat', tone: '#f3dccd', format: 'HDF5 files', format2: 'per split',
      task: 'local climate zones, 17 classes',
      sensor: 'Sentinel-1 and 2, SAR and MSI, 10 m, 32 x 32 px',
      samples: '400,673', license: 'CC-BY-4.0',
      files: 'arrays in training.h5, validation.h5 and testing.h5',
      structure: ['s1.tif', 's2.tif'], fields: ['split:split', 'so2sat:lcz']
    },
    {
      key: 'levircd', name: 'LEVIR-CD', tone: '#f2d4dc', format: 'PNG pairs', format2: 'and PNG masks',
      task: 'change detection, 2 classes',
      sensor: 'Google Earth, RGB, 0.5 m, 1,024 x 1,024 px',
      samples: '637', license: 'not given by TorchGeo',
      files: 'three-channel PNGs, and single-channel PNG masks where change is 255',
      structure: ['before.png', 'after.png', 'mask.png'], fields: ['split:split']
    }
  ];

  var CARD = { w: 120, gap: 15, y: 22, h: 128 };
  var BOX = { y: 300, h: 98 };
  var BAND = { x: 40, y: 196, w: 580, h: 58 };

  function el(tag, attrs, parent) {
    var node = document.createElementNS(NS, tag);
    for (var key in attrs) node.setAttribute(key, attrs[key]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function text(parent, x, y, value, cls, anchor) {
    var node = el('text', { x: x, y: y, 'class': cls }, parent);
    if (anchor) node.setAttribute('text-anchor', anchor);
    node.textContent = value;
    return node;
  }

  function cardX(i) {
    return i * (CARD.w + CARD.gap);
  }

  // where the path of dataset i meets the TACO band
  function gateX(i) {
    return W / 2 + (i - 2) * 46;
  }

  // The pile each dataset is today, drawn in the box from (x, y), 100 by 62.
  var PILES = {
    eurosat: function (g, x, y) {
      var greens = ['#9cc58f', '#c9b27c', '#6f9f64', '#d7cf9a', '#86b0c9', '#b5c98c'];
      for (var r = 0; r < 4; r++) {
        for (var c = 0; c < 6; c++) {
          var k = (r * 7 + c * 3) % greens.length;
          el('rect', { x: x + c * 16 + (r % 2) * 3, y: y + r * 15 + (c % 3), width: 12, height: 12, fill: greens[k], 'class': 'fl-ink-thin' }, g);
        }
      }
    },
    bigearthnet: function (g, x, y) {
      for (var b = 0; b < 6; b++) {
        el('rect', { x: x + b * 6, y: y + 4 + b * 4, width: 44, height: 36, fill: b % 2 ? '#e8e0c4' : '#f8f2da', 'class': 'fl-ink-thin' }, g);
      }
      el('rect', { x: x + 70, y: y + 8, width: 28, height: 36, fill: '#fff', 'class': 'fl-ink-thin' }, g);
      text(g, x + 84, y + 31, '{ }', 'fl-glyph', 'middle');
      el('rect', { x: x + 62, y: y + 40, width: 28, height: 22, fill: '#fff', 'class': 'fl-ink-thin', transform: 'rotate(-8 ' + (x + 76) + ' ' + (y + 51) + ')' }, g);
      text(g, x + 76, y + 56, '{ }', 'fl-glyph', 'middle');
    },
    sen12ms: function (g, x, y) {
      [['s1', 0, 6, -4], ['s2', 34, 0, 3], ['lc', 68, 10, -2]].forEach(function (a) {
        var bx = x + a[1];
        var by = y + a[2];
        var t = 'rotate(' + a[3] + ' ' + (bx + 15) + ' ' + (by + 24) + ')';
        var box = el('g', { transform: t }, g);
        el('rect', { x: bx, y: by, width: 30, height: 46, fill: '#c7dcef', 'class': 'fl-ink-thin' }, box);
        el('rect', { x: bx, y: by + 10, width: 30, height: 6, fill: '#7fa8cf' }, box);
        text(box, bx + 15, by + 34, a[0], 'fl-glyph', 'middle');
      });
    },
    so2sat: function (g, x, y) {
      [[4, 'training'], [38, 'validation'], [72, 'testing']].forEach(function (a, k) {
        var cx = x + a[0] + 12;
        var top = y + 8 + (k % 2) * 6;
        el('path', { d: 'M' + (cx - 12) + ' ' + top + ' v30 a12 5 0 0 0 24 0 v-30', fill: '#f0cdb8', 'class': 'fl-ink-thin' }, g);
        el('ellipse', { cx: cx, cy: top, rx: 12, ry: 5, fill: '#f7e1d3', 'class': 'fl-ink-thin' }, g);
        text(g, cx, top + 24, 'h5', 'fl-glyph', 'middle');
      });
    },
    levircd: function (g, x, y) {
      el('rect', { x: x, y: y + 4, width: 34, height: 34, fill: '#c9c2b4', 'class': 'fl-ink-thin', transform: 'rotate(-4 ' + (x + 17) + ' ' + (y + 21) + ')' }, g);
      el('rect', { x: x + 8, y: y + 26, width: 10, height: 8, fill: '#8f8676' }, g);
      el('rect', { x: x + 38, y: y + 8, width: 34, height: 34, fill: '#c9c2b4', 'class': 'fl-ink-thin', transform: 'rotate(3 ' + (x + 55) + ' ' + (y + 25) + ')' }, g);
      el('rect', { x: x + 44, y: y + 28, width: 10, height: 8, fill: '#8f8676' }, g);
      el('rect', { x: x + 58, y: y + 14, width: 10, height: 12, fill: '#a65d4f' }, g);
      el('rect', { x: x + 70, y: y + 32, width: 30, height: 30, fill: '#111', 'class': 'fl-ink-thin', transform: 'rotate(-6 ' + (x + 85) + ' ' + (y + 47) + ')' }, g);
      el('rect', { x: x + 82, y: y + 38, width: 9, height: 11, fill: '#fff' }, g);
    }
  };

  function build(svg) {
    var v = { sets: [] };
    var defs = el('defs', {}, svg);
    [['fl-head', '#b8bac2'], ['fl-head-on', '#492ae8']].forEach(function (m) {
      var head = el('marker', { id: m[0], markerWidth: 8, markerHeight: 8, refX: 6.5, refY: 4, orient: 'auto' }, defs);
      el('path', { d: 'M0,0 L8,4 L0,8 Z', fill: m[1] }, head);
    });

    text(svg, 0, 12, 'five public datasets, five layouts', 'fl-ruler');
    text(svg, 0, BOX.y - 8, 'the same datasets in TACO', 'fl-ruler');

    // the TACO band in the middle
    var band = el('g', { 'class': 'fl-band' }, svg);
    el('rect', { 'class': 'fl-band-box', x: BAND.x, y: BAND.y, width: BAND.w, height: BAND.h }, band);
    el('image', { href: 'assets/svg/taco-mark-static.svg?v=1', x: BAND.x + 14, y: BAND.y + 9, width: 40, height: 40 }, band);
    text(band, BAND.x + 64, BAND.y + 26, 'TACO', 'fl-band-title');
    text(band, BAND.x + 64, BAND.y + 44, 'one contract per dataset: the files of every sample and the fields of each level', 'fl-band-sub');

    SETS.forEach(function (set, i) {
      var x = cardX(i);
      var g = el('g', {
        'class': 'fl-set', tabindex: '0', role: 'button', 'data-set': set.key,
        'aria-label': set.name + ', ' + set.samples + ' samples. Show how it is stored today and as TACO.'
      }, svg);

      // today: a pile in its own format
      el('rect', { 'class': 'fl-card', x: x, y: CARD.y, width: CARD.w, height: CARD.h, fill: set.tone }, g);
      text(g, x + 10, CARD.y + 20, set.name, 'fl-name');
      PILES[set.key](g, x + 10, CARD.y + 30);
      text(g, x + 10, CARD.y + 106, set.format, 'fl-format');
      text(g, x + 10, CARD.y + 119, set.format2, 'fl-format');

      // into TACO and out again
      var cx = x + CARD.w / 2;
      var gx = gateX(i);
      el('path', { 'class': 'fl-path', d: 'M' + cx + ' ' + (CARD.y + CARD.h) + ' C' + cx + ' ' + (BAND.y - 18) + ' ' + gx + ' ' + (CARD.y + CARD.h + 10) + ' ' + gx + ' ' + (BAND.y - 3), 'marker-end': 'url(#fl-head)' }, g);
      el('path', { 'class': 'fl-path', d: 'M' + gx + ' ' + (BAND.y + BAND.h) + ' C' + gx + ' ' + (BOX.y - 14) + ' ' + cx + ' ' + (BAND.y + BAND.h + 12) + ' ' + cx + ' ' + (BOX.y - 3), 'marker-end': 'url(#fl-head)' }, g);

      // as TACO: every dataset the same shape, one row per sample
      el('rect', { 'class': 'fl-box', x: x, y: BOX.y, width: CARD.w, height: BOX.h }, g);
      text(g, x + 10, BOX.y + 19, set.name, 'fl-name');
      for (var r = 0; r < 4; r++) {
        var ry = BOX.y + 29 + r * 12;
        el('rect', { 'class': 'fl-chip', x: x + 10, y: ry, width: 8, height: 8, fill: set.tone }, g);
        el('rect', { 'class': 'fl-cell', x: x + 22, y: ry + 1, width: 34, height: 6 }, g);
        el('rect', { 'class': 'fl-cell', x: x + 60, y: ry + 1, width: 22, height: 6 }, g);
        el('rect', { 'class': 'fl-cell is-file', x: x + 86, y: ry + 1, width: 24, height: 6 }, g);
      }
      text(g, x + 10, BOX.y + 89, set.samples + ' samples', 'fl-format');
      v.sets.push(g);
    });

    text(svg, 0, H - 6, 'taco.open_dataset(source)  reads any of them, and the same SQL filters them', 'fl-code');
    return v;
  }

  function row(label, value) {
    return '<dt>' + label + '</dt><dd>' + value + '</dd>';
  }

  function code(names) {
    return names.map(function (n) { return '<code>' + n + '</code>'; }).join(', ');
  }

  function describe(set) {
    return '<p class="flow-title"><b>' + set.name + '</b>, ' + set.task + '</p>' +
      '<div class="flow-cols">' +
      '<div><p class="flow-k">Today</p><dl>' +
      row('sensor', set.sensor) +
      row('samples', set.samples) +
      row('files', set.files) +
      row('license', set.license) +
      '</dl></div>' +
      '<div><p class="flow-k is-taco">As TACO</p><dl>' +
      row('a sample', code(set.structure)) +
      row('fields', code(['id'].concat(set.fields))) +
      row('rows', set.samples + ' in <code>sample.parquet</code>') +
      row('read', '<code>taco.open_dataset</code>, then SQL') +
      '</dl></div></div>';
  }

  var HINT = '<p class="flow-hint">Each dataset is stored its own way today. Point at one, tap it or tab to it to see its files and how they would read as a TACO dataset: one sample per row, with its files beside it. Facts from the <a href="https://docs.torchgeo.org/en/stable/api/datasets.html" rel="noopener noreferrer">TorchGeo dataset docs</a>.</p>';

  taco.flowFigure = function (figure) {
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'group', 'aria-label': figure.getAttribute('data-label') || '' }, figure);
    var info = document.createElement('div');
    info.className = 'flow-info';
    info.setAttribute('aria-live', 'polite');
    figure.appendChild(info);
    var view = build(svg);
    var pinned = null;

    function show(key) {
      var set = SETS.filter(function (s) { return s.key === key; })[0];
      if (set) figure.setAttribute('data-active', key);
      else figure.removeAttribute('data-active');
      view.sets.forEach(function (g) {
        var on = g.getAttribute('data-set') === key;
        g.classList.toggle('is-active', on);
        g.querySelectorAll('.fl-path').forEach(function (path) {
          path.setAttribute('marker-end', on ? 'url(#fl-head-on)' : 'url(#fl-head)');
        });
      });
      info.innerHTML = set ? describe(set) : HINT;
    }

    view.sets.forEach(function (g) {
      var key = g.getAttribute('data-set');
      g.addEventListener('mouseenter', function () { show(key); });
      g.addEventListener('mouseleave', function () { show(pinned); });
      g.addEventListener('focus', function () { show(key); });
      g.addEventListener('blur', function () { show(pinned); });
      // a tap or a click keeps a dataset shown, a second one lets it go
      g.addEventListener('click', function () {
        pinned = pinned === key ? null : key;
        show(pinned || key);
      });
      g.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          pinned = pinned === key ? null : key;
          show(pinned || key);
        }
      });
    });
    show(null);
  };

  var figure = document.getElementById('flow');
  if (figure) taco.flowFigure(figure);
})((window.taco = window.taco || {}));
