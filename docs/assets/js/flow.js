// The landing figure: five public datasets, each stored its own way, pass
// through TACO and come out with one shape. Rasters become Rumi files, which
// decode on a CPU or a GPU; tables and JSON stay tables and JSON. Five of the
// datasets below are shown on every load, CloudSEN12 and four at random. Pointing at one, tapping
// it or focusing it lights its path and fills the panel under the drawing.
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
  var H = 322;
  var SHOWN = 5;

  var TASKS = {
    C: 'classification', S: 'segmentation', CD: 'change detection', OD: 'object detection',
    I: 'instance segmentation', R: 'regression', T: 'time series'
  };
  var TYPES = { rumi: '#492ae8', table: '#f7cc09', json: '#c4ffc2' };
  var TINTS = ['#d8efd6', '#f3e6b3', '#d3e5f3', '#f3dccd', '#f2d4dc'];
  var LABELS = {
    tif: 'TIFF', bands: 'TIFF', png: 'PNG', jpg: 'JPEG', h5: 'HDF5', tar: 'TAR.GZ',
    json: 'JSON', geojson: 'GeoJSON', xml: 'XML', npy: 'NPY', npz: 'NPZ', csv: 'CSV',
    nc: 'NetCDF', jp2: 'JP2', shp: 'SHP'
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

  var CARD = { w: 120, gap: 15, y: 0, h: 104 };
  var BAND = { x: 0, y: 136, w: 660, h: 50 };
  var BOX = { y: 222, h: 98 };

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

  // Five datasets on every load: CloudSEN12, at a random place, and four more
  // drawn with a Fisher-Yates shuffle.
  var ALWAYS = 'CloudSEN12';

  function pick() {
    var pool = POOL.filter(function (d) { return d.name !== ALWAYS; });
    for (var i = pool.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = pool[i]; pool[i] = pool[j]; pool[j] = t;
    }
    var sets = pool.slice(0, SHOWN - 1);
    var fixed = POOL.filter(function (d) { return d.name === ALWAYS; })[0];
    sets.splice(Math.floor(Math.random() * SHOWN), 0, fixed);
    return sets;
  }

  function doc(g, x, y, mark, fill) {
    el('path', { d: 'M' + x + ' ' + (y + 6) + ' h22 l8 8 v26 h-30 Z', fill: fill, 'class': 'fl-thin' }, g);
    text(g, x + 15, y + 31, mark, 'fl-glyph', 'middle');
  }

  // One glyph per format, drawn in the box from (x, y), w wide.
  var GLYPHS = {
    tif: function (g, x, y, w) {
      var tones = ['#9cc58f', '#c9b27c', '#6f9f64', '#d7cf9a', '#86b0c9', '#b5c98c'];
      var cols = Math.max(2, Math.floor((w - 2) / 15));
      for (var r = 0; r < 3; r++) {
        for (var c = 0; c < cols; c++) {
          el('rect', { x: x + c * 15 + (r % 2) * 2, y: y + 4 + r * 15 + (c % 2), width: 11, height: 11, fill: tones[(r * 5 + c * 3) % tones.length], 'class': 'fl-thin' }, g);
        }
      }
    },
    bands: function (g, x, y, w) {
      for (var b = 0; b < 5; b++) {
        el('rect', { x: x + b * 5, y: y + 4 + b * 4, width: Math.min(44, w - 22), height: 28, fill: b % 2 ? '#e8e0c4' : '#f8f2da', 'class': 'fl-thin' }, g);
      }
    },
    png: function (g, x, y, w) {
      var s = Math.min(30, (w - 6) / 2);
      el('rect', { x: x, y: y + 6, width: s, height: s, fill: '#c9c2b4', 'class': 'fl-thin' }, g);
      el('path', { d: 'M' + x + ' ' + (y + 6 + s) + ' l' + s * 0.4 + ' ' + -s * 0.45 + ' l' + s * 0.25 + ' ' + s * 0.25 + ' l' + s * 0.35 + ' ' + -s * 0.3 + ' v' + s * 0.5 + ' Z', fill: '#8f8676' }, g);
      el('rect', { x: x + s + 5, y: y + 12, width: s, height: s, fill: '#111', 'class': 'fl-thin' }, g);
      el('rect', { x: x + s + 5 + s * 0.3, y: y + 12 + s * 0.25, width: s * 0.35, height: s * 0.4, fill: '#fff' }, g);
    },
    jpg: function (g, x, y, w) {
      var s = Math.min(32, (w - 6) / 1.6);
      [[0, 4, -3], [s * 0.55, 12, 4]].forEach(function (a) {
        var px = x + a[0];
        var py = y + a[1];
        var p = el('g', { transform: 'rotate(' + a[2] + ' ' + (px + s / 2) + ' ' + (py + s / 2) + ')' }, g);
        el('rect', { x: px, y: py, width: s, height: s * 0.8, fill: '#bcd4e6', 'class': 'fl-thin' }, p);
        el('circle', { cx: px + s * 0.72, cy: py + s * 0.25, r: s * 0.1, fill: '#f7cc09' }, p);
        el('path', { d: 'M' + px + ' ' + (py + s * 0.8) + ' l' + s * 0.45 + ' ' + -s * 0.4 + ' l' + s * 0.55 + ' ' + s * 0.4 + ' Z', fill: '#7a9a6c' }, p);
      });
    },
    h5: function (g, x, y, w) {
      var n = w > 60 ? 3 : 2;
      for (var k = 0; k < n; k++) {
        var cx = x + 11 + k * (w > 60 ? 30 : 24);
        var top = y + 8 + (k % 2) * 5;
        el('path', { d: 'M' + (cx - 10) + ' ' + top + ' v24 a10 4 0 0 0 20 0 v-24', fill: '#f0cdb8', 'class': 'fl-thin' }, g);
        el('ellipse', { cx: cx, cy: top, rx: 10, ry: 4, fill: '#f7e1d3', 'class': 'fl-thin' }, g);
      }
    },
    tar: function (g, x, y) {
      [[0, 4, -4], [32, 0, 3], [64, 8, -2]].forEach(function (a) {
        var bx = x + a[0];
        var by = y + a[1];
        var box = el('g', { transform: 'rotate(' + a[2] + ' ' + (bx + 13) + ' ' + (by + 20) + ')' }, g);
        el('rect', { x: bx, y: by, width: 26, height: 38, fill: '#c7dcef', 'class': 'fl-thin' }, box);
        el('rect', { x: bx, y: by + 8, width: 26, height: 5, fill: '#7fa8cf' }, box);
      });
    },
    json: function (g, x, y) { doc(g, x, y, '{ }', '#fff'); },
    geojson: function (g, x, y) { doc(g, x, y, '{ }', '#eef6ea'); },
    xml: function (g, x, y) { doc(g, x, y, '</>', '#fff'); },
    npy: function (g, x, y) { doc(g, x, y, '[ ]', '#eef3f8'); },
    npz: function (g, x, y, w) {
      doc(g, x, y, '[ ]', '#eef3f8');
      if (w > 60) doc(g, x + 36, y + 4, '[ ]', '#eef3f8');
    },
    jp2: function (g, x, y, w) { GLYPHS.jpg(g, x, y, w); },
    nc: function (g, x, y) {
      [[16, 0], [8, 6], [0, 12]].forEach(function (a) {
        el('rect', { x: x + a[0], y: y + 4 + a[1], width: 34, height: 26, fill: '#dfe9d6', 'class': 'fl-thin' }, g);
      });
    },
    shp: function (g, x, y) {
      el('path', { d: 'M' + (x + 2) + ' ' + (y + 30) + ' l8 -20 l14 6 l6 16 l-12 10 Z', fill: '#e7f0dc', 'class': 'fl-thin' }, g);
    },
    csv: function (g, x, y) {
      el('rect', { x: x, y: y + 6, width: 30, height: 34, fill: '#fff', 'class': 'fl-thin' }, g);
      for (var r = 1; r < 4; r++) el('path', { d: 'M' + x + ' ' + (y + 6 + r * 8.5) + ' h30', 'class': 'fl-rule' }, g);
      el('path', { d: 'M' + (x + 12) + ' ' + (y + 6) + ' v34', 'class': 'fl-rule' }, g);
    }
  };

  function build(svg, sets) {
    var v = { sets: [] };
    var defs = el('defs', {}, svg);
    [['fl-head', '#b8bac2'], ['fl-head-on', '#492ae8']].forEach(function (m) {
      var head = el('marker', { id: m[0], markerWidth: 8, markerHeight: 8, refX: 6.5, refY: 4, orient: 'auto' }, defs);
      el('path', { d: 'M0,0 L8,4 L0,8 Z', fill: m[1] }, head);
    });

    // TACO, and the three formats a dataset leaves it in
    var band = el('g', {}, svg);
    el('rect', { 'class': 'fl-band-box', x: BAND.x, y: BAND.y, width: BAND.w, height: BAND.h }, band);
    el('image', { href: 'assets/svg/taco-mark-static.svg?v=1', x: BAND.x + 14, y: BAND.y + 7, width: 36, height: 36 }, band);
    text(band, BAND.x + 58, BAND.y + 31, 'TACO', 'fl-band-title');
    [['rumi', 'Rumi'], ['table', 'Parquet'], ['json', 'JSON']].forEach(function (f, k) {
      var x = BAND.x + 400 + k * 88;
      el('rect', { 'class': 'fl-type', x: x, y: BAND.y + 19, width: 12, height: 12, fill: TYPES[f[0]] }, band);
      text(band, x + 18, BAND.y + 30, f[1], 'fl-type-name');
    });

    sets.forEach(function (set, i) {
      var x = i * (CARD.w + CARD.gap);
      var cx = x + CARD.w / 2;
      var gx = W / 2 + (i - 2) * 46;
      var g = el('g', {
        'class': 'fl-set', tabindex: '0', role: 'button', 'data-set': String(i),
        'aria-label': set.name + ', ' + set.samples + ' samples'
      }, svg);

      // today: its own tint, its own pile, its own formats
      el('rect', { 'class': 'fl-card', x: x, y: CARD.y, width: CARD.w, height: CARD.h, fill: TINTS[i % TINTS.length] }, g);
      text(g, x + 10, CARD.y + 19, set.short, 'fl-name');
      if (set.glyphs.length === 1) GLYPHS[set.glyphs[0]](g, x + 10, CARD.y + 26, 100);
      else {
        GLYPHS[set.glyphs[0]](g, x + 10, CARD.y + 26, 62);
        GLYPHS[set.glyphs[1]](g, x + 78, CARD.y + 26, 32);
      }
      var tags = [];
      set.glyphs.forEach(function (k) { if (tags.indexOf(LABELS[k]) < 0) tags.push(LABELS[k]); });
      text(g, x + 10, CARD.y + 94, tags.join(' + '), 'fl-format');

      // through TACO
      el('path', { 'class': 'fl-path', d: 'M' + cx + ' ' + (CARD.y + CARD.h) + ' C' + cx + ' ' + (BAND.y - 12) + ' ' + gx + ' ' + (CARD.y + CARD.h + 8) + ' ' + gx + ' ' + (BAND.y - 3), 'marker-end': 'url(#fl-head)' }, g);
      el('path', { 'class': 'fl-path', d: 'M' + gx + ' ' + (BAND.y + BAND.h) + ' C' + gx + ' ' + (BOX.y - 10) + ' ' + cx + ' ' + (BAND.y + BAND.h + 8) + ' ' + cx + ' ' + (BOX.y - 3), 'marker-end': 'url(#fl-head)' }, g);

      // in TACO: the files of one sample, then one row per sample
      el('rect', { 'class': 'fl-box', x: x, y: BOX.y, width: CARD.w, height: BOX.h }, g);
      text(g, x + 10, BOX.y + 19, set.short, 'fl-name');
      set.files.forEach(function (f, k) {
        el('rect', { 'class': 'fl-type', x: x + 10 + k * 16, y: BOX.y + 28, width: 12, height: 12, fill: TYPES[fileType(f)] }, g);
      });
      for (var r = 0; r < 3; r++) {
        el('rect', { 'class': 'fl-row', x: x + 10, y: BOX.y + 50 + r * 9, width: 100, height: 5 }, g);
      }
      text(g, x + 10, BOX.y + 90, set.samples, 'fl-format');
      v.sets.push(g);
    });
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

  // nothing until a dataset is pointed at, tapped or focused
  var HINT = '';

  taco.flowFigure = function (figure) {
    var sets = pick();
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'group', 'aria-label': figure.getAttribute('data-label') || '' }, figure);
    var info = document.createElement('div');
    info.className = 'flow-info';
    info.setAttribute('aria-live', 'polite');
    figure.appendChild(info);
    var view = build(svg, sets);
    var pinned = null;

    function show(key) {
      var set = key === null ? null : sets[Number(key)];
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
      var toggle = function () {
        pinned = pinned === key ? null : key;
        show(pinned || key);
      };
      g.addEventListener('mouseenter', function () { show(key); });
      g.addEventListener('mouseleave', function () { show(pinned); });
      g.addEventListener('focus', function () { show(key); });
      g.addEventListener('blur', function () { show(pinned); });
      // a tap or a click keeps a dataset shown, a second one lets it go
      g.addEventListener('click', toggle);
      g.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggle();
        }
      });
    });
    show(null);
  };

  var figure = document.getElementById('flow');
  if (figure) taco.flowFigure(figure);
})((window.taco = window.taco || {}));
