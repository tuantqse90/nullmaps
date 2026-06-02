// NullMaps JS client — Google/Goong-compatible, one import. Browser + Node (fetch).
//
//   import { NullMaps } from "./nullmaps.js";
//   const nm = new NullMaps({ key: "YOUR_KEY" });   // baseUrl defaults to maps.nullshift.sh
//   const route = await nm.directions("10.7725,106.6980", "10.7951,106.7218");
//   nm.map(maplibregl, "map");                       // embed the self-hosted basemap

export class NullMaps {
  constructor({ baseUrl = "https://maps.nullshift.sh", key } = {}) {
    if (!key) throw new Error("NullMaps: `key` is required");
    this.base = baseUrl.replace(/\/+$/, "");
    this.key = key;
  }

  async _get(path, params = {}) {
    const u = new URL(this.base + path);
    u.searchParams.set("key", this.key);
    for (const [k, v] of Object.entries(params)) if (v != null && v !== "") u.searchParams.set(k, v);
    const r = await fetch(u);
    if (!r.ok) throw new Error(`NullMaps ${path} -> HTTP ${r.status}`);
    return r.json();
  }

  // origin/destination: "lat,lng". opts: { mode, waypoints, normalize, location }
  directions(origin, destination, opts = {}) {
    return this._get("/maps/api/directions/json", { origin, destination, ...opts });
  }

  // origins/destinations: "lat,lng" or array of them
  distanceMatrix(origins, destinations, opts = {}) {
    return this._get("/maps/api/distancematrix/json", {
      origins: [].concat(origins).join("|"),
      destinations: [].concat(destinations).join("|"),
      ...opts,
    });
  }

  geocode(address, opts = {}) { return this._get("/maps/api/geocode/json", { address, ...opts }); }
  reverse(lat, lng, opts = {}) { return this._get("/maps/api/geocode/json", { latlng: `${lat},${lng}`, ...opts }); }
  autocomplete(input, opts = {}) { return this._get("/maps/api/place/autocomplete/json", { input, ...opts }); }

  // fleet extras
  isochrone(location, contours, opts = {}) {
    return this._get("/v1/isochrone", { location, contours: [].concat(contours).join(","), ...opts });
  }
  snap(pathPts, opts = {}) { return this._get("/v1/snap", { path: [].concat(pathPts).join("|"), ...opts }); }

  // optimized multi-stop route (TSP): stops = ["lat,lng", ...]
  optimizedRoute(stops, opts = {}) {
    const [origin, ...rest] = stops;
    const destination = rest.pop();
    const waypoints = ["optimize:true", ...rest].join("|");
    return this.directions(origin, destination, { waypoints, ...opts });
  }

  // MapLibre helper — pass the imported maplibregl module + a container id/element.
  // opts.theme "dark" uses the dark style; opts.controls adds nav/scale/geolocate/fullscreen.
  map(maplibregl, container, opts = {}) {
    const { theme = "light", controls = true, ...mapOpts } = opts;
    const m = new maplibregl.Map({
      container,
      style: `${this.base}/${theme === "dark" ? "style-dark.json" : "style.json"}`,
      center: [106.700, 10.776],
      zoom: 11,
      ...mapOpts,
    });
    if (controls) {
      m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
      m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
      m.addControl(new maplibregl.GeolocateControl({ trackUserLocation: true }), "top-right");
      if (maplibregl.FullscreenControl) m.addControl(new maplibregl.FullscreenControl(), "top-right");
    }
    return m;
  }

  // Draw a /directions result on the map (overview polyline + start/end dots).
  // Returns the source id; call again to update (it replaces).
  renderRoute(map, directionsResponse, opts = {}) {
    const { id = "nm-route", color = "#00B260", width = 5, fit = true } = opts;
    const route = directionsResponse?.routes?.[0];
    if (!route) return null;
    const coords = NullMaps.decodePolyline(route.overview_polyline.points).map(([lat, lng]) => [lng, lat]);
    const data = { type: "Feature", geometry: { type: "LineString", coordinates: coords } };
    const src = map.getSource(id);
    if (src) { src.setData(data); }
    else {
      map.addSource(id, { type: "geojson", data });
      map.addLayer({ id: `${id}-casing`, type: "line", source: id,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": width + 3 } });
      map.addLayer({ id: `${id}-line`, type: "line", source: id,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": color, "line-width": width } });
    }
    if (fit && coords.length) {
      const lons = coords.map((c) => c[0]), lats = coords.map((c) => c[1]);
      map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
        { padding: 60, duration: 600 });
    }
    return id;
  }

  // Clustered points layer. points: [{lat,lng, ...props}]. Good for fleets/stations.
  addClusters(map, points, opts = {}) {
    const { id = "nm-cluster", color = "#00B260" } = opts;
    const data = { type: "FeatureCollection", features: points.map((p) => ({
      type: "Feature", properties: { ...p }, geometry: { type: "Point", coordinates: [p.lng, p.lat] } })) };
    if (map.getSource(id)) { map.getSource(id).setData(data); return id; }
    map.addSource(id, { type: "geojson", data, cluster: true, clusterRadius: 50 });
    map.addLayer({ id: `${id}-clusters`, type: "circle", source: id, filter: ["has", "point_count"],
      paint: { "circle-color": color, "circle-opacity": 0.85,
        "circle-radius": ["step", ["get", "point_count"], 16, 25, 22, 100, 30] } });
    map.addLayer({ id: `${id}-count`, type: "symbol", source: id, filter: ["has", "point_count"],
      layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Noto Sans Bold"], "text-size": 12 },
      paint: { "text-color": "#ffffff" } });
    map.addLayer({ id: `${id}-point`, type: "circle", source: id, filter: ["!", ["has", "point_count"]],
      paint: { "circle-color": color, "circle-radius": 6, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    return id;
  }

  // Add a custom GeoJSON overlay (e.g. your showrooms/stations).
  addOverlay(map, geojson, opts = {}) {
    const { id = "nm-overlay", color = "#163300", radius = 7 } = opts;
    if (map.getSource(id)) { map.getSource(id).setData(geojson); return id; }
    map.addSource(id, { type: "geojson", data: geojson });
    map.addLayer({ id: `${id}-pts`, type: "circle", source: id,
      paint: { "circle-color": color, "circle-radius": radius, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    return id;
  }

  // Encoded-polyline decoder (precision 5 by default) -> [[lat,lng], ...]
  static decodePolyline(str, precision = 5) {
    let index = 0, lat = 0, lng = 0;
    const coords = [], factor = Math.pow(10, precision);
    while (index < str.length) {
      let result = 1, shift = 0, b;
      do { b = str.charCodeAt(index++) - 63 - 1; result += b << shift; shift += 5; } while (b >= 0x1f);
      lat += result & 1 ? ~(result >> 1) : result >> 1;
      result = 1; shift = 0;
      do { b = str.charCodeAt(index++) - 63 - 1; result += b << shift; shift += 5; } while (b >= 0x1f);
      lng += result & 1 ? ~(result >> 1) : result >> 1;
      coords.push([lat / factor, lng / factor]);
    }
    return coords;
  }
}

export default NullMaps;
