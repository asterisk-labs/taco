import { openDataset } from "../javascript/src/index.js?v=16";
import * as maplibregl from "https://cdn.jsdelivr.net/npm/maplibre-gl@6.9.0/dist/maplibre-gl.mjs";
import { extendRandomRowIndexes, randomRowIndexes } from "./sampling.js?v=2";

const FIXTURE_ROOT = "https://huggingface.co/datasets/asterisk-labs/taco-api-fixtures/resolve/main";
const MANIFEST_URL = `${FIXTURE_ROOT}/manifest.json`;
const CENTROID_PROFILES = new Set(["spatial", "ispacial", "ispatial", "stac", "stac-interval", "shared-stac", "stac-footprint", "istac"]);
const CENTROID_FIELDS = ["stac:centroid", "spatial:centroid"];
// Columns that locate a sample; they are not shown as metadata or offered for coloring.
const LOCATION_FIELDS = new Set([...CENTROID_FIELDS, "stac:geometry", "spatial:geometry", "stac:bbox", "spatial:bbox"]);
const PLOT_COLORS = ["#0f766e", "#2563eb", "#7c3aed", "#d97706", "#dc2626", "#0891b2", "#65a30d", "#c026d3"];
const DESKTOP_POINT_LIMIT = 100_000;
const DESKTOP_POINT_STEP = 50_000;
const MOBILE_POINT_LIMIT = 25_000;
const MOBILE_POINT_STEP = 25_000;
const GROUND_CELL_FILL = .72;
const GROUND_CELL_SHRINK = .8;
const GROUND_CELL_MIN_FILL = .3;
const DENSE_RADIUS_LIMIT = 6;
const CODE_LANGUAGE_KEY = "taco-viewer:code-language";
const CODE_LANGUAGES = [["python", "Python"], ["r", "R"], ["julia", "Julia"]];
const PREVIEW_SOURCE = "taco-preview";
const PREVIEW_LAYER = "taco-preview";
const PROJ4_URL = "https://cdn.jsdelivr.net/npm/proj4@2.15.0/+esm";
const LANGUAGE_ICONS = {
  python: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#3776AB" d="M11.9 2c-4.6 0-4.3 2-4.3 2v2.1h4.4v.6H5.8S3 6.4 3 11s2.5 4.4 2.5 4.4H7v-2.1s-.1-2.5 2.5-2.5h4.3s2.4 0 2.4-2.3V4.6S16.6 2 11.9 2zM9.5 3.4a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6z"/><path fill="#FFD43B" d="M12.1 22c4.6 0 4.3-2 4.3-2v-2.1H12v-.6h6.2S21 17.6 21 13s-2.5-4.4-2.5-4.4H17v2.1s.1 2.5-2.5 2.5h-4.3s-2.4 0-2.4 2.3v3.9S7.4 22 12.1 22zm2.4-1.4a.8.8 0 1 1 0-1.6.8.8 0 0 1 0 1.6z"/></svg>',
  r: '<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="11" cy="10" rx="9.5" ry="6.2" fill="none" stroke="#9aa3ad" stroke-width="2.6"/><text x="14" y="20" fill="#276DC3" font-family="Arial, Helvetica, sans-serif" font-size="14" font-weight="900" text-anchor="middle">R</text></svg>',
  julia: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="6.6" r="4.3" fill="#389826"/><circle cx="6.6" cy="16" r="4.3" fill="#CB3C33"/><circle cx="17.4" cy="16" r="4.3" fill="#9558B2"/></svg>',
};
const DOWNLOAD_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>';
const PLOT_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="1.5"/><path d="M3 16l5-5 4 4 3-3 6 6"/><circle cx="16" cy="9" r="1.5"/></svg>';
const PREVIEW_SIZE = 256;
const PLACEHOLDER_EMOJIS = ["🌮", "🌍", "🛰️", "🌋", "🏔️", "🌊", "🌵", "🌲", "🌾", "🏝️", "🌪️", "🔥", "🐢", "🦙", "🐙", "🦜", "🌻", "🍄", "❄️", "🌈"];
const COPY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="1.5"/><path d="M5 15V5.5A1.5 1.5 0 0 1 6.5 4H15"/></svg>';
const CHECK_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const requestedUrl = new URL(window.location.href).searchParams.get("url");

const element = Object.fromEntries(
  [
    "fixtureSelect", "datasetUrl", "plotPanel", "plotField", "plotMode", "plotLegend", "loadDataset", "status", "message",
    "metadataPanel", "pointPosition", "pointTitle", "pointCoordinates", "metadataBody", "closeMetadata",
    "metadataPath", "metadataSource", "metadataCount", "loading", "loadingLabel",
    "downloadProgress", "downloadBar", "downloadPercent", "downloadBytes",
    "sampleDisplay", "sampleDisplayPercent", "sampleDisplayCount", "sampleDisplayProgress",
    "sampleDisplayHint", "decreasePoints", "increasePoints",
  ].map((id) => [id, document.getElementById(id)]),
);

const POINT_SOURCE = "taco-samples";
const POINT_LAYER = "taco-points";

function projectionControl() {
  let container;
  let activeProjection = "globe";
  let transitionToken = 0;

  return {
    onAdd(mapInstance) {
      container = document.createElement("div");
      container.className = "maplibregl-ctrl map-mode-control";
      container.setAttribute("role", "group");
      container.setAttribute("aria-label", "Map projection");

      for (const [label, projection] of [["3D", "globe"], ["2D", "mercator"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.dataset.projection = projection;
        button.setAttribute("aria-pressed", String(projection === "globe"));
        button.classList.toggle("active", projection === "globe");
        button.addEventListener("click", async () => {
          if (projection === activeProjection) return;
          const token = ++transitionToken;
          const mapContainer = mapInstance.getContainer();
          const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          mapInstance.stop();

          for (const sibling of container.querySelectorAll("button")) {
            const active = sibling === button;
            sibling.classList.toggle("active", active);
            sibling.setAttribute("aria-pressed", String(active));
            sibling.disabled = true;
          }

          if (!reducedMotion) {
            mapContainer.classList.remove("projection-entering");
            mapContainer.classList.add("projection-leaving");
            await new Promise((resolve) => setTimeout(resolve, 140));
          }

          if (token !== transitionToken) return;
          mapInstance.setProjection({ type: projection });
          activeProjection = projection;
          mapInstance.triggerRepaint();
          mapContainer.classList.remove("projection-leaving");

          if (!reducedMotion) {
            mapContainer.classList.add("projection-entering");
            await new Promise((resolve) => setTimeout(resolve, 300));
          }

          if (token !== transitionToken) return;
          mapContainer.classList.remove("projection-entering");
          for (const sibling of container.querySelectorAll("button")) sibling.disabled = false;
        });
        container.append(button);
      }

      return container;
    },
    onRemove() {
      transitionToken += 1;
      container?.remove();
    },
  };
}

const map = new maplibregl.Map({
  container: "map",
  center: [0, 12],
  zoom: 2.25,
  attributionControl: false,
  maxZoom: 16,
  style: {
    version: 8,
    projection: { type: "globe" },
    sources: {
      basemap: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
        maxzoom: 16,
      },
      labels: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
        maxzoom: 16,
      },
    },
    layers: [
      { id: "basemap", type: "raster", source: "basemap" },
      { id: "labels", type: "raster", source: "labels" },
    ],
    sky: {
      "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0],
    },
  },
});
map.addControl(projectionControl(), "top-left");
map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), "bottom-right");
const mapReady = new Promise((resolve) => map.on("load", resolve));

map.on("click", (event) => {
  if (!map.getLayer(POINT_LAYER)) return;
  const feature = nearestPointFeature(event.point);
  if (feature?.id !== undefined) void selectPoint(Number(feature.id));
});
map.on("mousemove", (event) => {
  if (!map.getLayer(POINT_LAYER)) return;
  const feature = nearestPointFeature(event.point);
  map.getCanvas().style.cursor = feature ? "pointer" : "";
});
map.on("mouseout", () => {
  map.getCanvas().style.cursor = "";
});

function nearestPointFeature(cursor) {
  const tolerance = 8;
  const features = map.queryRenderedFeatures(
    [[cursor.x - tolerance, cursor.y - tolerance], [cursor.x + tolerance, cursor.y + tolerance]],
    { layers: [POINT_LAYER] },
  );
  let nearest = null;
  let nearestDistance = Infinity;
  for (const feature of features) {
    if (feature.id === undefined || feature.geometry?.type !== "Point") continue;
    const screenPoint = map.project(feature.geometry.coordinates);
    const distance = ((screenPoint.x - cursor.x) ** 2) + ((screenPoint.y - cursor.y) ** 2);
    if (distance < nearestDistance) {
      nearest = feature;
      nearestDistance = distance;
    }
  }
  return nearest;
}

