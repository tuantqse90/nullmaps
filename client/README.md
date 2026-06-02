# NullMaps JS client

One-import, Google/Goong-compatible client for NullMaps. Browser or Node (needs `fetch`).

```js
import { NullMaps } from "./nullmaps.js";

const nm = new NullMaps({ key: "YOUR_API_KEY" });   // baseUrl defaults to https://maps.nullshift.sh

// Directions (motorbike by default) — includes turn-by-turn steps
const route = await nm.directions("10.7725,106.6980", "10.7951,106.7218");
console.log(route.routes[0].legs[0].distance.text);          // "5.4 km"
route.routes[0].legs[0].steps.forEach(s => console.log(s.html_instructions));

// Distance matrix
await nm.distanceMatrix(["10.77,106.69"], ["10.76,106.68", "10.80,106.71"]);

// Geocode + viewport bias, reverse, autocomplete
await nm.geocode("nguyen hue", { location: "10.776,106.700" });
await nm.reverse(10.7725, 106.6980);
await nm.autocomplete("ben thanh", { location: "10.776,106.700" });

// AI address cleanup (opt-in)
await nm.geocode("Q1 P.Ben Nghe", { normalize: 1 });

// Fleet
await nm.optimizedRoute(["10.77,106.69", "10.82,106.63", "10.75,106.66", "10.79,106.72"]); // TSP
await nm.isochrone("10.7725,106.6980", [10, 20]);            // reachability polygons
await nm.snap(["10.7725,106.6980", "10.7760,106.7000"]);     // snap-to-roads
```

## Map features (MapLibre helpers)

```js
const map = nm.map(maplibregl, "map", { theme: "dark", controls: true });

// draw a route on the map
const route = await nm.directions("10.7725,106.6980", "10.7951,106.7218");
map.on("load", () => nm.renderRoute(map, route));            // polyline + fitBounds

// cluster many points (fleet / stations)
nm.addClusters(map, [{ lat: 10.77, lng: 106.69, id: "A" }, /* ... */]);

// overlay your own GeoJSON (showrooms/stations)
nm.addOverlay(map, myStationsGeoJSON, { color: "#163300" });
```

`map()` adds navigation / scale / geolocate / fullscreen controls by default (`controls: false` to skip).

### Static map image (client-side)

```js
// renders offscreen, returns a PNG data URL — no server renderer needed
const png = await nm.staticImage(maplibregl, {
  center: [106.70, 10.776], zoom: 13, size: [600, 400],
  markers: [{ lng: 106.70, lat: 10.776, color: "#00B260" }],
});
img.src = png;  // or upload the data URL
```

For backend/email-side rendering (no browser), a server GL renderer (maplibre-gl-native /
tileserver-gl) would be a separate service — deliberately not added to the shared box.

## Embed the map (MapLibre)

```html
<link href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css" rel="stylesheet" />
<script type="module">
  import maplibregl from "https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js";
  import { NullMaps } from "./nullmaps.js";
  const nm = new NullMaps({ key: "YOUR_API_KEY" });
  nm.map(maplibregl, "map");        // self-hosted VN basemap, centered on HCMC
</script>
<div id="map" style="height:100vh"></div>
```

## API docs

Interactive OpenAPI / Swagger UI: **https://maps.nullshift.sh/docs** (schema at `/openapi.json`).

## Notes

- Auth: the client sends `?key=`. You can also use header `X-API-Key`.
- `mode`/`vehicle`: unspecified → motorbike (`motor_scooter`); `driving`→car, `walking`, `bicycling`.
- Tiles/style/demo are read-only (no key); the API endpoints require the key.
