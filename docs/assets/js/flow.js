// The landing figure: forty public datasets, each stored its own way, pour
// through TACO and come out in one shape. Rasters become Rumi files, which
// decode on a CPU or a GPU; tables become Parquet and JSON stays JSON. Every
// dataset is a tile; pointing at one or focusing it lights it,
// draws its way through TACO and fills the table and the panel below.
// Nothing moves on its own.
//
// The facts come from the TorchGeo dataset documentation, docs.torchgeo.org,
// version 0.10.1: the row of each dataset in its summary tables, and the file
// formats its own page states. CloudSEN12 is not in TorchGeo: its facts come
// from the tacofoundation/cloudsen12 card on Hugging Face, and its sample
// count from the CloudSEN12Plus viewer there. Only datasets with a license
// are listed; NA marks another cell the docs leave empty. The TACO side is one
// way to package each dataset, an example and not a published one.

(function (taco) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var W = 660;
  var H = 432;

  var TASKS = {
    C: 'classification', S: 'segmentation', CD: 'change detection', OD: 'object detection',
    I: 'instance segmentation', R: 'regression', T: 'time series'
  };
  var TYPES = { rumi: '#492ae8', table: '#f7cc09', json: '#c4ffc2' };
  // each tile takes the tint of its first format, so the mosaic shows how
  // mixed the formats are
  var TINTS = {
    tif: '#d8efd6', bands: '#d8efd6', png: '#f2d4dc', jpg: '#d3e5f3', jp2: '#d3e5f3',
    h5: '#f3dccd', npy: '#e4e2f3', npz: '#e4e2f3', nc: '#e2eed8', tar: '#dbe6f3',
    json: '#f3e6b3', geojson: '#f3e6b3', csv: '#f3e6b3', xml: '#f3e6b3', shp: '#e7f0dc'
  };
  // name, task codes, source, samples, classes, size in px, resolution in m,
  // bands, license, the formats today, the glyphs that draw them, and the files
  // of one sample in TACO
  var POOL = [
    ['CloudSEN12', 'S', 'Sentinel-2 L1C and L2A', '50.2K', '4', '509x509, 2,000x2,000', '10-60', 'MSI', 'CC0-1.0', 'GeoTIFF copies on ScienceDataBank', ['tif'], ['l1c.rumi', 'l2a.rumi', 'label.rumi']],
    ['EuroSAT', 'C', 'Sentinel-2', '27,000', '10', '64x64', '10', 'MSI', 'MIT', '13-channel GeoTIFF', ['tif'], ['image.rumi']],
    ['BigEarthNet', 'C', 'Sentinel-1/2', '590,326', '19-43', '120x120', '10', 'SAR, MSI', 'CDLA-Permissive-1.0', 'a GeoTIFF per band, a JSON per image', ['bands', 'json'], ['s1.rumi', 's2.rumi', 'labels.json']],
    ['SEN12MS', 'S', 'Sentinel-1/2, MODIS', '180,662', '33', '256x256', '10', 'SAR, MSI', 'CC-BY-4.0', 'GeoTIFF triplets in tar.gz archives', ['tar'], ['s1.rumi', 's2.rumi', 'landcover.rumi']],
    ['So2Sat', 'C', 'Sentinel-1/2', '400,673', '17', '32x32', '10', 'SAR, MSI', 'CC-BY-4.0', 'HDF5', ['h5'], ['s1.rumi', 's2.rumi']],
    ['OSCD', 'CD', 'Sentinel-2', '24', '2', '241-1,180', '60', 'MSI', 'CC-BY-NC-SA-4.0', '13-channel TIFF, PNG masks', ['tif', 'png'], ['before.rumi', 'after.rumi', 'mask.rumi']],
    ['xBD', 'CD', 'Maxar', '3,732', '4', '1,024x1,024', '0.8', 'RGB', 'CC-BY-NC-SA-4.0', 'PNG images and masks', ['png'], ['pre.rumi', 'post.rumi', 'damage.rumi']],
    ['Cloud Cover Detection', 'S', 'Sentinel-2', '22,728', '2', '512x512', '10', 'MSI', 'CC-BY-4.0', 'GeoTIFF images and labels', ['tif'], ['image.rumi', 'clouds.rumi'], 'Cloud Cover'],
    ['RESISC45', 'C', 'Google Earth', '31,500', '45', '256x256', '0.2-30', 'RGB', 'CC-BY-NC-4.0', 'JPEG', ['jpg'], ['image.rumi']],
    ['LoveDA', 'S', 'Google Earth', '5,987', '7', '1,024x1,024', '0.3', 'RGB', 'CC-BY-NC-SA-4.0', 'PNG images and masks', ['png'], ['image.rumi', 'mask.rumi']],
    ['SpaceNet 1', 'I', 'WorldView-2', '9,735', 'NA', '406x439', '0.5-1', 'RGB, MSI', 'CC-BY-SA-4.0', 'GeoTIFF, GeoJSON labels', ['tif', 'geojson'], ['rgb.rumi', 'msi.rumi', 'labels.geojson']],
    ['SpaceNet 2', 'I', 'WorldView-3', '14,119', 'NA', '650x650', '0.3-1.24', 'RGB, MSI', 'CC-BY-SA-4.0', 'GeoTIFF, GeoJSON labels', ['tif', 'geojson'], ['rgb.rumi', 'msi.rumi', 'labels.geojson']],
    ['SpaceNet 7', 'I', 'Planet Dove', '1,889', 'NA', '1,024x1,024', '4', 'RGB', 'CC-BY-SA-4.0', 'GeoTIFF mosaics, GeoJSON labels', ['tif', 'geojson'], ['mosaic.rumi', 'labels.geojson']],
    ['SeasoNet', 'S', 'Sentinel-2', '1,759,830', '33', '120x120', '10', 'MSI', 'CC-BY-4.0', 'a GeoTIFF per resolution', ['bands'], ['bands_10m.rumi', 'bands_20m.rumi', 'bands_60m.rumi', 'mask.rumi']],
    ['FAIR1M', 'OD', 'Gaofen, Google Earth', '15,000', '37', '1,024x1,024', '0.3-0.8', 'RGB', 'CC-BY-NC-SA-3.0', 'TIFF, XML boxes', ['tif', 'xml'], ['image.rumi', 'boxes.parquet']],
    ['DFC2022', 'S', 'Aerial', '3,981', '15', '2,000x2,000', '0.5', 'RGB', 'CC-BY-4.0', 'GeoTIFF images, DEMs and masks', ['tif'], ['image.rumi', 'dem.rumi', 'mask.rumi']],
    ['CaBuAr', 'CD', 'Sentinel-2', '424', '2', '512x512', '20', 'MSI', 'OpenRAIL', 'one HDF5', ['h5'], ['before.rumi', 'after.rumi', 'mask.rumi']],
    ['ChaBuD', 'CD', 'Sentinel-2', '356', '2', '512x512', '10', 'MSI', 'OpenRAIL', 'one HDF5', ['h5'], ['before.rumi', 'after.rumi', 'mask.rumi']],
    ['ReforesTree', 'OD', 'Aerial', '100', '6', '4,000x4,000', '0.02', 'RGB', 'CC-BY-4.0', 'PNG, CSV annotations', ['png', 'csv'], ['image.rumi', 'trees.parquet']],
    ['EverWatch', 'OD', 'Aerial', '5,325', '8', '1,500x1,500', '0.01', 'RGB', 'CC0-1.0', 'PNG, CSV annotations', ['png', 'csv'], ['image.rumi', 'birds.parquet']],
    ['Western USA Live Fuel Moisture', 'R', 'Landsat 8, Sentinel-1', '2,615', 'NA', 'NA', 'NA', 'NA', 'CC-BY-NC-ND-4.0', 'a GeoJSON per point', ['geojson'], ['point.geojson'], 'Fuel Moisture'],
    ['Forest Damage', 'OD', 'Drone', '1,543', '4', '1,500x1,500', 'NA', 'RGB', 'CDLA-Permissive-1.0', 'JPEG, XML boxes', ['jpg', 'xml'], ['image.rumi', 'boxes.parquet']],
    ['SKIPP’D', 'R', 'Fish-eye camera', '363,375', 'NA', '64x64', 'NA', 'RGB', 'CC-BY-4.0', 'HDF5, NPY timestamps', ['h5', 'npy'], ['sky.rumi']],
    ['Substation', 'S', 'OpenStreetMap, Sentinel-2', '27K', '2', '228x228', '10', 'MSI', 'CC-BY-4.0', 'an NPZ per point', ['npz'], ['image.rumi', 'mask.rumi']],
    ['MMEarth', 'C', 'Aster, Sentinel, ERA5', '100K-1M', 'NA', '128x128', '10', 'MSI', 'CC-BY-4.0', 'one HDF5, JSON metadata', ['h5', 'json'], ['s1.rumi', 's2.rumi', 'aster.rumi', 'stats.json']],
    ['Digital Typhoon', 'C', 'Himawari', '189,364', '8', '512', '5000', 'Infrared', 'CC-BY-4.0', 'HDF5, CSV metadata', ['h5', 'csv'], ['infrared.rumi', 'track.parquet']],
    ['CaFFe', 'S', 'Sentinel-1, TerraSAR-X, TanDEM-X, ENVISAT, ERS-1/2, ALOS PALSAR, RADARSAT-1', '19,092', '2 or 4', '512x512', '6-20', 'SAR', 'CC-BY-4.0', 'PNG images and masks', ['png'], ['image.rumi', 'mask.rumi']],
    ['SSL4EO-L', 'T', 'Landsat', '1M', 'NA', '264x264', '30', 'MSI', 'CC0-1.0', 'a GeoTIFF per patch', ['tif'], ['image.rumi']],
    ['DL4GAM', 'S', 'Sentinel-2', '2,251 or 11,440', '2', '256x256', '10', 'MSI', 'CC-BY-4.0', 'NetCDF stacks of images and masks', ['nc'], ['image.rumi', 'mask.rumi']],
    ['FireRisk', 'C', 'NAIP aerial', '91,872', '7', '320x320', '1', 'RGB', 'CC-BY-NC-4.0', 'PNG', ['png'], ['image.rumi']],
    ['GeoNRW', 'S', 'Aerial', '7,783', '11', '1,000x1,000', '1', 'RGB, DEM', 'CC-BY-4.0', 'JP2 images, TIFF DEMs and labels', ['jp2', 'tif'], ['rgb.rumi', 'dem.rumi', 'labels.rumi']],
    ['IDTReeS', 'OD', 'Aerial', '591', '33', '200x200', '0.1-1', 'RGB', 'CC-BY-4.0', 'GeoTIFF, shapefile crowns, CSV species', ['tif', 'shp'], ['rgb.rumi', 'crowns.parquet']],
    ['LandCover.ai', 'S', 'Aerial', '10,674', '5', '512x512', '0.25-0.5', 'RGB', 'CC-BY-NC-SA-4.0', 'JPG chips, PNG masks', ['jpg', 'png'], ['image.rumi', 'mask.rumi']],
    ['NASA Marine Debris', 'OD', 'PlanetScope', '707', '1', '256x256', '3', 'RGB', 'Apache-2.0', 'GeoTIFF, NPY boxes', ['tif', 'npy'], ['image.rumi', 'boxes.parquet'], 'Marine Debris'],
    ['PASTIS', 'I', 'Sentinel-1/2', '2,433', '19', '128x128 per date', '10', 'MSI', 'CC-BY-4.0', 'NPY time series and annotations', ['npy'], ['s2.rumi', 'labels.rumi']],
    ['PatternNet', 'C', 'Google Earth', '30,400', '38', '256x256', '0.06-5', 'RGB', 'CC-BY-4.0', 'JPEG', ['jpg'], ['image.rumi']],
    ['QuakeSet', 'C, R', 'Sentinel-1', '3,327', '2', '512x512', '10', 'SAR', 'OpenRAIL', 'one HDF5', ['h5'], ['sar.rumi']],
    ['USAVars', 'R', 'NAIP aerial', '100K', 'NA', 'NA', '4', 'RGB, NIR', 'CC-BY-4.0', '4-channel GeoTIFF', ['tif'], ['image.rumi']],
    ['ZueriCrop', 'I', 'Sentinel-2', '116K', '48', '24x24', '10', 'MSI', 'CC-BY-NC-4.0', 'one HDF5', ['h5'], ['s2.rumi', 'mask.rumi', 'instances.rumi']],
    ['SustainBench Crop Yield', 'R', 'MODIS', '11K', 'NA', '32x32', 'NA', 'MSI', 'CC-BY-SA-4.0', 'NPZ stacks', ['npz'], ['image.rumi'], 'Crop Yield']
  ].map(function (d) {
    return {
      name: d[0], short: d[12] || d[0],
      task: d[1].split(', ').map(function (t) { return TASKS[t]; }).join(', '),
      source: d[2], samples: d[3], classes: d[4], size: d[5], res: d[6], bands: d[7], license: d[8],
      today: d[9], glyphs: d[10], files: d[11]
    };
  });

  var TILE = { w: 58, h: 40, pitch: 66, row: 48, x: 4, y: 6, cols: 10 };
  var HUB = { x: 330, y: 262, r: 34 };
  var PORTS = [['rumi', 'Rumi', 242], ['table', 'Parquet', 330], ['json', 'JSON', 418]];
  var TABLE = { x: 190, y: 338, w: 280, h: 90 };

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

  function fileType(name) {
    if (/\.rumi$/.test(name)) return 'rumi';
    if (/\.parquet$/.test(name)) return 'table';
    return 'json';
  }

  // the forty tiles in a new order on every load
  function shuffled() {
    var pool = POOL.slice();
    for (var i = pool.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = pool[i]; pool[i] = pool[j]; pool[j] = t;
    }
    return pool;
  }

  function doc(g, x, y, mark) {
    el('path', { d: 'M' + x + ' ' + y + ' h12 l5 5 v17 h-17 Z', fill: '#fff', 'class': 'fl-thin' }, g);
    text(g, x + 8.5, y + 15, mark, 'fl-glyph', 'middle');
  }

  // One small glyph per format, drawn in a 22 by 22 box from (x, y).
  var GLYPHS = {
    tif: function (g, x, y) {
      var tones = ['#9cc58f', '#c9b27c', '#6f9f64', '#86b0c9'];
      for (var k = 0; k < 4; k++) {
        el('rect', { x: x + (k % 2) * 11, y: y + Math.floor(k / 2) * 11, width: 9, height: 9, fill: tones[k], 'class': 'fl-thin' }, g);
      }
    },
    bands: function (g, x, y) {
      for (var b = 0; b < 3; b++) el('rect', { x: x + b * 3, y: y + b * 3, width: 15, height: 15, fill: b % 2 ? '#e8e0c4' : '#f8f2da', 'class': 'fl-thin' }, g);
    },
    png: function (g, x, y) {
      el('rect', { x: x, y: y, width: 14, height: 14, fill: '#c9c2b4', 'class': 'fl-thin' }, g);
      el('path', { d: 'M' + x + ' ' + (y + 14) + ' l5 -6 l4 3 l5 -4 v7 Z', fill: '#8f8676' }, g);
      el('rect', { x: x + 9, y: y + 9, width: 12, height: 12, fill: '#111', 'class': 'fl-thin' }, g);
      el('rect', { x: x + 13, y: y + 12, width: 4, height: 5, fill: '#fff' }, g);
    },
    jpg: function (g, x, y) {
      el('rect', { x: x, y: y + 2, width: 21, height: 17, fill: '#bcd4e6', 'class': 'fl-thin' }, g);
      el('circle', { cx: x + 15, cy: y + 7, r: 2.2, fill: '#f7cc09' }, g);
      el('path', { d: 'M' + x + ' ' + (y + 19) + ' l8 -8 l13 8 Z', fill: '#7a9a6c' }, g);
    },
    h5: function (g, x, y) {
      el('path', { d: 'M' + (x + 2) + ' ' + (y + 4) + ' v13 a8 3.2 0 0 0 16 0 v-13', fill: '#f0cdb8', 'class': 'fl-thin' }, g);
      el('ellipse', { cx: x + 10, cy: y + 4, rx: 8, ry: 3.2, fill: '#f7e1d3', 'class': 'fl-thin' }, g);
    },
    nc: function (g, x, y) {
      for (var b = 0; b < 3; b++) el('rect', { x: x + 6 - b * 3, y: y + b * 4, width: 15, height: 11, fill: '#dfe9d6', 'class': 'fl-thin' }, g);
    },
    tar: function (g, x, y) {
      el('rect', { x: x + 3, y: y, width: 15, height: 21, fill: '#c7dcef', 'class': 'fl-thin' }, g);
      el('rect', { x: x + 3, y: y + 4, width: 15, height: 3, fill: '#7fa8cf' }, g);
    },
    json: function (g, x, y) { doc(g, x, y, '{}'); },
    geojson: function (g, x, y) { doc(g, x, y, '{}'); },
    xml: function (g, x, y) { doc(g, x, y, '<>'); },
    npy: function (g, x, y) { doc(g, x, y, '[]'); },
    npz: function (g, x, y) { doc(g, x, y, '[]'); },
    csv: function (g, x, y) {
      el('rect', { x: x + 1, y: y + 1, width: 18, height: 19, fill: '#fff', 'class': 'fl-thin' }, g);
      el('path', { d: 'M' + (x + 1) + ' ' + (y + 7.5) + ' h18 M' + (x + 1) + ' ' + (y + 14) + ' h18 M' + (x + 8) + ' ' + (y + 1) + ' v19', 'class': 'fl-rule' }, g);
    },
    shp: function (g, x, y) {
      el('path', { d: 'M' + (x + 1) + ' ' + (y + 14) + ' l5 -12 l9 4 l4 10 l-8 6 Z', fill: '#e7f0dc', 'class': 'fl-thin' }, g);
    },
    jp2: function (g, x, y) { GLYPHS.jpg(g, x, y); }
  };

  function build(svg, sets) {
    var v = { tiles: [] };

    // the funnel the tiles pour into
    var top = TILE.y + 4 * TILE.row + 2;
    el('path', {
      'class': 'fl-funnel',
      d: 'M0 ' + top + ' C200 ' + top + ' 296 ' + (top + 10) + ' ' + (HUB.x - 26) + ' ' + (HUB.y - HUB.r + 6) +
        ' L' + (HUB.x + 26) + ' ' + (HUB.y - HUB.r + 6) + ' C364 ' + (top + 10) + ' 460 ' + top + ' 660 ' + top + ' Z'
    }, svg);

    // the way of the dataset that is shown, through TACO
    v.way = el('path', { 'class': 'fl-way' }, svg);
    v.wayDot = el('circle', { 'class': 'fl-way-dot', r: 3.5 }, svg);

    // the tiles, a little out of line, as they come
    sets.forEach(function (set, i) {
      var col = i % TILE.cols;
      var row = Math.floor(i / TILE.cols);
      var x = TILE.x + col * TILE.pitch;
      var y = TILE.y + row * TILE.row;
      var turn = ((i * 37) % 9) - 4;
      var g = el('g', {
        'class': 'fl-tile', tabindex: '0', 'data-set': String(i),
        'aria-label': set.name + ', ' + set.samples + ' samples'
      }, svg);
      var inner = el('g', { transform: 'rotate(' + turn + ' ' + (x + TILE.w / 2) + ' ' + (y + TILE.h / 2) + ')' }, g);
      el('rect', { 'class': 'fl-tile-box', x: x, y: y, width: TILE.w, height: TILE.h, fill: TINTS[set.glyphs[0]] }, inner);
      if (set.glyphs.length === 1) GLYPHS[set.glyphs[0]](inner, x + 18, y + 9);
      else {
        GLYPHS[set.glyphs[0]](inner, x + 6, y + 9);
        GLYPHS[set.glyphs[1]](inner, x + 31, y + 9);
      }
      v.tiles.push({ g: g, x: x + TILE.w / 2, y: y + TILE.h });
    });

    // TACO, where every dataset goes in
    var hub = el('g', { 'class': 'fl-hub' }, svg);
    el('circle', { 'class': 'fl-hub-ring', cx: HUB.x, cy: HUB.y, r: HUB.r + 7 }, hub);
    el('circle', { 'class': 'fl-hub-disc', cx: HUB.x, cy: HUB.y, r: HUB.r }, hub);
    el('image', { href: 'assets/svg/taco-mark-static.svg?v=1', x: HUB.x - 25, y: HUB.y - 27, width: 50, height: 50 }, hub);
    text(hub, HUB.x + HUB.r + 18, HUB.y + 7, 'TACO', 'fl-hub-name');

    // what comes out: rasters as Rumi, tables as Parquet, JSON as JSON
    v.ports = PORTS.map(function (p) {
      var g = el('g', { 'class': 'fl-port', 'data-type': p[0] }, svg);
      el('path', { 'class': 'fl-port-line', d: 'M' + HUB.x + ' ' + (HUB.y + HUB.r + 7) + ' C' + HUB.x + ' ' + (HUB.y + HUB.r + 26) + ' ' + p[2] + ' ' + (HUB.y + HUB.r + 12) + ' ' + p[2] + ' ' + (TABLE.y - 22) }, g);
      el('circle', { 'class': 'fl-port-dot', cx: p[2], cy: TABLE.y - 16, r: 6, fill: TYPES[p[0]] }, g);
      text(g, p[2] + 11, TABLE.y - 12, p[1], 'fl-port-name');
      return g;
    });

    // one row per sample, the same for every dataset
    var table = el('g', { 'class': 'fl-table' }, svg);
    el('rect', { 'class': 'fl-table-box', x: TABLE.x, y: TABLE.y, width: TABLE.w, height: TABLE.h }, table);
    v.name = text(table, TABLE.x + 12, TABLE.y + 21, '', 'fl-table-name');
    v.count = text(table, TABLE.x + TABLE.w - 12, TABLE.y + 21, '', 'fl-table-count', 'end');
    v.rows = [];
    for (var r = 0; r < 4; r++) {
      var ry = TABLE.y + 33 + r * 13;
      var row = el('g', {}, table);
      el('rect', { 'class': 'fl-cell is-id', x: TABLE.x + 12, y: ry, width: 34, height: 7 }, row);
      el('rect', { 'class': 'fl-cell', x: TABLE.x + 52, y: ry, width: 70, height: 7 }, row);
      var files = el('g', {}, row);
      v.rows.push(files);
    }
    return v;
  }

  function chip(name) {
    return '<span class="flow-file"><i style="background:' + TYPES[fileType(name)] + '"></i>' + name + '</span>';
  }

  function describe(set) {
    var where = [set.source];
    if (set.size !== 'NA') where.push(set.size + ' px');
    if (set.res !== 'NA') where.push(set.res + ' m');
    return '<p class="flow-title"><b>' + set.name + '</b> ' + set.task + '</p>' +
      '<div class="flow-cols">' +
      '<div><p class="flow-k">Today</p>' +
      '<p>' + where.join(', ') + '</p>' +
      '<p>' + set.samples + ' samples' + (set.classes === 'NA' ? '' : ', ' + set.classes + ' classes') + '</p>' +
      '<p>' + set.today + '</p>' +
      '<p>License ' + set.license + '</p></div>' +
      '<div><p class="flow-k is-taco">In TACO</p>' +
      '<p class="flow-files">' + set.files.map(chip).join('') + '</p>' +
      '<p>' + set.samples + ' rows, one per sample</p></div>' +
      '</div>';
  }

  taco.flowFigure = function (figure) {
    var sets = shuffled();
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'group', 'aria-label': figure.getAttribute('data-label') || '' }, figure);
    var info = document.createElement('div');
    info.className = 'flow-info';
    info.setAttribute('aria-live', 'polite');
    figure.appendChild(info);
    var view = build(svg, sets);

    function fill(set) {
      // the table shows the files of one sample of the dataset, in every row
      view.name.textContent = set ? set.short : 'one row per sample';
      view.count.textContent = set ? set.samples + ' rows' : '';
      view.rows.forEach(function (files) {
        while (files.firstChild) files.removeChild(files.firstChild);
        (set ? set.files : ['a.rumi', 'b.rumi', 'c.parquet']).forEach(function (f, k) {
          el('rect', { 'class': 'fl-file', x: TABLE.x + 134 + k * 21, y: Number(files.parentNode.firstChild.getAttribute('y')) - 1.5, width: 16, height: 10, fill: set ? TYPES[fileType(f)] : '#e4e4e8' }, files);
        });
      });
      var used = set ? set.files.map(fileType) : [];
      view.ports.forEach(function (p) { p.classList.toggle('is-used', used.indexOf(p.getAttribute('data-type')) >= 0); });
    }

    function show(key) {
      var i = key === null ? -1 : Number(key);
      var set = i >= 0 ? sets[i] : null;
      if (set) figure.setAttribute('data-active', key);
      else figure.removeAttribute('data-active');
      view.tiles.forEach(function (t, k) { t.g.classList.toggle('is-active', k === i); });
      if (set) {
        var t = view.tiles[i];
        var hubTop = HUB.y - HUB.r - 8;
        view.way.setAttribute('d', 'M' + t.x + ' ' + (t.y + 2) + ' C' + t.x + ' ' + (t.y + 60) + ' ' + HUB.x + ' ' + (hubTop - 50) + ' ' + HUB.x + ' ' + hubTop);
        view.wayDot.setAttribute('cx', t.x);
        view.wayDot.setAttribute('cy', t.y + 2);
      }
      fill(set);
      info.innerHTML = set ? describe(set) : '';
    }

    view.tiles.forEach(function (t, k) {
      var key = String(k);
      t.g.addEventListener('mouseenter', function () { show(key); });
      t.g.addEventListener('mouseleave', function () { show(null); });
      t.g.addEventListener('focus', function () { show(key); });
      t.g.addEventListener('blur', function () { show(null); });
    });
    // The panel keeps the height of the tallest description, measured at the
    // current width, so pointing at a dataset never moves the page.
    function reserve() {
      var shown = info.innerHTML;
      var tallest = 0;
      info.style.minHeight = '0px';
      sets.forEach(function (set) {
        info.innerHTML = describe(set);
        tallest = Math.max(tallest, info.offsetHeight);
      });
      info.innerHTML = shown;
      info.style.minHeight = tallest + 'px';
    }

    var resized = 0;
    window.addEventListener('resize', function () {
      window.clearTimeout(resized);
      resized = window.setTimeout(reserve, 150);
    });
    show(null);
    reserve();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(reserve);
  };

  var figure = document.getElementById('flow');
  if (figure) taco.flowFigure(figure);
})((window.taco = window.taco || {}));