const state = {
  manifest: null,
  fixtures: [],
  centroidCases: new Set(),
  fixtureIndex: -1,
  currentUrl: null,
  dataset: null,
  points: [],
  sampledIndexes: null,
  sampleIndexes: null,
  sampleRows: 0,
  centroidField: null,
  identityColumns: [],
  colorIndexes: new Uint8Array(),
  plotToken: 0,
  pointData: null,
  pointBaseZoom: 2,
  plotField: null,
  plotMode: "auto",
  sampleFields: {},
  selectedPoint: -1,
  panelMode: null,
  metadataPages: [],
  metadataPageIndex: -1,
  parquetCachePromise: null,
  metadataToken: 0,
  messageTimer: null,
  loadToken: 0,
  changingPointCount: false,
};

prefillRequestedUrl();
bindEvents();
initialize();

function prefillRequestedUrl() {
  if (!requestedUrl) return;
  element.datasetUrl.value = requestedUrl;
}

async function initialize() {
  setStatus("loading", "Loading fixtures");
  try {
    const response = await fetch(MANIFEST_URL);
    if (!response.ok) throw new Error(`Fixture manifest returned HTTP ${response.status}`);
    state.manifest = await response.json();
    state.fixtures = state.manifest.datasets || [];
    state.centroidCases = new Set(
      (state.manifest.logical_cases || [])
        .filter((item) => CENTROID_PROFILES.has(item.coordinate_profile))
        .map((item) => item.id),
    );
    if (state.fixtures.length !== 50) throw new Error(`Expected 50 fixtures, found ${state.fixtures.length}`);
    populateFixtureSelect();
    if (requestedUrl) {
      element.fixtureSelect.value = "custom";
    }
    if (state.loadToken === 0) setStatus("idle", "Ready to load");
  } catch (error) {
    populateFixtureSelect();
    if (state.loadToken === 0) setStatus("idle", "Ready to load");
    showMessage(`Examples unavailable. ${messageOf(error)}`);
  }
}

function bindEvents() {
  element.fixtureSelect.addEventListener("change", () => {
    const requested = Number(element.fixtureSelect.value);
    if (!Number.isInteger(requested)) return;
    const fixture = state.fixtures[requested];
    if (state.centroidCases.has(fixture.case)) {
      loadFixture(requested);
      return;
    }
    const next = findCompatibleFixture(requested, 1, fixture.topology);
    if (next < 0) return fail(new Error("No fixture with a sample-level spatial centroid was found."));
    showMessage(`${fixture.case} has no sample centroid. Opened ${state.fixtures[next].case}.`);
    loadFixture(next);
  });
  element.loadDataset.addEventListener("click", () => { void loadDatasetUrl(element.datasetUrl.value); });
  element.increasePoints.addEventListener("click", () => { void increaseDisplayedPoints(); });
  element.decreasePoints.addEventListener("click", () => { void decreaseDisplayedPoints(); });
  element.plotField.addEventListener("change", () => {
    state.plotMode = "auto";
    element.plotMode.value = "auto";
    void loadPlotField(element.plotField.value || null);
  });
  element.plotMode.addEventListener("change", () => {
    state.plotMode = element.plotMode.value;
    if (state.plotField) void loadPlotField(state.plotField);
  });
  element.datasetUrl.addEventListener("keydown", (event) => {
    if (event.key === "Enter") void loadDatasetUrl(element.datasetUrl.value);
  });
  element.closeMetadata.addEventListener("click", closeMetadata);
  document.addEventListener("keydown", (event) => {
    if (!element.metadataPanel.classList.contains("open")) return;
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement || event.target instanceof HTMLButtonElement) return;
    if (event.key === "Escape") closeMetadata();
    if (event.key === "ArrowLeft") navigateMetadata(-1);
    if (event.key === "ArrowRight") navigateMetadata(1);
  });
}

