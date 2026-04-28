const map = L.map('map', { preferCanvas: true }).setView([-14.2, -51.9], 5);
window.map = map;  // exposed for dashboards / screenshots that want to reposition
window.__state = { cellLayers: null, seenPlaces: null };
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap'
}).addTo(map);

const legend = L.control({ position: 'bottomright' });
legend.onAdd = function () {
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = `
    <div><span class="sw" style="background:#9333EA"></span>done</div>
    <div><span class="sw" style="background:#d8b4fe"></span>in progress</div>
    <div><span class="sw" style="background:#ef4444"></span>error</div>
    <div><span class="sw" style="background:transparent"></span>pending</div>
  `;
  return div;
};
legend.addTo(map);

const STYLE = {
  pending:      { color: '#4c1d95', weight: 0.3, fillOpacity: 0.0 },
  in_progress:  { color: '#a855f7', weight: 0.8, fillColor: '#d8b4fe', fillOpacity: 0.35 },
  done:         { color: '#7c3aed', weight: 0.6, fillColor: '#9333EA', fillOpacity: 0.45 },
  subdivided:   { color: '#7c3aed', weight: 0.3, fillOpacity: 0.0 },
  error:        { color: '#ef4444', weight: 0.8, fillColor: '#ef4444', fillOpacity: 0.35 },
};

const cellLayers = {};     // cell_id -> L.Layer
const seenPlaces = new Set();
let lastPlaceId = 0;
let placesLayer = L.layerGroup().addTo(map);
let cellsGroup = L.layerGroup().addTo(map);

function styleFor(status) { return STYLE[status] || STYLE.pending; }

function renderCellsGeoJSON(geojson) {
  const seen = new Set();
  geojson.features.forEach(f => {
    const id = f.properties.cell_id;
    const status = f.properties.status;
    seen.add(id);
    const existing = cellLayers[id];
    if (existing) {
      if (existing._status !== status) {
        existing.setStyle(styleFor(status));
        existing._status = status;
      }
      return;
    }
    const layer = L.geoJSON(f, { style: styleFor(status) });
    layer._status = status;
    layer.bindTooltip(`${status} · ${f.properties.result_count || 0} results`, { sticky: true });
    layer.addTo(cellsGroup);
    cellLayers[id] = layer;
  });
  // Remove layers whose cells disappeared (shouldn't happen but safe)
  Object.keys(cellLayers).forEach(id => {
    if (!seen.has(id)) {
      cellsGroup.removeLayer(cellLayers[id]);
      delete cellLayers[id];
    }
  });
}

function markerColor(rating) {
  if (rating == null) return '#94a3b8';
  if (rating >= 4.5) return '#22c55e';
  if (rating >= 4.0) return '#84cc16';
  if (rating >= 3.0) return '#eab308';
  return '#f97316';
}

function renderPlaces(items) {
  items.forEach(p => {
    if (seenPlaces.has(p.id)) return;
    if (p.lat == null || p.lon == null) return;
    seenPlaces.add(p.id);
    const m = L.circleMarker([p.lat, p.lon], {
      radius: 4, color: '#1e1b4b', weight: 0.5,
      fillColor: markerColor(p.rating), fillOpacity: 0.9,
    });
    const safeName = (p.name || 'Sem nome').replace(/</g, '&lt;');
    const safeAddr = (p.address || '').replace(/</g, '&lt;');
    const safePhone = (p.phone || '').replace(/</g, '&lt;');
    const safeStatus = (p.open_status || '').replace(/</g, '&lt;');
    const rating = p.rating != null ? `⭐ ${p.rating}${p.reviews_count ? ' (' + p.reviews_count + ')' : ''}` : '';
    const phone = safePhone ? `<br>${safePhone}` : '';
    const status = safeStatus ? `<br>${safeStatus}` : '';
    const website = p.website ? `<br><a href="${p.website}" target="_blank">Website</a>` : '';
    m.bindPopup(`<b>${safeName}</b><br>${rating}${status}<br>${safeAddr}${phone}${website}<br><a href="${p.url}" target="_blank">Abrir no Maps</a>`);
    m.addTo(placesLayer);
  });
}

function renderSummary(summary, totalPlaces) {
  const total = Object.values(summary).reduce((a, b) => a + b, 0);
  document.getElementById('s-cells').textContent = total;
  document.getElementById('s-done').textContent = summary.done || 0;
  document.getElementById('s-sub').textContent = summary.subdivided || 0;
  document.getElementById('s-prog').textContent = summary.in_progress || 0;
  document.getElementById('s-pend').textContent = summary.pending || 0;
  document.getElementById('s-places').textContent = totalPlaces;
}

let cellTickBusy = false;
async function tickCells() {
  if (cellTickBusy) return;
  cellTickBusy = true;
  try {
    const res = await fetch('/api/cells');
    if (res.ok) renderCellsGeoJSON(await res.json());
  } catch (e) { /* transient */ }
  cellTickBusy = false;
}

let placeTickBusy = false;
async function tickPlaces() {
  if (placeTickBusy) return;
  placeTickBusy = true;
  try {
    const res = await fetch('/api/places?since=' + lastPlaceId);
    if (res.ok) {
      const data = await res.json();
      renderPlaces(data.items || []);
      renderSummary(data.summary || {}, seenPlaces.size);
      const maxId = Number(data.max_id || 0);
      if (Number.isFinite(maxId) && maxId > lastPlaceId) lastPlaceId = maxId;
    }
  } catch (e) { /* transient */ }
  placeTickBusy = false;
}

tickCells();
tickPlaces();
setInterval(tickCells, 2500);
setInterval(tickPlaces, 1500);
