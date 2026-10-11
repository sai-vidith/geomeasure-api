// Small client for the geomeasure API. No build step, no framework.

const API = "/api/files/";

const COLORS = {
  polygon: "#b85c1e",
  line: "#1f6f78",
  point: "#1d2125",
  highlight: "#e0a100",
};

const AREA_UNITS = {
  m2: { label: "m²", factor: 1, digits: 1 },
  ha: { label: "ha", factor: 1 / 10_000, digits: 3 },
  ac: { label: "ac", factor: 1 / 4046.8564224, digits: 3 },
};

const FILE_TYPES = { shapefile: "Shapefile", kml: "KML", kmz: "KMZ" };

const state = {
  files: [],
  currentId: null,
  info: null,
  layer: null, // null = all layers
  measurements: null,
  featureLayers: new Map(), // feature index -> Leaflet layer
  activeIndex: null,
  areaUnit: "m2",
};

const $ = (id) => document.getElementById(id);

// ---------- Map ----------

const map = L.map("map", { zoomControl: true }).setView([20.6, 78.9], 4);

const basemaps = {
  // Standard OSM tiles, desaturated in CSS so survey shapes stand out against them.
  streets: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    className: "tiles-muted",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }),
  satellite: L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Imagery &copy; Esri" },
  ),
};
basemaps.streets.addTo(map);

document.querySelectorAll("[data-basemap]").forEach((button) => {
  button.addEventListener("click", () => {
    Object.values(basemaps).forEach((layer) => map.removeLayer(layer));
    basemaps[button.dataset.basemap].addTo(map);
    document.querySelectorAll("[data-basemap]").forEach((b) => b.classList.toggle("active", b === button));
  });
});

let dataLayer = L.featureGroup().addTo(map);

function styleFor(geometryType, active = false) {
  const type = (geometryType || "").toLowerCase();
  const color = active ? COLORS.highlight : type.includes("polygon") ? COLORS.polygon : type.includes("line") ? COLORS.line : COLORS.point;
  return { color, weight: active ? 3.5 : 2, fillColor: color, fillOpacity: type.includes("polygon") ? 0.18 : 0.9 };
}

function drawFeatures(collection) {
  map.removeLayer(dataLayer);
  dataLayer = L.featureGroup();
  state.featureLayers.clear();

  L.geoJSON(collection, {
    filter: (feature) => feature.geometry !== null,
    style: (feature) => styleFor(feature.properties._geometry_type),
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, { radius: 5, ...styleFor("point") }),
    onEachFeature: (feature, layer) => {
      state.featureLayers.set(feature.id, layer);
      layer.bindTooltip(tooltipText(feature), { sticky: true });
      layer.on("click", () => selectFeature(feature.id, { fromMap: true }));
      dataLayer.addLayer(layer);
    },
  });

  dataLayer.addTo(map);
  if (dataLayer.getLayers().length) {
    map.fitBounds(dataLayer.getBounds(), { padding: [24, 24], maxZoom: 18 });
  }
}

function tooltipText(feature) {
  const p = feature.properties;
  const parts = [escapeHtml(featureName(p) || `Feature ${feature.id}`)];
  if (p._area_m2 != null) parts.push(formatArea(p._area_m2));
  if (p._length_m != null) parts.push(formatLength(p._length_m));
  return parts.join("<br>");
}

// ---------- Formatting ----------

const numberFormat = (digits) => new Intl.NumberFormat(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

function formatArea(m2) {
  if (m2 == null) return "";
  const unit = AREA_UNITS[state.areaUnit];
  return `${numberFormat(unit.digits).format(m2 * unit.factor)} ${unit.label}`;
}

function formatLength(m) {
  if (m == null) return "";
  return m >= 1000 ? `${numberFormat(3).format(m / 1000)} km` : `${numberFormat(1).format(m)} m`;
}

function featureName(properties) {
  return properties.name ?? properties.Name ?? properties.NAME ?? null;
}

function escapeHtml(value) {
  // File names and attributes come from user uploads; never inject them as raw HTML.
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function formatDate(iso) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// ---------- API ----------

async function request(url, options) {
  const response = await fetch(url, options);
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok && response.status !== 422) {
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
  }
  return body;
}

// ---------- File list ----------

async function loadFiles() {
  state.files = await request(API);
  renderFileList();
}

function renderFileList() {
  const list = $("file-list");
  if (!state.files.length) {
    list.innerHTML = '<li class="empty-note">Nothing uploaded yet.</li>';
    return;
  }
  list.innerHTML = state.files.map((f) => `
    <li data-id="${f.id}" class="${f.id === state.currentId ? "active" : ""}">
      <span class="file-name" title="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}</span>
      <span class="status status-${f.status}">${f.status.toLowerCase()}</span>
      <span class="file-sub">${f.feature_count} features · ${escapeHtml(f.crs ?? "no CRS")} · ${formatDate(f.created_at)}</span>
    </li>`).join("");
  list.querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => openFile(li.dataset.id)));
}