function populateFixtureSelect() {
  element.fixtureSelect.replaceChildren();
  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "Custom URL";
  element.fixtureSelect.append(custom);
  const groups = new Map();
  state.fixtures.forEach((fixture, index) => {
    if (!state.centroidCases.has(fixture.case)) return;
    if (!groups.has(fixture.case)) groups.set(fixture.case, []);
    groups.get(fixture.case).push({ fixture, index });
  });
  for (const [caseId, fixtures] of groups) {
    const group = document.createElement("optgroup");
    group.label = caseId;
    for (const { fixture, index } of fixtures) {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${caseId} / ${topologyLabel(fixture.topology)}`;
      group.append(option);
    }
    element.fixtureSelect.append(group);
  }
}

async function loadFixture(index) {
  if (index < 0 || index >= state.fixtures.length) return;
  const fixture = state.fixtures[index];
  await loadDatasetUrl(fixtureUrl(fixture), { fixture, index });
}

async function loadDatasetUrl(value, { fixture = null, index = -1 } = {}) {
  let url;
  try {
    url = normalizedDatasetUrl(value);
  } catch (error) {
    fail(error);
    return;
  }
  const token = ++state.loadToken;
  setLoading(true);
  closeMetadata();
  clearMarkers();
  state.points = [];
  state.sampledIndexes = null;
  state.sampleIndexes = null;
  state.sampleRows = 0;
  state.centroidField = null;
  state.identityColumns = [];
  state.colorIndexes = new Uint8Array();
  state.parquetCachePromise = null;
  state.changingPointCount = false;
  element.sampleDisplay.hidden = true;
  element.plotPanel.hidden = true;
  setStatus("loading", "Reading TACO");
  disableDatasetNavigation(true);
  element.fixtureSelect.value = String(index);

  try {
    const dataset = await openDataset(url);
    const sampleFields = dataset.contract.metadata.sample || {};
    const centroidField = CENTROID_FIELDS.find((field) => field in sampleFields) ?? null;
    if (!centroidField) {
      const next = fixture ? findCompatibleFixture(index, 1, fixture.topology) : -1;
      if (next >= 0 && next !== index) {
        showMessage(`${fixture.case} has no sample centroid. Moving to ${state.fixtures[next].case}.`);
        if (token === state.loadToken) await loadFixture(next);
        return;
      }
      throw new Error("This dataset has no sample-level spatial centroid.");
    }

    const identityColumns = ["internal:current_id", centroidField];
    if (dataset.container === "tacocat") identityColumns.push("internal:source_file");
    const sampleRows = await dataset.levelRowCount("sample");
    setStatus("loading", "Downloading sample.parquet");
    setLoadingLabel("Downloading sample.parquet");
    await dataset.cacheLevel("sample", { onProgress: updateDownloadProgress });
    finishDownloadProgress();
    setStatus("loading", "Sampling points");
    setLoadingLabel("Sampling points");
    const sampleIndexes = randomRowIndexes(sampleRows, pointDisplayLimit());
    const displayedRows = await dataset.readLevel("sample", {
      columns: identityColumns,
      ...(sampleIndexes ? { rowIndexes: sampleIndexes } : {}),
    });
    const points = pointsFromMetadata(displayedRows, centroidField, sampleIndexes);
    if (!points.length) throw new Error(`The ${centroidField} column contains no readable points.`);
    if (token !== state.loadToken) return;

    state.fixtureIndex = index;
    state.currentUrl = url;
    state.dataset = dataset;
    state.points = points;
    state.sampledIndexes = sampleIndexes;
    state.sampleIndexes = sampleIndexes === null && points.length === sampleRows
      ? null
      : points.map((point) => point.physicalIndex);
    state.sampleRows = sampleRows;
    state.centroidField = centroidField;
    state.identityColumns = identityColumns;
    state.colorIndexes = new Uint8Array(points.length);
    populatePlotFields(sampleFields);
    renderDataset(url, index);
    await renderPoints();
    setPointCountStatus(points.length, sampleRows, " · caching metadata");
    state.parquetCachePromise = cacheParquets(dataset, token, sampleRows);
  } catch (error) {
    if (token === state.loadToken) fail(error);
  } finally {
    if (token === state.loadToken) {
      setLoading(false);
      disableDatasetNavigation(false);
    }
  }
}

async function cacheParquets(dataset, token, sampleRows) {
  if (typeof dataset.cacheLevel !== "function") return;
  try {
    await Promise.all(dataset.levels.map((level) => dataset.cacheLevel(level)));
    if (token === state.loadToken && dataset === state.dataset && !state.changingPointCount) {
      setPointCountStatus(state.points.length, sampleRows);
    }
  } catch (error) {
    if (token === state.loadToken && dataset === state.dataset) showMessage(messageOf(error));
  }
}

function pointDisplayLimit() {
  return pointDisplayConfig().initial;
}

function pointDisplayConfig() {
  return window.matchMedia("(max-width: 760px)").matches
    ? { initial: MOBILE_POINT_LIMIT, step: MOBILE_POINT_STEP }
    : { initial: DESKTOP_POINT_LIMIT, step: DESKTOP_POINT_STEP };
}

function displayCountLabel(displayedRows, totalRows) {
  const displayed = displayedRows.toLocaleString();
  if (displayedRows < totalRows) return `${displayed} of ${totalRows.toLocaleString()} points`;
  return `${displayed} points`;
}

function setPointCountStatus(displayedRows, totalRows, suffix = "") {
  setStatus("ready", `${displayedRows.toLocaleString()} points${suffix}`);
  const { step, initial } = pointDisplayConfig();
  element.sampleDisplay.hidden = totalRows <= initial;
  if (element.sampleDisplay.hidden) return;

  const percentage = (displayedRows / totalRows) * 100;
  const currentSampleSize = state.sampledIndexes?.length ?? totalRows;
  const previousSize = previousPointCount(currentSampleSize, initial, step);
  const nextSize = Math.min(currentSampleSize + step, totalRows);
  const canDecrease = previousSize < currentSampleSize;
  const canIncrease = nextSize > currentSampleSize;

  element.sampleDisplayPercent.textContent = `${formatPercentage(percentage)} displayed`;
  element.sampleDisplayCount.textContent = displayCountLabel(displayedRows, totalRows);
  element.sampleDisplayProgress.max = totalRows;
  element.sampleDisplayProgress.value = displayedRows;
  element.decreasePoints.disabled = !canDecrease || state.changingPointCount;
  element.increasePoints.disabled = !canIncrease || state.changingPointCount;
  element.sampleDisplayHint.textContent = !canIncrease
    ? "All readable points are displayed"
    : nextSize > initial * 2
      ? "Higher point counts use more memory"
      : "Random sample of the complete dataset";
}

function previousPointCount(current, initial, step) {
  if (current <= initial) return initial;
  return initial + Math.floor((current - initial - 1) / step) * step;
}

function formatPercentage(value) {
  if (value >= 10) return `${Math.round(value)}%`;
  return `${value.toFixed(1)}%`;
}

function compactCount(value) {
  if (value >= 1_000_000) return `${Number((value / 1_000_000).toFixed(1))}M`;
  if (value >= 1_000) return `${Number((value / 1_000).toFixed(0))}k`;
  return String(value);
}

async function increaseDisplayedPoints() {
  if (!state.dataset || state.sampledIndexes === null || state.changingPointCount) return;
  const { step } = pointDisplayConfig();
  const targetSize = Math.min(state.sampledIndexes.length + step, state.sampleRows);
  await changeDisplayedPointCount(targetSize);
}

async function decreaseDisplayedPoints() {
  if (!state.dataset || state.sampledIndexes === null || state.changingPointCount) return;
  const { initial, step } = pointDisplayConfig();
  const targetSize = previousPointCount(state.sampledIndexes.length, initial, step);
  await changeDisplayedPointCount(targetSize);
}

async function changeDisplayedPointCount(targetSize) {
  const currentSize = state.sampledIndexes.length;
  if (targetSize === currentSize) return;

  const token = state.loadToken;
  const increasing = targetSize > currentSize;
  state.changingPointCount = true;
  element.decreasePoints.disabled = true;
  element.increasePoints.disabled = true;
  element.sampleDisplayHint.textContent = increasing
    ? "Reading additional points from cached metadata"
    : "Releasing points from the displayed view";
  setStatus("loading", `${increasing ? "Adding" : "Removing"} ${compactCount(Math.abs(targetSize - currentSize))} points`);

  try {
    if (increasing) {
      const extendedIndexes = extendRandomRowIndexes(state.sampleRows, state.sampledIndexes, targetSize);
      const addedIndexes = extendedIndexes.slice(currentSize);
      const rows = await state.dataset.readLevel("sample", {
        columns: state.identityColumns,
        rowIndexes: addedIndexes,
      });
      if (token !== state.loadToken) return;

      const newPoints = pointsFromMetadata(rows, state.centroidField, addedIndexes);
      state.sampledIndexes = extendedIndexes;
      state.points.push(...newPoints);
      state.sampleIndexes.push(...newPoints.map((point) => point.physicalIndex));
      const colors = new Uint8Array(state.points.length);
      colors.set(state.colorIndexes);
      state.colorIndexes = colors;
    } else {
      const retainedIndexes = state.sampledIndexes.slice(0, targetSize);
      const retained = new Set(retainedIndexes);
      const points = [];
      const colors = [];
      state.points.forEach((point, index) => {
        if (!retained.has(point.physicalIndex)) return;
        points.push(point);
        colors.push(state.colorIndexes[index]);
      });
      if (state.selectedPoint >= points.length) closeMetadata();
      state.sampledIndexes = retainedIndexes;
      state.points = points;
      state.sampleIndexes = points.map((point) => point.physicalIndex);
      state.colorIndexes = Uint8Array.from(colors);
    }

    await updateRenderedPoints();
    if (state.plotField) await loadPlotField(state.plotField);
  } catch (error) {
    if (token === state.loadToken) showMessage(messageOf(error));
  } finally {
    if (token === state.loadToken) {
      state.changingPointCount = false;
      setPointCountStatus(state.points.length, state.sampleRows);
    }
  }
}

function pointsFromMetadata(rows, centroidField, rowIndexes = null) {
  return rows.flatMap((metadata, position) => {
    const row = sampleRowFromMetadata(metadata);
    const centroid = decodePoint(row[centroidField]);
    if (!centroid) return [];
    const identity = { "taco:sample_index": row["taco:sample_index"] };
    if (row.source_file !== undefined) identity.source_file = row.source_file;
    return [{
      row: identity,
      centroidField,
      physicalIndex: rowIndexes?.[position] ?? position,
      longitude: centroid[0],
      latitude: centroid[1],
    }];
  });
}

function sampleRowFromMetadata(row) {
  const sample = { "taco:sample_index": Number(row["internal:current_id"]) };
  if (row["internal:source_file"] !== undefined) sample.source_file = row["internal:source_file"];
  for (const [name, value] of Object.entries(row)) {
    if (CENTROID_FIELDS.includes(name)) sample[name] = value;
  }
  return sample;
}

function renderDataset(url, index) {
  element.datasetUrl.value = url;
  if (index >= 0) element.fixtureSelect.value = String(index);
  else element.fixtureSelect.value = "custom";
  window.history.replaceState(null, "", shareablePlaygroundUrl(url));
}

async function renderPoints() {
  clearMarkers();
  await mapReady;
  state.pointData = pointFeatureCollection();
  map.addSource(POINT_SOURCE, {
    type: "geojson",
    data: state.pointData,
    maxzoom: 12,
  });

  const bounds = new maplibregl.LngLatBounds();
  state.points.forEach((point) => bounds.extend([point.longitude, point.latitude]));
  if (state.points.length === 1) map.jumpTo({ center: bounds.getCenter(), zoom: 7 });
  else map.fitBounds(bounds, { padding: 70, maxZoom: 5, duration: 0 });
  state.pointBaseZoom = map.getZoom();
  map.addLayer({
    id: POINT_LAYER,
    type: "circle",
    source: POINT_SOURCE,
    paint: {
      ...pointPaint(),
      "circle-color": categoricalColorExpression(),
      "circle-stroke-color": ["case", ["boolean", ["feature-state", "selected"], false], "#20251f", "#ffffff"],
    },
  });
}

async function updateRenderedPoints() {
  await mapReady;
  const source = map.getSource(POINT_SOURCE);
  if (!source) {
    await renderPoints();
    return;
  }
  state.pointData = pointFeatureCollection();
  await source.setData(state.pointData);
  for (const [property, value] of Object.entries(pointPaint())) map.setPaintProperty(POINT_LAYER, property, value);
}

function pointFeatureCollection() {
  return {
    type: "FeatureCollection",
    features: state.points.map((point, index) => ({
      type: "Feature",
      id: index,
      properties: { point_index: index, color_index: state.colorIndexes[index] },
      geometry: { type: "Point", coordinates: [point.longitude, point.latitude] },
    })),
  };
}

function populatePlotFields(sampleFields) {
  state.sampleFields = sampleFields;
  const fields = Object.keys(sampleFields).filter((name) => !LOCATION_FIELDS.has(name));
  element.plotField.replaceChildren();
  const uniform = document.createElement("option");
  uniform.value = "";
  uniform.textContent = "Single color";
  element.plotField.append(uniform);
  fields.forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    element.plotField.append(option);
  });
  state.plotField = null;
  state.plotMode = "auto";
  element.plotField.value = "";
  element.plotMode.value = "auto";
  element.plotMode.hidden = true;
  element.plotLegend.hidden = true;
  element.plotField.disabled = false;
  element.plotPanel.hidden = fields.length === 0;
}

function colorIndex(value) {
  if (value === null || value === undefined || value === "") return 255;
  const text = String(value);
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
  return Math.abs(hash) % PLOT_COLORS.length;
}

function categoricalColorExpression() {
  return [
    "match", ["get", "color_index"],
    0, PLOT_COLORS[0], 1, PLOT_COLORS[1], 2, PLOT_COLORS[2], 3, PLOT_COLORS[3],
    4, PLOT_COLORS[4], 5, PLOT_COLORS[5], 6, PLOT_COLORS[6], 7, PLOT_COLORS[7],
    "#94a3b8",
  ];
}

function continuousColorExpression() {
  return [
    "case", ["==", ["get", "color_index"], 255], "#94a3b8",
    ["interpolate", ["linear"], ["get", "color_index"],
      0, "#440154", 64, "#3b528b", 128, "#21918c", 192, "#5ec962", 254, "#fde725"],
  ];
}

async function loadPlotField(field) {
  const token = ++state.plotToken;
  state.plotField = field;
  element.plotField.disabled = true;
  element.plotMode.disabled = true;
  element.plotMode.hidden = !field;
  try {
    let analysis = null;
    if (!field) {
      state.colorIndexes = new Uint8Array(state.points.length);
      map.setPaintProperty(POINT_LAYER, "circle-color", categoricalColorExpression());
    } else {
      const columns = ["internal:current_id", field];
      if (state.dataset.container === "tacocat") columns.push("internal:source_file");
      const rows = await state.dataset.readLevel("sample", {
        columns,
        ...(state.sampleIndexes ? { rowIndexes: state.sampleIndexes } : {}),
      });
      if (token !== state.plotToken) return;
      analysis = analyzePlotValues(rows, field, state.sampleFields[field], state.plotMode);
      state.colorIndexes = analysis.colors;
      map.setPaintProperty(
        POINT_LAYER,
        "circle-color",
        analysis.mode === "continuous" ? continuousColorExpression() : categoricalColorExpression(),
      );
    }
    if (token !== state.plotToken) return;
    state.pointData.features.forEach((feature, index) => {
      feature.properties.color_index = state.colorIndexes[index];
    });
    await map.getSource(POINT_SOURCE).setData(state.pointData);
    renderPlotLegend(field, analysis);
  } catch (error) {
    if (token === state.plotToken) showMessage(messageOf(error));
  } finally {
    if (token === state.plotToken) {
      element.plotField.disabled = false;
      element.plotMode.disabled = false;
    }
  }
}

function analyzePlotValues(rows, field, declaration, requestedMode) {
  const values = rows.map((row) => row[field]);
  const declaredType = String(declaration?.type ?? "").toLowerCase();
  const declaredNumeric = /(int|float|double|decimal|number)/.test(declaredType);
  const collectHeavyHitters = requestedMode === "categorical" || (requestedMode === "auto" && !declaredNumeric);
  const sample = [];
  const unique = new Set();
  const heavyHitters = new Map();
  let validCount = 0;
  let numericCount = 0;
  let allIntegers = true;

  values.forEach((value) => {
    if (value === null || value === undefined || value === "") return;
    validCount += 1;
    const key = String(value);
    if (unique.size <= 64) unique.add(key);
    if (heavyHitters.has(key)) heavyHitters.set(key, heavyHitters.get(key) + 1);
    else if (heavyHitters.size < 64) heavyHitters.set(key, 1);
    else if (collectHeavyHitters) {
      let lightestKey;
      let lightestCount = Infinity;
      for (const [candidate, count] of heavyHitters) {
        if (count < lightestCount) {
          lightestKey = candidate;
          lightestCount = count;
        }
      }
      heavyHitters.delete(lightestKey);
      heavyHitters.set(key, lightestCount + 1);
    }

    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return;
    numericCount += 1;
    allIntegers &&= Number.isInteger(numeric);
    if (sample.length < 4096) sample.push(numeric);
    else {
      const slot = ((numericCount * 2654435761) >>> 0) % numericCount;
      if (slot < sample.length) sample[slot] = numeric;
    }
  });

  let mode = requestedMode;
  if (mode === "auto") {
    const lowCardinalityInteger = allIntegers && unique.size <= 20
      && (unique.size <= 4 || unique.size / Math.max(validCount, 1) <= .02);
    mode = declaredNumeric && !lowCardinalityInteger ? "continuous" : "categorical";
  }

  if (mode === "continuous") {
    if (!numericCount) throw new Error(`${field} contains no numeric values.`);
    sample.sort((left, right) => left - right);
    let low = quantile(sample, .02);
    let high = quantile(sample, .98);
    if (low === high) {
      low = sample[0];
      high = sample[sample.length - 1];
    }
    const colors = new Uint8Array(values.length);
    values.forEach((value, index) => {
      const numeric = Number(value);
      if (value === null || value === undefined || value === "" || !Number.isFinite(numeric)) {
        colors[index] = 255;
      } else if (high === low) {
        colors[index] = 127;
      } else {
        colors[index] = Math.round(Math.max(0, Math.min(1, (numeric - low) / (high - low))) * 254);
      }
    });
    return { mode, colors, validCount, low, high };
  }

  const colors = new Uint8Array(values.length);
  values.forEach((value, index) => { colors[index] = colorIndex(value); });
  const candidates = unique.size <= 64 ? unique : new Set(heavyHitters.keys());
  const exactCounts = new Map([...candidates].map((value) => [value, 0]));
  values.forEach((value) => {
    const key = String(value);
    if (value !== null && value !== undefined && value !== "" && exactCounts.has(key)) {
      exactCounts.set(key, exactCounts.get(key) + 1);
    }
  });
  const categories = [...exactCounts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 6);
  return { mode, colors, validCount, categories, uniqueCount: unique.size, truncated: unique.size > 64 };
}

function quantile(sorted, probability) {
  if (sorted.length === 1) return sorted[0];
  const position = (sorted.length - 1) * probability;
  const lower = Math.floor(position);
  const fraction = position - lower;
  return sorted[lower] + ((sorted[Math.min(lower + 1, sorted.length - 1)] - sorted[lower]) * fraction);
}

function renderPlotLegend(field, analysis) {
  element.plotLegend.replaceChildren();
  element.plotLegend.hidden = !field || !analysis;
  if (!field || !analysis) return;
  const title = document.createElement("strong");
  title.textContent = field;
  const summary = document.createElement("small");
  summary.textContent = analysis.mode === "continuous"
    ? `Continuous · 2–98% stretch`
    : `Categorical · ${analysis.truncated ? "64+" : analysis.uniqueCount} values`;
  element.plotLegend.append(title, summary);

  if (analysis.mode === "continuous") {
    const gradient = document.createElement("div");
    gradient.className = "legend-gradient";
    const range = document.createElement("div");
    range.className = "legend-range";
    const low = document.createElement("span");
    low.textContent = formatLegendNumber(analysis.low);
    const high = document.createElement("span");
    high.textContent = formatLegendNumber(analysis.high);
    range.append(low, high);
    element.plotLegend.append(gradient, range);
    return;
  }

  const categories = document.createElement("div");
  categories.className = "legend-categories";
  analysis.categories.forEach(([value, count]) => {
    const row = document.createElement("div");
    row.className = "legend-category";
    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.background = PLOT_COLORS[colorIndex(value)];
    const label = document.createElement("span");
    label.textContent = value;
    const frequency = document.createElement("span");
    frequency.textContent = count.toLocaleString();
    row.append(swatch, label, frequency);
    categories.append(row);
  });
  element.plotLegend.append(categories);
}

function formatLegendNumber(value) {
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude >= 100_000 || magnitude < .001)) return value.toExponential(2);
  return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 4 }).format(value);
}

function pointRadiusBase(count) {
  return count > 50_000 ? .8 : count > 10_000 ? 1.1 : count > 5_000 ? 1.5 : count > 1_000 ? 2.2 : 5;
}

function pointRadiusExpression(count, baseZoom) {
  const base = pointRadiusBase(count);
  const selected = ["boolean", ["feature-state", "selected"], false];
  const stops = [[baseZoom, ["case", selected, 6, base]]];
  for (let step = 1; step <= 5; step += 1) {
    const radius = Math.min(7, base * (2 ** step));
    stops.push([baseZoom + step, ["case", selected, Math.max(6, radius + 2), radius]]);
  }
  return ["interpolate", ["linear"], ["zoom"], ...stops.flat()];
}

function pointPaint() {
  const count = state.points.length;
  const selected = ["boolean", ["feature-state", "selected"], false];
  const spacing = estimatePointSpacing(state.points);
  const cellRadius = spacing && ((zoom) => (spacing.meters * GROUND_CELL_FILL) / metersPerPixel(zoom, spacing.latitude));
  if (cellRadius && cellRadius(state.pointBaseZoom) <= DENSE_RADIUS_LIMIT) {
    const base = pointRadiusBase(count);
    const stops = [];
    for (let zoom = 0; zoom <= map.getMaxZoom(); zoom += 1) {
      const levels = Math.max(0, zoom - Math.ceil(state.pointBaseZoom) - 1);
      const shrink = Math.max(GROUND_CELL_MIN_FILL / GROUND_CELL_FILL, GROUND_CELL_SHRINK ** levels);
      const radius = Math.max(base, cellRadius(zoom) * shrink);
      stops.push(zoom, ["case", selected, Math.max(6, radius + 2), radius]);
    }
    return {
      "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], ...stops],
      "circle-opacity": .9,
      "circle-stroke-width": ["case", selected, 2, 0],
    };
  }
  return {
    "circle-radius": pointRadiusExpression(count, state.pointBaseZoom),
    "circle-opacity": count > 5_000 ? .6 : .84,
    "circle-stroke-width": ["case", selected, 2, count > 10_000 ? 0 : 1],
  };
}

function estimatePointSpacing(points) {
  if (points.length < 50) return null;
  let west = Infinity, east = -Infinity, south = Infinity, north = -Infinity;
  for (const point of points) {
    west = Math.min(west, point.longitude);
    east = Math.max(east, point.longitude);
    south = Math.min(south, point.latitude);
    north = Math.max(north, point.latitude);
  }
  const cell = Math.max(1e-6, Math.sqrt(((east - west) * (north - south)) / points.length) * 2);
  const buckets = new Map();
  points.forEach((point) => {
    const key = `${Math.floor(point.longitude / cell)},${Math.floor(point.latitude / cell)}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(point);
  });

  const stride = Math.max(1, Math.floor(points.length / 1000));
  const distances = [];
  const latitudes = [];
  for (let index = 0; index < points.length; index += stride) {
    const probe = points[index];
    const column = Math.floor(probe.longitude / cell);
    const row = Math.floor(probe.latitude / cell);
    const scale = Math.cos((probe.latitude * Math.PI) / 180);
    let nearest = Infinity;
    for (let ring = 1; ring <= 3 && nearest === Infinity; ring += 1) {
      for (let x = column - ring; x <= column + ring; x += 1) {
        for (let y = row - ring; y <= row + ring; y += 1) {
          for (const other of buckets.get(`${x},${y}`) ?? []) {
            const dx = (other.longitude - probe.longitude) * scale * 111_320;
            const dy = (other.latitude - probe.latitude) * 110_540;
            const distance = Math.hypot(dx, dy);
            if (distance > 0 && distance < nearest) nearest = distance;
          }
        }
      }
    }
    if (nearest !== Infinity) {
      distances.push(nearest);
      latitudes.push(probe.latitude);
    }
  }
  if (distances.length < 25) return null;
  distances.sort((left, right) => left - right);
  latitudes.sort((left, right) => left - right);
  return { meters: distances[distances.length >> 1], latitude: latitudes[latitudes.length >> 1] };
}

