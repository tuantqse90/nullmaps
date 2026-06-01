# services/geocoder — Geocoding, Reverse, Autocomplete (Phase 3)

> **Status: scaffolded, not built.** Lands in Phase 3.

**What:** Address ↔ coordinate lookup and address typeahead via **Photon** (OSM + Elasticsearch).

**Why:** Address typeahead for my app forms, plus geocoding/reverse for routing inputs.

## Plan

- Import the VN OSM data into Photon.
- Expose `/geocode`, `/reverse`, `/autocomplete`.
- **VN tuning:** Photon is partial-token friendly out of the box; enable **diacritic folding** in the
  VN analyzer so `nguyen` matches `Nguyễn`.
- Pelias is the heavier fallback only if Photon proves insufficient.

## When implementing

- Uncomment the `photon` service in `docker-compose.yml`; persist `photon_data` volume (RAM-hungry —
  update README sizing).
