# NullMaps — operator commands. `make help` lists them.
# Loads .env if present (falls back to .env.example defaults via the recipes).

ifneq (,$(wildcard .env))
include .env
export
endif

OSM_EXTRACT_URL ?= https://download.geofabrik.de/asia/vietnam-latest.osm.pbf
DATA_DIR        ?= ./data
RAW_DIR         ?= ./data/raw
PMTILES_FILE    ?= vietnam.pmtiles
VALHALLA_PORT   ?= 8002
PBF             := $(RAW_DIR)/vietnam-latest.osm.pbf

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(PBF): ## (internal) download Vietnam OSM extract — default data source
	@mkdir -p $(RAW_DIR)
	@echo ">> Downloading Vietnam extract (Geofabrik). NOT planet — VN only."
	curl -fSL --progress-bar -o $(PBF) "$(OSM_EXTRACT_URL)"

.PHONY: fetch
fetch: $(PBF) ## Download the Vietnam OSM extract into data/raw/

SRC_DIR  := $(DATA_DIR)/sources
LAKE_URL ?= https://github.com/lukasmartinelli/osm-lakelines/releases/download/v0.9/lake_centerline.shp.zip
NE_URL   ?= https://naciscdn.org/naturalearth/packages/natural_earth_vector.sqlite.zip
WATER_URL?= https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip

define fetch_source
	@test -s "$(SRC_DIR)/$(1)" && echo ">> have $(1)" || { \
	  echo ">> fetching $(1) (resumable; the osmdata water mirror is often slow/flaky)"; \
	  curl -fL --retry 8 --retry-delay 5 --retry-all-errors -C - \
	    --connect-timeout 30 --speed-time 60 --speed-limit 10000 \
	    -o "$(SRC_DIR)/$(1)" "$(2)"; }
endef

.PHONY: sources
sources: ## Pre-fetch Planetiler auxiliary basemap sources (NOT planet OSM) with retry/resume
	@mkdir -p $(SRC_DIR)
	$(call fetch_source,lake_centerline.shp.zip,$(LAKE_URL))
	$(call fetch_source,natural_earth_vector.sqlite.zip,$(NE_URL))
	$(call fetch_source,water-polygons-split-3857.zip,$(WATER_URL))

.PHONY: tiles
tiles: $(PBF) sources ## Build Vietnam PMTiles from the extract via Planetiler
	@echo ">> Planetiler: $(PBF) -> $(DATA_DIR)/$(PMTILES_FILE)"
	@echo ">> OSM stays LOCAL via --osm-path; auxiliary sources pre-fetched by 'make sources'."
	@echo ">> --download is a fallback only; planet OSM is never downloaded."
	docker run --rm \
	  -v "$(abspath $(DATA_DIR)):/data" \
	  ghcr.io/onthegomap/planetiler:latest \
	  --osm-path=/data/raw/vietnam-latest.osm.pbf \
	  --download \
	  --output=/data/$(PMTILES_FILE) \
	  --force
	@echo ">> Done. Tiles at $(DATA_DIR)/$(PMTILES_FILE)"

.PHONY: up
up: ## Start Phase 1 services (martin + demo) in the background
	docker compose up -d martin demo

.PHONY: demo
demo: ## Build tiles if missing, start services, print the demo URL
	@test -f "$(DATA_DIR)/$(PMTILES_FILE)" || $(MAKE) tiles
	docker compose up -d martin demo
	@echo ">> Demo:   http://localhost:$(DEMO_PORT)   (Ho Chi Minh City basemap)"
	@echo ">> Martin: http://localhost:$(MARTIN_PORT)/catalog"

ADAPTER_PORT ?= 8010

.PHONY: adapter-test
adapter-test: ## (Phase 4) Smoke-test the Google-compat adapter (Directions+Matrix live, geocode pending)
	@K=$$(grep -E '^API_KEY=' .env | cut -d= -f2); P=$(ADAPTER_PORT); \
	echo ">> directions (motorbike):"; \
	curl -fsS "http://localhost:$$P/maps/api/directions/json?origin=10.7725,106.6980&destination=10.7951,106.7218&key=$$K" \
	  | python3 -c "import sys,json;l=json.load(sys.stdin)['routes'][0]['legs'][0];print('   ',l['distance']['text'],l['duration']['text'])"; \
	echo ">> distancematrix (2x2):"; \
	curl -fsS "http://localhost:$$P/maps/api/distancematrix/json?origins=10.7725,106.6980|10.7800,106.7010&destinations=10.7626,106.6822|10.7691,106.7000&key=$$K" \
	  | python3 -c "import sys,json;b=json.load(sys.stdin);print('   ',[[e.get('distance',{}).get('text','-') for e in r['elements']] for r in b['rows']])"; \
	echo ">> geocode (expect 503 until Phase 3):"; \
	curl -s -o /dev/null -w "    HTTP %{http_code}\n" "http://localhost:$$P/maps/api/geocode/json?address=x&key=$$K"

.PHONY: down
down: ## Stop all services
	docker compose down

.PHONY: logs
logs: ## Tail service logs
	docker compose logs -f

.PHONY: graph
graph: ## (Phase 2) Build/start Valhalla — auto-builds the VN routing graph on first run
	@test -s services/routing/custom_files/vietnam-latest.osm.pbf || \
	  ln -f $(PBF) services/routing/custom_files/vietnam-latest.osm.pbf 2>/dev/null || \
	  cp $(PBF) services/routing/custom_files/
	docker compose up -d valhalla
	@echo ">> Valhalla starting on :$(VALHALLA_PORT). First run builds the graph (minutes)."
	@echo ">> Watch:  docker compose logs -f valhalla   |   Ready when GET /status returns tileset_last_modified"

.PHONY: route-test
route-test: ## (Phase 2) Smoke-test an HCMC motorbike route (costing=motor_scooter)
	@echo ">> HCMC: Ben Thanh Market -> Landmark 81, costing=motor_scooter"
	@curl -fsS http://localhost:$(VALHALLA_PORT)/route \
	  -H 'Content-Type: application/json' \
	  -d '{"locations":[{"lat":10.7725,"lon":106.6980},{"lat":10.7951,"lon":106.7218}],"costing":"motor_scooter","units":"kilometers"}' \
	  | python3 -c "import sys,json; s=json.load(sys.stdin)['trip']['summary']; print('>> OK: %.1f km, %.0f min'%(s['length'], s['time']/60))" \
	  || echo ">> Valhalla not ready — check: docker compose logs -f valhalla"

.PHONY: matrix-test
matrix-test: ## (Phase 2) Smoke-test a 2x2 distance matrix (costing=motor_scooter)
	@echo ">> HCMC District 1: 2 sources x 2 targets, motor_scooter"
	@curl -fsS http://localhost:$(VALHALLA_PORT)/sources_to_targets \
	  -H 'Content-Type: application/json' \
	  -d '{"sources":[{"lat":10.7725,"lon":106.6980},{"lat":10.7800,"lon":106.7010}],"targets":[{"lat":10.7626,"lon":106.6822},{"lat":10.7691,"lon":106.7000}],"costing":"motor_scooter","units":"kilometers"}' \
	  | python3 -c "import sys,json; m=json.load(sys.stdin)['sources_to_targets']; print('>> matrix km:', [[c['distance'] for c in r] for r in m])" \
	  || echo ">> Valhalla not ready — check: docker compose logs -f valhalla"