function metersPerPixel(zoom, latitude) {
  const scale = Math.max(.1, Math.cos((latitude * Math.PI) / 180));
  return (40_075_016.686 * scale) / (512 * (2 ** zoom));
}

function selectPoint(index) {
  if (!state.points.length) return;
  const normalized = (index + state.points.length) % state.points.length;
  const token = ++state.metadataToken;
  const firstOpen = !element.metadataPanel.classList.contains("open");
  if (state.selectedPoint >= 0 && map.getSource(POINT_SOURCE)) {
    map.setFeatureState({ source: POINT_SOURCE, id: state.selectedPoint }, { selected: false });
  }
  state.selectedPoint = normalized;
  map.setFeatureState({ source: POINT_SOURCE, id: normalized }, { selected: true });
  const point = state.points[normalized];
  state.panelMode = "point";
  element.metadataPanel.classList.remove("dataset-mode");
  element.pointPosition.textContent = `Point ${normalized + 1} of ${state.points.length}`;
  element.pointTitle.textContent = pointName(point);
  element.pointCoordinates.textContent = `${formatLatitude(point.latitude)}, ${formatLongitude(point.longitude)}`;
  element.metadataPanel.setAttribute("aria-hidden", "false");
  state.metadataPages = [
    samplePageFromMemory(point),
    ...state.dataset.levels.slice(1).map((level) => ({ label: levelFilename(level), depth: metadataLevelDepth(level), records: null })),
  ];
  state.metadataPageIndex = 0;
  renderMetadataNavigation();
  renderMetadataPage();
  if (firstOpen) {
    requestAnimationFrame(() => {
      if (token === state.metadataToken && state.panelMode === "point") element.metadataPanel.classList.add("open");
    });
  } else {
    element.metadataPanel.classList.add("open");
  }
  void hydrateSampleMetadata(point, token, normalized);
}

