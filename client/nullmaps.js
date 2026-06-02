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

  // MapLibre helper — pass the imported maplibregl module + a container id/element
  map(maplibregl, container, opts = {}) {
    return new maplibregl.Map({
      container,
      style: this.base + "/style.json",
      center: [106.700, 10.776],
      zoom: 11,
      ...opts,
    });
  }
}

export default NullMaps;