// ---------- Upload ----------

const dropzone = $("dropzone");
const fileInput = $("file-input");

fileInput.addEventListener("change", () => fileInput.files[0] && uploadFile(fileInput.files[0]));
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});

["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => {
  event.preventDefault();
  dropzone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragging");
}));
dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer.files[0];
  if (file) uploadFile(file);
});

async function uploadFile(file) {
  const status = $("upload-status");
  status.className = "upload-status";
  status.textContent = `Processing ${file.name}…`;
  dropzone.classList.add("busy");

  const form = new FormData();
  form.append("file", file);
  try {
    const record = await request(API, { method: "POST", body: form });
    status.textContent = record.status === "FAILED" ? `${file.name} could not be read.` : `${file.name}: ${record.feature_count} features.`;
    status.classList.toggle("error", record.status === "FAILED");
    await loadFiles();
    await openFile(record.id);
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    dropzone.classList.remove("busy");
    fileInput.value = "";
  }
}

// ---------- Samples ----------

async function loadSamples() {
  const samples = await request("/api/samples/");
  if (!samples.length) return;
  const box = $("samples");
  box.hidden = false;
  box.innerHTML = "No file handy? Try a sample:" + samples.map((s, i) => `<button type="button" data-sample="${i}">${escapeHtml(s.name)}</button>`).join("");
  box.querySelectorAll("[data-sample]").forEach((button) => button.addEventListener("click", async () => {
    const sample = samples[Number(button.dataset.sample)];
    const blob = await (await fetch(sample.url)).blob();
    uploadFile(new File([blob], sample.name));
  }));
}

// ---------- File details ----------

async function openFile(id) {
  state.currentId = id;
  state.activeIndex = null;
  state.layer = null;
  history.replaceState(null, "", `#${id}`); // makes the current file linkable
  renderFileList();

  const info = await request(`${API}${id}/`);
  state.info = info;
  $("empty-state").hidden = true;
  $("details").hidden = false;
  $("file-title").textContent = info.filename;
  const layerNote = info.layers.length > 1 ? ` · ${info.layers.length} layers` : "";
  $("file-meta").textContent = `${FILE_TYPES[info.file_type] ?? info.file_type} · ${info.feature_count} features · CRS ${info.crs ?? "unknown"}${layerNote}`;

  renderWarnings(info.warnings);
  const errorBox = $("error-box");
  errorBox.hidden = info.status !== "FAILED";
  errorBox.textContent = info.error ?? "";

  $("result-actions").hidden = info.status !== "COMPLETED";
  renderLayerFilter();

  if (info.status !== "COMPLETED") {
    state.measurements = null;
    $("summary").innerHTML = "";
    $("feature-rows").innerHTML = "";
    drawFeatures({ type: "FeatureCollection", features: [] });
    return;
  }

  await loadResults();
}

// Fetches measurements and geometry for the current file, limited to the chosen layer.
async function loadResults() {
  const id = state.currentId;
  const params = new URLSearchParams();
  if (state.layer) params.set("layer", state.layer);
  const query = params.size ? `?${params}` : "";

  const [measurements, features] = await Promise.all([
    request(`${API}${id}/measurements/${query}`),
    request(`${API}${id}/features/${query}`),
  ]);
  if (id !== state.currentId) return; // another file was opened while this one loaded

  state.measurements = measurements;
  state.activeIndex = null;
  state.names = new Map(features.features.map((f) => [f.id, featureName(f.properties)]));
  renderSummary();
  renderTable();
  drawFeatures(features);

  // Downloads follow the same filter as the table.
  $("download-csv").href = `${API}${id}/export/${query}`;
  params.set("format", "geojson");
  $("download-geojson").href = `${API}${id}/export/?${params}`;
}