async function hydrateSampleMetadata(point, token, selectedIndex) {
  try {
    const pages = await metadataPagesForPoint(point);
    if (token !== state.metadataToken || selectedIndex !== state.selectedPoint) return;
    state.metadataPages = pages;
    renderMetadataNavigation();
    renderMetadataPage();
  } catch (error) {
    if (token === state.metadataToken && selectedIndex === state.selectedPoint) showMessage(messageOf(error));
  }
}

function samplePageFromMemory(point) {
  return {
    label: "sample.parquet",
    depth: 0,
    records: [{
      title: pointName(point),
      values: compactObject(point.row),
    }],
  };
}

async function metadataPagesForPoint(point) {
  await state.parquetCachePromise;
  const sourceFile = point.row.source_file;
  const sampleId = Number(point.row["taco:sample_index"]);
  const locations = new Map();
  const pages = [];

  const selectedRows = new Map();
  const samples = await state.dataset.readLevel("sample", {
    filter: metadataSampleFilter(sampleId, sourceFile),
  });
  selectedRows.set("sample", samples);
  pages.push(parquetPage("sample", samples, locations));

  for (let index = 1; index < state.dataset.levels.length; index += 1) {
    const level = state.dataset.levels[index];
    const parents = selectedRows.get(parentMetadataLevel(level)) ?? [];
    const rows = parents.length
      ? await state.dataset.readLevel(level, {
          filter: metadataParentFilter(parents, sourceFile),
        })
      : [];
    rows.forEach((row) => {
      const path = contractPath(row["internal:relative_path"]);
      const location = metadataLocation(row);
      if (path && location) locations.set(locationKey(row["internal:source_file"], path), location);
    });
    selectedRows.set(level, rows);
    pages.push(parquetPage(level, rows, locations));
  }
  return pages;
}

function metadataSampleFilter(sampleId, sourceFile) {
  const identity = { "internal:current_id": { $eq: BigInt(sampleId) } };
  return sourceFile === undefined
    ? identity
    : { $and: [identity, { "internal:source_file": { $eq: sourceFile } }] };
}

function metadataParentFilter(parents, sourceFile) {
  const parentIds = [...new Set(parents.map((row) => row["internal:current_id"]))];
  const identity = { "internal:parent_id": { $in: parentIds } };
  return sourceFile === undefined
    ? identity
    : { $and: [identity, { "internal:source_file": { $eq: sourceFile } }] };
}

function metadataLocation(row) {
  const offset = row["internal:offset"];
  const size = row["internal:size"];
  if (offset !== undefined && size !== undefined) {
    const sourceFile = row["internal:source_file"];
    const source = sourceFile ? new URL(encodePath(sourceFile), new URL("../", state.dataset.url)).href : state.dataset.url;
    return `/vsisubfile/${offset}_${size},/vsicurl/${source}`;
  }
  const path = row["internal:relative_path"];
  if (!path) return null;
  return `/vsicurl/${new URL(`DATA/${encodePath(path)}`, state.dataset.url).href}`;
}

function encodePath(path) {
  return String(path).split("/").map(encodeURIComponent).join("/");
}

function parquetPage(level, rows, locations) {
  return {
    label: levelFilename(level),
    depth: metadataLevelDepth(level),
    records: rows.map((row, index) => {
      const path = contractPath(row["internal:relative_path"]);
      const values = {};
      if (path) values.path = path;
      values.current_id = row["internal:current_id"];
      if (row["internal:parent_id"] !== undefined) values.parent_id = row["internal:parent_id"];
      if (row["internal:source_file"] !== undefined) values.source_file = row["internal:source_file"];
      if (row["internal:offset"] !== undefined) values.offset = row["internal:offset"];
      if (row["internal:size"] !== undefined) values.size = row["internal:size"];
      for (const [key, value] of Object.entries(row)) {
        if (!key.startsWith("internal:")) values[key] = value;
      }
      const location = locations.get(locationKey(row["internal:source_file"], path));
      if (location) values["taco:location"] = location;
      const fallbackTitle =
        level === "sample" && row["internal:current_id"] !== undefined
          ? `sample__${row["internal:current_id"]}`
          : `row ${index}`;
      return { title: path || String(row["fixture:sample_key"] ?? fallbackTitle), values };
    }),
  };
}

function renderMetadataNavigation() {
  element.metadataPath.replaceChildren();
  state.metadataPages.forEach((page, index) => {
    const button = document.createElement("button");
    button.type = "button";
    const label = document.createElement("span");
    label.textContent = page.label;
    const count = document.createElement("span");
    count.className = "level-count";
    count.textContent = page.records === null ? "…" : `${page.records.length} ${page.records.length === 1 ? "row" : "rows"}`;
    button.append(label, count);
    button.style.setProperty("--depth", String(page.depth));
    button.setAttribute("aria-current", index === state.metadataPageIndex ? "page" : "false");
    button.addEventListener("click", () => {
      state.metadataPageIndex = index;
      renderMetadataNavigation();
      renderMetadataPage();
    });
    if (index === state.metadataPageIndex) button.classList.add("active");
    else if (index === 1 && state.metadataPageIndex === 0) {
      button.classList.add("explore");
      button.title = "Open the metadata of the files inside this sample";
    }
    element.metadataPath.append(button);
  });
  element.metadataPath.querySelector("button.active")?.scrollIntoView({ block: "nearest", inline: "center" });
}

function renderMetadataPage() {
  const page = state.metadataPages[state.metadataPageIndex];
  if (!page) return;
  element.metadataSource.textContent = page.label;
  if (page.records === null) {
    element.metadataCount.textContent = "reading";
    element.metadataBody.replaceChildren(metadataMessage("Reading this level from the cached metadata…"));
    return;
  }
  element.metadataCount.textContent = `${page.records.length} ${page.records.length === 1 ? "row" : "rows"}`;
  element.metadataBody.replaceChildren();
  if (!page.records.length) {
    element.metadataBody.append(metadataMessage("No row for this point at this level."));
    return;
  }
  page.records.forEach((record) => appendMetadataRecord(record));
}

