// The landing figure: a TACO dataset drawn as its parts, the catalog with the
// contract and one Parquet file per level, and the ZIP partitions that hold
// the files. Four steps, chosen with the arrows, the step names or the arrow
// keys, as on the cozip and GeoZL pages:
//   contract  what COLLECTION.json declares: the files of a sample and its fields
//   samples   the sample level read as a table, one row per sample
//   filter    the table filtered with SQL
//   read      only the files of the kept samples read, one range each
// Each step lights its part and dims the rest. taco.step(n) shows step n.
//
// Every value is real: Major TOM Core-DEM, read from Source Cooperative with
// the TACO reader. The six rows are samples 495678, 517242, 517478, 517479,
// 528375 and 528576 of that dataset, and the query keeps 492 of 1,735,044
// samples, all of them in partition 15 of 63, core-dem_MT3000km_0001D_0003L.zip.

(function (taco) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var W = 660;
  var H = 430;

  var VIOLET = '#492ae8';
  var TONES = ['#d8efd6', '#f3e6b3', '#f3e6b3', '#f3e6b3', '#d3e5f3', '#d3e5f3'];

  var OPEN = 'ds = <span class="rd-fn">taco.open_dataset</span>(<span class="rd-str">"https://data.source.coop/major-tom/core-dem/"</span>)';
  var STEPS = [
    { name: 'Contract', text: 'The collection declares *one contract*: the files of every sample and the fields of each level.', code: OPEN },
    { name: 'Samples', text: 'Each sample is +one row+, with its metadata and the location of its file.',
      code: 'samples = ds.<span class="rd-fn">read</span>()' },
    { name: 'Filter', text: 'Filter the samples *with SQL*, before reading any file.',
      code: 'peru = ds.<span class="rd-fn">sql</span>(query)' },
    { name: 'Read', text: 'Read +only the files you kept+, one byte range each.',
      code: '<span class="rd-fn">rumi.read</span>(row[<span class="rd-str">"dem.rumi::location"</span>], row[<span class="rd-str">"dem.rumi::header"</span>])' }
  ];

  // id, country, state, elevation in metres, offset and size of dem.rumi
  var ROWS = [
    ['MT10km_0003D_0833L', 'Peru', 'Loreto', 191, 10533559, 24509],
    ['MT10km_0100D_0854L', 'Peru', 'Ancash', 4787, 577605662, 48266],
    ['MT10km_0102D_0852L', 'Peru', 'Ancash', 4531, 583721039, 46237],
    ['MT10km_0102D_0853L', 'Peru', 'Ancash', 4817, 583767325, 49166],
    ['MT10km_0164D_0745L', 'Bolivia', 'La Paz', 4772, 872519459, 42666],
    ['MT10km_0165D_0745L', 'Bolivia', 'La Paz', 4661, 877514932, 41848]
  ];
  var PICKED = [1, 2, 3];   // Peru and above 4,500 m
  var ZIPS = 63;
  var HIT = 14;             // the partition that holds all 492 kept samples

  // the dataset on the left: the catalog, then the partitions
  var AX = 20;
  var AW = 160;
  var CAT = { y: 34, h: 124 };
  var BX = 30;
  var BW = 140;
  var BH = 26;
  var FILES = [
    { key: 'collection', y: 62, label: 'COLLECTION.json', sub: '23 kB' },
    { key: 'sample', y: 92, label: 'sample.parquet', sub: '37 MB' },
    { key: 'children', y: 122, label: 'children.parquet', sub: '33 MB' }
  ];
  var PART = { y: 172, h: 246 };
  var GRID = { x: 33, y: 200, pitch: 20, cell: 15, cols: 7 };

  // the panel on the right
  var TX = 200;
  var TW = 450;
  var TOP = 34;
  var ROW0 = 76;
  var ROW_STEP = 32;
  var COL = { chip: 210, id: 228, country: 368, elevation: 480, location: 494 };
  var LOCATION_CHARS = 21;
  var URL = 'https://data.source.coop/major-tom/core-dem/core-dem_MT3000km_0001D_0003L.zip';

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

  function group(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function rowY(i) {
    return ROW0 + i * ROW_STEP;
  }

  function cellXY(k) {
    return {
      x: GRID.x + (k % GRID.cols) * GRID.pitch,
      y: GRID.y + Math.floor(k / GRID.cols) * GRID.pitch
    };
  }

  function build(svg) {
    var v = {};

    var defs = el('defs', {}, svg);
    [['rd-head', VIOLET], ['rd-head-ink', '#000']].forEach(function (m) {
      var head = el('marker', { id: m[0], markerWidth: 8, markerHeight: 8, refX: 6.5, refY: 4, orient: 'auto' }, defs);
      el('path', { d: 'M0,0 L8,4 L0,8 Z', fill: m[1] }, head);
    });

    // the catalog: the collection and one Parquet file per level
    text(svg, AX, CAT.y - 12, 'Major TOM Core-DEM', 'rd-title');
    v.catalog = el('g', { 'class': 'rd-group' }, svg);
    el('rect', { 'class': 'rd-zip', x: AX, y: CAT.y, width: AW, height: CAT.h }, v.catalog);
    text(v.catalog, AX + 10, CAT.y + 18, '.tacocat/', 'rd-frame-label');
    v.files = {};
    FILES.forEach(function (f) {
      var g = el('g', { 'class': 'rd-block is-meta', style: '--tone:' + VIOLET }, v.catalog);
      el('rect', { 'class': 'rd-block-box', x: BX, y: f.y, width: BW, height: BH }, g);
      text(g, BX + 8, f.y + BH / 2 + 4, f.label, 'rd-block-label');
      text(g, BX + BW - 8, f.y + BH / 2 + 4, f.sub, 'rd-block-sub', 'end');
      v.files[f.key] = g;
    });

    // the partitions, 63 ZIP files that hold the DEM files
    v.partitions = el('g', {}, svg);
    el('rect', { 'class': 'rd-zip', x: AX, y: PART.y, width: AW, height: PART.h }, v.partitions);
    text(v.partitions, AX + 10, PART.y + 18, '63 ZIP partitions', 'rd-frame-label');
    v.cells = [];
    for (var k = 0; k < ZIPS; k++) {
      var p = cellXY(k);
      v.cells.push(el('rect', { 'class': 'rd-part', x: p.x, y: p.y, width: GRID.cell, height: GRID.cell }, v.partitions));
    }
    text(v.partitions, AX + 10, PART.y + PART.h - 12, '23.6 GB of DEM files', 'rd-frame-sub');

    // contract: what COLLECTION.json declares
    var tie = FILES[0].y + BH / 2;
    v.contract = el('g', { 'class': 'rd-panel' }, svg);
    el('rect', { 'class': 'rd-panel-box', x: TX, y: TOP, width: TW, height: 210 }, v.contract);
    el('path', { 'class': 'rd-panel-tie', d: 'M' + (BX + BW) + ' ' + tie + ' H' + TX }, v.contract);
    text(v.contract, TX + 14, TOP + 22, 'COLLECTION.json', 'rd-col');
    var FIELDS = [
      ['id', ['major-tom-core-dem'], ''],
      ['structure', ['dem.rumi'], 'one file per sample'],
      ['sample', ['id, spatial:centroid, majortom:code,', 'geoenrich:elevation,', 'geoenrich:admin_countries, ...'], '23 fields'],
      ['children', ['rumi:header, rumi:mean, ...'], '7 fields'],
      ['sources', ['63 ZIP partitions'], '1,735,044 samples']
    ];
    var y = TOP + 50;
    FIELDS.forEach(function (f, i) {
      if (i) el('path', { 'class': 'rd-panel-rule', d: 'M' + (TX + 14) + ' ' + (y - 17) + ' H' + (TX + TW - 14) }, v.contract);
      text(v.contract, TX + 14, y, f[0], 'rd-field-key');
      f[1].forEach(function (line, j) {
        text(v.contract, TX + 96, y + j * 18, line, 'rd-field-value' + (i === 1 || i === 2 ? ' is-key' : ''));
      });
      if (f[2]) text(v.contract, TX + TW - 14, y, f[2], 'rd-field-note', 'end');
      y += 27 + (f[1].length - 1) * 18;
    });

    // samples: the sample level as a table, one row per sample
    v.table = el('g', { 'class': 'rd-panel' }, svg);
    el('rect', { 'class': 'rd-panel-box', x: TX, y: TOP, width: TW, height: 232 }, v.table);
    v.tableTie = el('path', { 'class': 'rd-panel-tie', d: 'M' + (BX + BW) + ' ' + (FILES[1].y + BH / 2) + ' H' + TX }, v.table);
    text(v.table, COL.id, TOP + 20, 'id', 'rd-col');
    text(v.table, COL.country, TOP + 20, 'country', 'rd-col');
    text(v.table, COL.elevation, TOP + 20, 'elevation', 'rd-col', 'end');
    text(v.table, COL.location, TOP + 20, 'dem.rumi::location', 'rd-col');
    v.rows = ROWS.map(function (r, i) {
      var y = rowY(i);
      var g = el('g', { 'class': 'rd-row', style: '--tone:' + TONES[i] }, v.table);
      el('rect', { 'class': 'rd-pick', x: TX + 2, y: y - 14, width: TW - 4, height: 28 }, g);
      el('rect', { 'class': 'rd-chip', x: COL.chip, y: y - 5.5, width: 10, height: 10 }, g);
      text(g, COL.id, y + 4, r[0], 'rd-cell');
      text(g, COL.country, y + 4, r[1], 'rd-cell');
      text(g, COL.elevation, y + 4, group(r[3]) + ' m', 'rd-cell', 'end');
      // the reader builds the location from the offset, the size and the ZIP; it is never stored
      var location = '/vsisubfile/' + r[4] + '_' + r[5] + ',/vsicurl/' + URL;
      text(g, COL.location, y + 4, location.slice(0, LOCATION_CHARS) + '…', 'rd-cell rd-location');
      return g;
    });
    text(v.table, COL.id, TOP + 220, '... 1,735,044 rows, 23 fields each', 'rd-col');

    // filter: the query and what it keeps
    v.query = el('g', { 'class': 'rd-panel' }, svg);
    el('rect', { 'class': 'rd-query-box', x: TX, y: 286, width: TW, height: 58 }, v.query);
    text(v.query, TX + 14, 309, 'WHERE "geoenrich:admin_countries" = \'Peru\'', 'rd-query');
    text(v.query, TX + 14, 329, '  AND "geoenrich:elevation" > 4500', 'rd-query');
    v.kept = el('g', { 'class': 'rd-panel' }, svg);
    text(v.kept, TX, 376, '492 of 1,735,044 samples, in 14 ms', 'rd-say');
    text(v.kept, TX, 398, 'DuckDB on the sample metadata, on a laptop', 'rd-frame-sub');

    // read: one range each, all in one partition
    v.reads = PICKED.map(function (k) {
      var y = rowY(k);
      var c = cellXY(HIT);
      var x1 = c.x + GRID.cell + 3;
      var y1 = c.y + GRID.cell / 2;
      return el('path', {
        'class': 'rd-read',
        d: 'M' + (TX - 2) + ' ' + y + ' C' + (TX - 60) + ' ' + y + ' ' + (x1 + 70) + ' ' + y1 + ' ' + x1 + ' ' + y1,
        'marker-end': 'url(#rd-head-ink)'
      }, svg);
    });
    v.checks = PICKED.map(function (k) {
      return el('path', { 'class': 'rd-check', d: 'M' + (TX + TW + 3) + ' ' + rowY(k) + ' l3.5 3.5 l6 -7' }, svg);
    });
    v.got = el('g', { 'class': 'rd-panel' }, svg);
    text(v.got, TX, 376, '492 ranges, 16.5 MB, from one ZIP', 'rd-say');
    text(v.got, TX, 398, 'core-dem_MT3000km_0001D_0003L.zip, of 23.6 GB in all', 'rd-frame-sub');
    return v;
  }

  function set(node, cls, on) {
    if (node.classList.contains(cls) !== on) node.classList.toggle(cls, on);
  }

  function show(v, s) {
    var picked = function (i) { return PICKED.indexOf(i) >= 0; };

    set(v.files.collection, 'is-lit', s === 0);
    set(v.files.sample, 'is-lit', s === 1 || s === 2);
    set(v.files.children, 'is-dim', s >= 1);
    set(v.files.collection, 'is-dim', s >= 1);
    set(v.files.sample, 'is-dim', s === 3);
    set(v.contract, 'is-shown', s === 0);
    set(v.table, 'is-shown', s >= 1);
    set(v.tableTie, 'is-dim', s === 3);
    set(v.query, 'is-shown', s === 2);
    set(v.kept, 'is-shown', s === 2);
    set(v.got, 'is-shown', s === 3);

    v.cells.forEach(function (c, k) {
      set(c, 'is-dim', s === 3 && k !== HIT);
      set(c, 'is-hit', s === 3 && k === HIT);
    });
    v.rows.forEach(function (g, i) {
      set(g, 'is-picked', s >= 2 && picked(i));
      set(g, 'is-dim', s >= 2 && !picked(i));
    });
    v.reads.forEach(function (r) { set(r, 'is-shown', s === 3); });
    v.checks.forEach(function (c) { set(c, 'is-shown', s === 3); });
  }

  taco.readFigure = function (figure) {
    var head = document.createElement('div');
    head.className = 'read-step';
    head.innerHTML = '<span class="read-n"></span><p class="read-text"></p>';
    figure.appendChild(head);
    var code = document.createElement('code');
    code.className = 'read-code';
    figure.appendChild(code);
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': figure.getAttribute('data-label') || '' }, figure);

    // the carousel of the GeoZL page: previous, the four steps, next
    var nav = document.createElement('div');
    nav.className = 'read-carousel';
    nav.setAttribute('role', 'group');
    nav.setAttribute('aria-label', 'Steps of a read');
    var arrow = function (d, label) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'read-carousel__button';
      b.setAttribute('aria-label', label);
      b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + d + '"></path></svg>';
      return b;
    };
    var prev = arrow('m15 18-6-6 6-6', 'Previous step');
    var next = arrow('m9 18 6-6-6-6', 'Next step');
    var steps = document.createElement('div');
    steps.className = 'read-carousel__steps';
    var dots = STEPS.map(function (step, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'read-carousel__dot';
      b.textContent = step.name;
      b.setAttribute('aria-label', 'Step ' + (i + 1) + ', ' + step.name);
      b.addEventListener('click', function () { go(i); });
      steps.appendChild(b);
      return b;
    });
    var said = document.createElement('span');
    said.className = 'read-carousel__announcement';
    said.setAttribute('aria-live', 'polite');
    nav.appendChild(prev);
    nav.appendChild(steps);
    nav.appendChild(next);
    nav.appendChild(said);
    figure.appendChild(nav);

    var view = build(svg);
    var current = -1;

    function go(i) {
      i = (i + STEPS.length) % STEPS.length;
      if (i === current) return;
      current = i;
      head.querySelector('.read-n').textContent = String(i + 1);
      // the key phrase of a step: *words* take the yellow marker, +words+ the green one
      head.querySelector('.read-text').innerHTML = STEPS[i].text
        .replace(/\*(.+?)\*/g, '<mark class="read-mark">$1</mark>')
        .replace(/\+(.+?)\+/g, '<mark class="read-mark is-green">$1</mark>');
      code.innerHTML = STEPS[i].code;
      dots.forEach(function (d, j) {
        d.classList.toggle('is-active', j === i);
        if (j === i) d.setAttribute('aria-current', 'step');
        else d.removeAttribute('aria-current');
      });
      said.textContent = 'Step ' + (i + 1) + ' of ' + STEPS.length + ': ' + STEPS[i].text.replace(/[*+]/g, '');
      show(view, i);
    }

    prev.addEventListener('click', function () { go(current - 1); });
    next.addEventListener('click', function () { go(current + 1); });
    document.addEventListener('keydown', function (event) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.target.closest && event.target.closest('input, textarea, select, [contenteditable]')) return;
      if (event.key === 'ArrowLeft') go(current - 1);
      else if (event.key === 'ArrowRight') go(current + 1);
    });

    taco.step = function (n) { go(n); };
    go(0);
  };

  var figure = document.getElementById('read');
  if (figure) taco.readFigure(figure);
})((window.taco = window.taco || {}));