function renderLayerFilter() {
  const box = $("layer-filter");
  const layers = state.info?.status === "COMPLETED" ? state.info.layers : [];
  box.hidden = layers.length < 2;
  if (box.hidden) return;

  const chips = [{ name: null, label: "All layers", count: state.info.feature_count }]
    .concat(layers.map((l) => ({ name: l.name, label: l.name, count: l.feature_count })));
  box.innerHTML = chips.map((chip, i) => `
    <button type="button" class="chip ${chip.name === state.layer ? "active" : ""}" data-chip="${i}"
            aria-pressed="${chip.name === state.layer}">${escapeHtml(chip.label)}<span>${chip.count}</span></button>`).join("");
  box.querySelectorAll("[data-chip]").forEach((button) => button.addEventListener("click", async () => {
    state.layer = chips[Number(button.dataset.chip)].name;
    renderLayerFilter();
    await loadResults();
  }));
}

function renderWarnings(warnings) {
  const box = $("warnings");
  box.hidden = !warnings.length;
  box.innerHTML = warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("");
}

function renderSummary() {
  const s = state.measurements.summary;
  const items = [
    ["Total area", formatArea(s.total_area_m2) || "–"],
    ["Total length", s.total_length_m ? formatLength(s.total_length_m) : "–"],
    ["Measured", `${s.measured_features} of ${s.measured_features + s.unmeasured_features}`],
    ["Geometry", Object.entries(s.geometry_types).map(([t, n]) => `${n} ${t}`).join(", ") || "–"],
  ];
  $("summary").innerHTML = items.map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
}

function renderTable() {
  const rows = state.measurements.features.map((f) => `
    <tr data-index="${f.index}" class="${f.index === state.activeIndex ? "active" : ""}">
      <td class="dim">${f.index}</td>
      <td>${escapeHtml(state.names.get(f.index) ?? "")}</td>
      <td>${escapeHtml(f.layer)}</td>
      <td>${escapeHtml(f.geometry_type ?? "none")}</td>
      <td class="num">${formatArea(f.area_m2)}</td>
      <td class="num">${formatLength(f.perimeter_m)}</td>
      <td class="num">${formatLength(f.length_m)}</td>
      <td class="dim">${escapeHtml(f.measurement_crs ?? "")}</td>
      <td class="note">${escapeHtml(f.note ?? "")}</td>
    </tr>`).join("");
  $("feature-rows").innerHTML = rows;
  $("feature-rows").querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", () => selectFeature(Number(tr.dataset.index))));
}

function selectFeature(index, { fromMap = false } = {}) {
  const previous = state.featureLayers.get(state.activeIndex);
  if (previous?.setStyle) previous.setStyle(styleFor(previous.feature.properties._geometry_type));

  state.activeIndex = index;
  document.querySelectorAll("#feature-rows tr").forEach((tr) => tr.classList.toggle("active", Number(tr.dataset.index) === index));

  const layer = state.featureLayers.get(index);
  if (!layer) return;
  if (layer.setStyle) {
    layer.setStyle(styleFor(layer.feature.properties._geometry_type, true));
    layer.bringToFront?.();
  }
  if (fromMap) {
    document.querySelector(`#feature-rows tr[data-index="${index}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } else if (layer.getBounds) {
    map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 18 });
  } else if (layer.getLatLng) {
    map.setView(layer.getLatLng(), Math.max(map.getZoom(), 16));
  }
}

$("area-unit").addEventListener("change", (event) => {
  state.areaUnit = event.target.value;
  if (state.measurements) {
    renderSummary();
    renderTable();
  }
});

$("delete-btn").addEventListener("click", async () => {
  const file = state.files.find((f) => f.id === state.currentId);
  if (!file || !confirm(`Delete ${file.filename}?`)) return;
  await request(`${API}${file.id}/`, { method: "DELETE" });
  state.currentId = null;
  $("details").hidden = true;
  $("empty-state").hidden = false;
  drawFeatures({ type: "FeatureCollection", features: [] });
  await loadFiles();
});

loadSamples().catch(() => {}); // samples are optional; the page works without them

loadFiles()
  .then(() => {
    const id = location.hash.slice(1);
    if (id && state.files.some((f) => f.id === id)) return openFile(id);
  })
  .catch((error) => {
    $("file-list").innerHTML = `<li class="empty-note">${escapeHtml(error.message)}</li>`;
  });