function navigateMetadata(direction) {
  if (!state.metadataPages.length) return;
  const next = Math.max(0, Math.min(state.metadataPages.length - 1, state.metadataPageIndex + direction));
  if (next === state.metadataPageIndex) return;
  state.metadataPageIndex = next;
  renderMetadataNavigation();
  renderMetadataPage();
}

function appendMetadataRecord(record) {
  const section = document.createElement("section");
  section.className = "metadata-record";
  const heading = document.createElement("h3");
  heading.textContent = record.title;
  const list = document.createElement("dl");
  for (const [name, value] of orderedMetadataEntries(record.values)) {
    if (LOCATION_FIELDS.has(name)) continue;
    const wrapper = document.createElement("div");
    wrapper.className = "metadata-row";
    const term = document.createElement("dt");
    term.textContent = name;
    const detail = document.createElement("dd");
    if (name === "taco:location") {
      wrapper.classList.add("location-row");
      appendLocation(detail, String(value), String(record.values.path || record.title), rumiHeaderBytes(record.values["rumi:header"]));
    } else if (name === "rumi:header" && rumiHeaderBytes(value)) {
      wrapper.classList.add("location-row");
      const bytes = rumiHeaderBytes(value);
      detail.append(codeBlock(headerSummary(bytes), () => headerLiteral(bytes), "Copy header bytes"));
    } else {
      detail.textContent = formatValue(value);
    }
    wrapper.append(term, detail);
    list.append(wrapper);
  }
  section.append(heading, list);
  element.metadataBody.append(section);
}

function appendLocation(parent, location, filename, header) {
  const actions = document.createElement("div");
  actions.className = "file-actions";
  const result = document.createElement("span");
  result.className = "asset-result";
  const preview = document.createElement("div");
  preview.className = "rumi-preview";

  if (/\.rumi$/i.test(filename) && header) {
    const languages = document.createElement("div");
    languages.className = "language-copy";
    for (const [language, name] of CODE_LANGUAGES) {
      languages.append(iconAction(LANGUAGE_ICONS[language], `Copy rumi.read for ${name}`, result, async (button) => {
        rememberCodeLanguage(language);
        await copyText(rumiReadCall(location, header, language));
        flashCopied(button);
        result.textContent = `${name} rumi.read copied.`;
      }));
    }
    actions.append(languages);
  }

  const tools = document.createElement("div");
  tools.className = "asset-tools";
  tools.append(iconAction(DOWNLOAD_ICON, "Download file", result, async () => {
    result.textContent = "Preparing download…";
    const resolved = state.dataset.resolveAsset(location);
    const blob = await resolved.blob(contentType(filename));
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = basename(filename) || "taco-asset";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    result.textContent = `${anchor.download} · ${formatBytes(blob.size)}`;
  }));
  if (/\.rumi$/i.test(filename) && header) {
    const plot = iconAction(PLOT_ICON, "Plot", result, async (button) => {
      await plotRumi(preview, state.dataset.resolveAsset(location), header, button);
    });
    plot.classList.add("plot-action");
    tools.append(plot);
  }
  actions.append(tools);

  parent.append(codeBlock(location, location, "Copy location"), actions, result, preview);
}

function iconAction(icon, label, result, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-action";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = icon;
  button.addEventListener("click", async () => {
    button.disabled = true;
    result.textContent = "";
    try {
      await action(button);
    } catch (error) {
      result.textContent = messageOf(error);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function flashCopied(button) {
  button.classList.add("copied");
  window.setTimeout(() => button.classList.remove("copied"), 1200);
}

function codeBlock(text, copyValue, label) {
  const block = document.createElement("div");
  block.className = "code-block";
  const code = document.createElement("code");
  code.className = "location-value";
  code.textContent = text;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-icon";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = COPY_ICON;
  button.addEventListener("click", async () => {
    try {
      await copyText(typeof copyValue === "function" ? copyValue() : copyValue);
      button.innerHTML = CHECK_ICON;
      button.classList.add("copied");
    } catch (error) {
      showMessage(messageOf(error));
      return;
    }
    window.setTimeout(() => {
      button.innerHTML = COPY_ICON;
      button.classList.remove("copied");
    }, 1400);
  });
  block.append(code, button);
  return block;
}

function rumiHeaderBytes(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  return null;
}

function rumiMagic(bytes) {
  return new TextDecoder("ascii").decode(bytes.subarray(0, 4));
}

function headerSummary(bytes) {
  const hex = [...bytes.subarray(4, 10)].map((byte) => byte.toString(16).padStart(2, "0")).join(" ");
  return `${rumiMagic(bytes)} · ${formatBytes(bytes.length)} · ${hex} …`;
}

function codeLanguage() {
  try {
    const stored = window.localStorage.getItem(CODE_LANGUAGE_KEY);
    if (CODE_LANGUAGES.some(([id]) => id === stored)) return stored;
  } catch {}
  return "python";
}

function rememberCodeLanguage(language) {
  try {
    window.localStorage.setItem(CODE_LANGUAGE_KEY, language);
  } catch {}
}

function headerLiteral(bytes, language = codeLanguage()) {
  if (language === "r") {
    return `as.raw(c(${[...bytes].map((byte) => `0x${byte.toString(16).padStart(2, "0")}`).join(", ")}))`;
  }
  let text = "";
  for (const byte of bytes) {
    const escaped = byte === 0x22 || byte === 0x5c;
    if (byte >= 0x20 && byte <= 0x7e && !escaped) text += String.fromCharCode(byte);
    else if (escaped) text += `\\${String.fromCharCode(byte)}`;
    else text += `\\x${byte.toString(16).padStart(2, "0")}`;
  }
  return `b"${text}"`;
}

function rumiReadCall(location, header, language = codeLanguage()) {
  const source = JSON.stringify(location);
  const bytes = headerLiteral(header, language);
  if (language === "r") return `rumi::read(${source}, header = ${bytes})`;
  if (language === "julia") return `Rumi.read(${source.replaceAll("$", "\\$")}; header = ${bytes})`;
  return `rumi.read(${source}, header=${bytes})`;
}

function parseRumiHeader(bytes) {
  if (bytes.length < 32 || rumiMagic(bytes) !== "LOVE") throw new Error("This is not a Rumi header.");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const bits = view.getUint8(24);
  const format = ({ 1: "uint", 2: "int", 3: "float" })[view.getUint8(25)] ?? "unknown";
  return {
    version: view.getUint16(4, true),
    width: view.getUint32(6, true),
    height: view.getUint32(10, true),
    time: view.getUint32(14, true),
    tile: [view.getUint16(20, true), view.getUint16(18, true)],
    bands: view.getUint16(22, true),
    dtype: `${format}${bits}`,
  };
}

async function plotRumi(container, asset, headerBytes, button) {
  if (button.classList.contains("active")) {
    clearPreview();
    return;
  }
  const header = parseRumiHeader(headerBytes);
  const raster = decodeRumi(asset, header);
  const canvas = document.createElement("canvas");
  canvas.width = raster.width;
  canvas.height = raster.height;
  canvas.getContext("2d").putImageData(colorizeRaster(stretchRaster(raster)), 0, 0);
  const time = header.time > 1 ? ` · ${header.time} times` : "";
  const summary = `${header.width}×${header.height} · ${header.bands} ${header.bands === 1 ? "band" : "bands"}${time} · ${header.dtype} · placeholder until geozl 0.3`;

  clearPreview();
  const caption = document.createElement("small");
  const corners = window.matchMedia("(max-width: 760px)").matches ? null : await sampleFootprint(header);
  if (corners) {
    showPreviewOnMap(canvas, corners);
    caption.textContent = `On the map · ${summary}`;
    container.replaceChildren(caption);
  } else {
    caption.textContent = summary;
    container.replaceChildren(canvas, caption);
  }
  button.classList.add("active");
}

function clearPreview() {
  if (map.getLayer(PREVIEW_LAYER)) map.removeLayer(PREVIEW_LAYER);
  if (map.getSource(PREVIEW_SOURCE)) map.removeSource(PREVIEW_SOURCE);
  document.querySelectorAll(".plot-action.active").forEach((button) => button.classList.remove("active"));
  document.querySelectorAll(".rumi-preview").forEach((preview) => preview.replaceChildren());
}

function showPreviewOnMap(canvas, corners) {
  map.addSource(PREVIEW_SOURCE, { type: "image", url: canvas.toDataURL(), coordinates: corners });
  map.addLayer({
    id: PREVIEW_LAYER,
    type: "raster",
    source: PREVIEW_SOURCE,
    paint: { "raster-opacity": 0, "raster-opacity-transition": { duration: 450 }, "raster-fade-duration": 0 },
  });
  requestAnimationFrame(() => map.setPaintProperty(PREVIEW_LAYER, "raster-opacity", .95));
  const bounds = new maplibregl.LngLatBounds();
  corners.forEach((corner) => bounds.extend(corner));
  const panel = element.metadataPanel.getBoundingClientRect().width;
  map.fitBounds(bounds, { padding: { top: 60, bottom: 60, left: 60, right: panel + 60 }, maxZoom: 14, duration: 900 });
}

async function sampleFootprint(header) {
  const values = state.metadataPages[0]?.records?.[0]?.values ?? {};
  const prefix = ["stac", "spatial"].find((name) => values[`${name}:proj_code`] && values[`${name}:proj_transform`]);
  if (!prefix) return null;
  const epsg = Number(String(values[`${prefix}:proj_code`]).match(/^EPSG:(\d+)$/i)?.[1]);
  const transform = Array.from(values[`${prefix}:proj_transform`], Number);
  if (!epsg || transform.length !== 6) return null;
  const toLonLat = await lonLatProjection(epsg);
  if (!toLonLat) return null;
  // STAC proj:transform order: x = a*column + b*row + c, y = d*column + e*row + f.
  const [a, b, c, d, e, f] = transform;
  return [[0, 0], [header.width, 0], [header.width, header.height], [0, header.height]]
    .map(([column, row]) => toLonLat([a * column + b * row + c, d * column + e * row + f]));
}

async function lonLatProjection(epsg) {
  if (epsg === 4326) return (point) => point;
  if (epsg === 3857) {
    return ([x, y]) => [(x / 6378137) * (180 / Math.PI), (2 * Math.atan(Math.exp(y / 6378137)) - Math.PI / 2) * (180 / Math.PI)];
  }
  const zone = epsg - (epsg >= 32701 ? 32700 : 32600);
  if (!((epsg >= 32601 && epsg <= 32660) || (epsg >= 32701 && epsg <= 32760))) return null;
  const proj4 = (await import(PROJ4_URL)).default;
  const definition = `+proj=utm +zone=${zone}${epsg >= 32701 ? " +south" : ""} +datum=WGS84 +units=m +no_defs`;
  return (point) => proj4(definition, "EPSG:4326", point);
}

function decodeRumi(_asset, header) {
  // TODO: decode with geozl once it ships WASM (0.3)
  const scale = Math.min(1, PREVIEW_SIZE / Math.max(header.width, header.height));
  const width = Math.max(1, Math.round(header.width * scale));
  const height = Math.max(1, Math.round(header.height * scale));
  const bands = Math.max(1, header.bands);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.font = `${Math.round(Math.min(width, height) * .78)}px "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif`;
  context.fillText(PLACEHOLDER_EMOJIS[Math.floor(Math.random() * PLACEHOLDER_EMOJIS.length)], width / 2, height / 2);
  const pixels = context.getImageData(0, 0, width, height).data;

  const data = new Float32Array(bands * width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const pixel = y * width + x;
      const alpha = pixels[pixel * 4 + 3] / 255;
      const background = .12 + .22 * ((x / width + y / height) / 2);
      const channel = (index) => background * (1 - alpha) + (pixels[pixel * 4 + index] / 255) * alpha;
      const brightness = (channel(0) + channel(1) + channel(2)) / 3;
      for (let band = 0; band < bands; band += 1) {
        data[band * width * height + pixel] = bands >= 3 && band < 3 ? channel(band) : brightness;
      }
    }
  }
  return { width, height, bands, data };
}

function stretchRaster({ width, height, bands, data }) {
  const pixels = width * height;
  const stretched = new Float32Array(data.length);
  for (let band = 0; band < bands; band += 1) {
    let low = Infinity;
    let high = -Infinity;
    for (let index = band * pixels; index < (band + 1) * pixels; index += 1) {
      if (!Number.isFinite(data[index])) continue;
      low = Math.min(low, data[index]);
      high = Math.max(high, data[index]);
    }
    const span = high > low ? high - low : 1;
    for (let index = band * pixels; index < (band + 1) * pixels; index += 1) {
      stretched[index] = Number.isFinite(data[index]) ? (data[index] - low) / span : NaN;
    }
  }
  return { width, height, bands, data: stretched };
}

function colorizeRaster({ width, height, bands, data }) {
  const pixels = width * height;
  const image = new ImageData(width, height);
  for (let index = 0; index < pixels; index += 1) {
    const rgb = bands >= 3
      ? [data[index], data[pixels + index], data[2 * pixels + index]].map((value) => Math.round(value * 255))
      : viridis(data[index]);
    image.data.set([...rgb, Number.isFinite(data[index]) ? 255 : 0], index * 4);
  }
  return image;
}

function viridis(value) {
  const stops = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
  const position = Math.max(0, Math.min(1, value)) * (stops.length - 1);
  const lower = Math.min(stops.length - 2, Math.floor(position));
  const fraction = position - lower;
  return stops[lower].map((channel, index) => Math.round(channel + (stops[lower + 1][index] - channel) * fraction));
}

function showDatasetMetadata() {
  if (!state.dataset || state.fixtureIndex < 0) return;
  state.metadataToken += 1;
  clearSelectedPoint();
  state.panelMode = "dataset";
  state.metadataPages = [];
  state.metadataPageIndex = -1;

  const fixture = state.fixtures[state.fixtureIndex];
  const collection = state.dataset.collection;
  element.metadataPanel.classList.add("dataset-mode", "open");
  element.metadataPanel.setAttribute("aria-hidden", "false");
  element.pointPosition.textContent = `Dataset · ${topologyLabel(fixture.topology)}`;
  element.pointTitle.textContent = String(collection.title || collection.id);
  element.metadataPath.replaceChildren();
  element.metadataSource.textContent = "COLLECTION.json";
  element.metadataCount.textContent = `${state.dataset.levels.length} metadata ${state.dataset.levels.length === 1 ? "level" : "levels"}`;
  element.metadataBody.replaceChildren();

  appendDatasetOverview(fixture, collection);
  appendContractGraph();
  appendContractSchemas();
}

function appendDatasetOverview(fixture, collection) {
  const providers = (collection.providers || []).map((provider) =>
    typeof provider === "string" ? provider : provider.name || provider.url || "provider",
  );
  appendMetadataRecord({
    title: "Collection",
    values: compactObject({
      id: collection.id,
      format_version: collection["taco:version"],
      title: collection.title,
      description: collection.description,
      licenses: collection.licenses,
      providers,
      tasks: collection.tasks,
      samples: collection["taco:sources"]?.samples ?? state.points.length,
      partitions: collection["taco:sources"]?.partitions?.length,
      container: topologyLabel(fixture.topology),
      source: state.dataset.url,
    }),
  });

  const extent = collection.extent;
  if (extent && typeof extent === "object") {
    const spatial = extent.spatial?.bbox ?? extent.spatial;
    const temporal = extent.temporal?.interval ?? extent.temporal;
    const coverage = compactObject({
      spatial_bbox: Array.isArray(spatial) ? spatial.flat(1).join(", ") : spatial,
      temporal_interval: Array.isArray(temporal)
        ? temporal.map((interval) => Array.isArray(interval) ? interval.map((value) => value ?? "open").join(" → ") : interval ?? "open").join(" → ")
        : temporal,
    });
    if (Object.keys(coverage).length) appendMetadataRecord({ title: "Coverage", values: coverage });
  }
}

function appendContractGraph() {
  const section = document.createElement("section");
  section.className = "dataset-section";
  const heading = document.createElement("h3");
  heading.textContent = "Contract graph";
  const graph = document.createElement("div");
  graph.className = "contract-graph";

  state.dataset.levels.forEach((level) => {
    const fields = Object.keys(state.dataset.contract.metadata[level] || {}).length;
    graph.append(contractNode("▦", levelFilename(level), `${fields} ${fields === 1 ? "field" : "fields"}`, metadataLevelDepth(level)));
  });

  state.dataset.structure.forEach((declaration) => {
    const depth = 2 + (declaration.match(/\//g)?.length ?? 0);
    graph.append(contractNode("◆", declaration, "payload", depth, true));
  });
  section.append(heading, graph);
  element.metadataBody.append(section);
}

function contractNode(icon, label, detail, depth, payload = false) {
  const node = document.createElement("div");
  node.className = `contract-node${payload ? " payload" : ""}`;
  node.style.setProperty("--depth", String(depth));
  const mark = document.createElement("span");
  mark.className = "node-icon";
  mark.textContent = icon;
  const name = document.createElement("span");
  name.textContent = label;
  const description = document.createElement("span");
  description.className = "node-detail";
  description.textContent = detail;
  node.append(mark, name, description);
  return node;
}

function appendContractSchemas() {
  const section = document.createElement("section");
  section.className = "dataset-section";
  const heading = document.createElement("h3");
  heading.textContent = "Metadata schemas";
  section.append(heading);

  state.dataset.levels.forEach((level) => {
    const fields = Object.entries(state.dataset.contract.metadata[level] || {});
    const card = document.createElement("article");
    card.className = "schema-card";
    const header = document.createElement("header");
    const name = document.createElement("strong");
    name.textContent = levelFilename(level);
    const count = document.createElement("span");
    count.textContent = `${fields.length} ${fields.length === 1 ? "field" : "fields"}`;
    header.append(name, count);
    card.append(header);

    fields.forEach(([fieldName, declaration]) => {
      const field = document.createElement("div");
      field.className = "schema-field";
      const line = document.createElement("div");
      const code = document.createElement("code");
      code.textContent = fieldName;
      const type = document.createElement("span");
      type.textContent = `${declaration.type}${declaration.nullable ? " · nullable" : ""}`;
      line.append(code, type);
      const description = document.createElement("p");
      description.textContent = declaration.description;
      field.append(line, description);
      card.append(field);
    });
    section.append(card);
  });
  element.metadataBody.append(section);
}

function findCompatibleFixture(start, direction, preferredTopology) {
  for (let step = 1; step <= state.fixtures.length; step += 1) {
    const index = (start + direction * step + state.fixtures.length) % state.fixtures.length;
    const fixture = state.fixtures[index];
    if (state.centroidCases.has(fixture.case) && fixture.topology === preferredTopology) return index;
  }
  for (let step = 1; step <= state.fixtures.length; step += 1) {
    const index = (start + direction * step + state.fixtures.length) % state.fixtures.length;
    if (state.centroidCases.has(state.fixtures[index].case)) return index;
  }
  return -1;
}

function closeMetadata() {
  state.metadataToken += 1;
  element.metadataPanel.classList.remove("open");
  element.metadataPanel.setAttribute("aria-hidden", "true");
  clearSelectedPoint();
  state.panelMode = null;
  state.metadataPages = [];
  state.metadataPageIndex = -1;
}

function clearSelectedPoint() {
  if (state.selectedPoint >= 0 && map.getSource(POINT_SOURCE)) {
    map.setFeatureState({ source: POINT_SOURCE, id: state.selectedPoint }, { selected: false });
  }
  state.selectedPoint = -1;
}

function clearMarkers() {
  clearPreview();
  if (map.getLayer(POINT_LAYER)) map.removeLayer(POINT_LAYER);
  if (map.getSource(POINT_SOURCE)) map.removeSource(POINT_SOURCE);
  state.pointData = null;
  element.plotLegend.hidden = true;
}

function decodePoint(value) {
  // Keep old WKB fixtures readable.
  if (value && typeof value === "object" && "lon" in value && "lat" in value) {
    return lonLatOrNull(Number(value.lon), Number(value.lat));
  }
  let bytes;
  if (value instanceof Uint8Array) bytes = value;
  else if (value instanceof ArrayBuffer) bytes = new Uint8Array(value);
  else if (ArrayBuffer.isView(value)) bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  else return null;
  if (bytes.byteLength < 21) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const littleEndian = view.getUint8(0) === 1;
  const rawType = view.getUint32(1, littleEndian);
  const type = rawType & 0xff;
  if (type !== 1) return null;
  const coordinateOffset = 5 + ((rawType & 0x20000000) === 0 ? 0 : 4);
  if (bytes.byteLength < coordinateOffset + 16) return null;
  return lonLatOrNull(view.getFloat64(coordinateOffset, littleEndian), view.getFloat64(coordinateOffset + 8, littleEndian));
}

function lonLatOrNull(longitude, latitude) {
  return Number.isFinite(longitude) && Number.isFinite(latitude)
    && longitude >= -180 && longitude <= 180 && latitude >= -90 && latitude <= 90
    ? [longitude, latitude]
    : null;
}

function locationKey(sourceFile, path) {
  return `${sourceFile ?? ""}\0${path ?? ""}`;
}

function sameSource(left, right) {
  return (left ?? "") === (right ?? "");
}

function rowIdentity(row) {
  return locationKey(row["internal:source_file"], Number(row["internal:current_id"]));
}

function parentIdentity(row) {
  return locationKey(row["internal:source_file"], Number(row["internal:parent_id"]));
}

function contractPath(path) {
  if (typeof path !== "string") return "";
  const slash = path.indexOf("/");
  return slash < 0 ? "" : path.slice(slash + 1);
}

function levelFilename(level) {
  return `${level.replaceAll("/", "__")}.parquet`;
}

function parentMetadataLevel(level) {
  if (level === "children") return "sample";
  const folder = level.slice("children/".length);
  const slash = folder.lastIndexOf("/");
  return slash < 0 ? "children" : `children/${folder.slice(0, slash)}`;
}

function metadataLevelDepth(level) {
  if (level === "sample") return 0;
  return 1 + (level.match(/\//g)?.length ?? 0);
}

function compactObject(values) {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null));
}

function orderedMetadataEntries(values) {
  const entries = Object.entries(values);
  const priority = ["id", "title", "description", "path", "taco:sample_index", "current_id", "parent_id", "source_file", "offset", "size"];
  const first = priority.flatMap((key) => entries.filter(([name]) => name === key));
  const middle = entries.filter(([name]) => !priority.includes(name) && name !== "taco:location");
  const location = entries.filter(([name]) => name === "taco:location");
  return [...first, ...middle, ...location];
}

function metadataMessage(text) {
  const paragraph = document.createElement("p");
  paragraph.className = "metadata-empty";
  paragraph.textContent = text;
  return paragraph;
}

function pointName(point) {
  return String(point.row["fixture:sample_key"] || `sample ${point.row["taco:sample_index"] ?? "—"}`);
}

function fixtureUrl(fixture) {
  return `${FIXTURE_ROOT}/${String(fixture.path).replace(/^\/+/, "")}`;
}

function normalizedDatasetUrl(value) {
  const text = String(value ?? "").trim();
  if (!text) throw new Error("Enter a TACO dataset URL.");
  const url = new URL(text);
  if (!new Set(["http:", "https:"]).has(url.protocol)) {
    throw new Error("The dataset URL must use HTTP or HTTPS.");
  }
  return url.href;
}

function shareablePlaygroundUrl(datasetUrl) {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("url", datasetUrl);
  return url.href;
}

function topologyLabel(value) {
  return ({ folder: "folder", "single-zip": "zip", "by-size": "cat / size", "by-split": "cat / split", "manual-catalog": "cat / manual" })[value] || value;
}

function formatValue(value) {
  if (value === null || value === undefined) return "—";
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return `binary · ${formatBytes(value.byteLength)}`;
  if (typeof value === "object") {
    return JSON.stringify(value, (_key, item) => typeof item === "bigint" ? item.toString() : item, 2);
  }
  return String(value);
}

function formatLatitude(value) { return `${Math.abs(value).toFixed(5)}° ${value >= 0 ? "N" : "S"}`; }
function formatLongitude(value) { return `${Math.abs(value).toFixed(5)}° ${value >= 0 ? "E" : "W"}`; }

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}

function basename(path) {
  const clean = String(path).split(/[?#]/, 1)[0];
  return clean.slice(clean.lastIndexOf("/") + 1);
}

function contentType(path) {
  const extension = basename(path).split(".").pop()?.toLowerCase();
  return ({
    json: "application/json",
    geojson: "application/geo+json",
    tif: "image/tiff",
    tiff: "image/tiff",
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
  })[extension] || "application/octet-stream";
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("Clipboard is unavailable in this browser.");
}

function disableDatasetNavigation(disabled) {
  element.fixtureSelect.disabled = disabled;
  element.datasetUrl.disabled = disabled;
  element.loadDataset.disabled = disabled;
  if (disabled) {
    element.plotField.disabled = true;
    element.plotMode.disabled = true;
  }
  else if (state.dataset) {
    element.plotField.disabled = false;
    element.plotMode.disabled = false;
  }
}

function setLoading(loading) {
  element.loading.hidden = !loading;
  if (loading) {
    setLoadingLabel("Reading TACO");
    element.downloadProgress.hidden = true;
    element.downloadBar.max = 1;
    element.downloadBar.value = 0;
    element.downloadPercent.textContent = "0%";
    element.downloadBytes.textContent = "0 B";
  }
}

function setLoadingLabel(text) {
  element.loadingLabel.textContent = text;
}

function updateDownloadProgress({ loaded, total }) {
  element.downloadProgress.hidden = false;
  element.downloadBar.max = Math.max(total, 1);
  element.downloadBar.value = loaded;
  element.downloadPercent.textContent = total > 0 ? `${Math.min(100, Math.round((loaded / total) * 100))}%` : "…";
  element.downloadBytes.textContent = total > 0
    ? `${formatBytes(loaded)} / ${formatBytes(total)}`
    : formatBytes(loaded);
}

function finishDownloadProgress() {
  const total = Number(element.downloadBar.max);
  if (total > 0) updateDownloadProgress({ loaded: total, total });
}

function setStatus(kind, text) {
  element.status.className = `status ${kind}`;
  element.status.querySelector("span").textContent = text;
}

function showMessage(text) {
  clearTimeout(state.messageTimer);
  element.message.textContent = text;
  element.message.hidden = false;
  state.messageTimer = setTimeout(() => { element.message.hidden = true; }, 5000);
}

function fail(error) {
  setLoading(false);
  disableDatasetNavigation(false);
  setStatus("error", "Could not read dataset");
  showMessage(messageOf(error));
}

function messageOf(error) {
  return error instanceof Error ? error.message : String(error);
}
