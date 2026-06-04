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
	  --maxzoom=16 \
	  --output=/data/$(PMTILES_FILE) \
	  --force
	@echo ">> Done. Tiles at $(DATA_DIR)/$(PMTILES_FILE)"

FONT_BASE := https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans

.PHONY: fonts
fonts: ## Fetch self-hosted glyph fonts (Noto Sans) for Martin to serve
	@mkdir -p services/tiles/fonts
	@test -s "services/tiles/fonts/NotoSans-Regular.ttf" || \
	  curl -fSL "$(FONT_BASE)/NotoSans-Regular.ttf" -o services/tiles/fonts/NotoSans-Regular.ttf
	@test -s "services/tiles/fonts/NotoSans-Bold.ttf" || \
	  curl -fSL "$(FONT_BASE)/NotoSans-Bold.ttf" -o services/tiles/fonts/NotoSans-Bold.ttf
	@echo ">> fonts ready in services/tiles/fonts/"

.PHONY: up
up: fonts ## Start Phase 1 services (martin + demo) in the background
	docker compose up -d martin demo

.PHONY: demo
demo: fonts ## Build tiles if missing, start services, print the demo URL
	@test -f "$(DATA_DIR)/$(PMTILES_FILE)" || $(MAKE) tiles
	docker compose up -d martin demo
	@echo ">> Demo:   http://localhost:$(DEMO_PORT)   (Ho Chi Minh City basemap)"
	@echo ">> Martin: http://localhost:$(MARTIN_PORT)/catalog"

ADAPTER_PORT  ?= 8010
GEOCODER_PORT ?= 2322

.PHONY: geo-index
geo-index: $(PBF) ## (Phase 3) Build the VN geocoder SQLite index from the OSM extract
	docker compose build geocoder
	@mkdir -p services/geocoder/data
	@echo ">> indexing (pyosmium, a few minutes)..."
	@# Build the DB on the container fs (SQLite + virtiofs bind-mount = I/O errors),
	@# then copy the finished file to the mounted volume.
	docker run --rm \
	  -v "$(abspath $(RAW_DIR)):/raw:ro" \
	  -v "$(abspath services/geocoder/data):/data" \
	  nullmap-geocoder sh -c \
	  "mkdir -p /build && python importer.py /raw/vietnam-latest.osm.pbf /build/geocoder.db && \
	   rm -f /data/geocoder.db && cp /build/geocoder.db /data/geocoder.db"
	@# dev-box only: virtiofs sometimes writes 'geocoder N.db' (space) — normalize it
	@if [ ! -s services/geocoder/data/geocoder.db ]; then \
	  f=$$(ls -t services/geocoder/data/geocoder*.db 2>/dev/null | head -1); \
	  [ -n "$$f" ] && mv "$$f" services/geocoder/data/geocoder.db; fi
	@rm -f services/geocoder/data/geocoder\ *.db
	docker compose up -d geocoder

.PHONY: geo-test
geo-test: ## (Phase 3) Smoke-test geocode / autocomplete / reverse
	@P=$(GEOCODER_PORT); \
	echo ">> autocomplete 'ben thanh':"; \
	curl -fsS "http://localhost:$$P/autocomplete?q=ben+thanh&limit=3" \
	  | python3 -c "import sys,json;[print('   ',r['name'],'('+r['kind']+')',round(r['lat'],4),round(r['lon'],4)) for r in json.load(sys.stdin)['results']]"; \
	echo ">> geocode 'nguyen hue' (diacritic-folded):"; \
	curl -fsS "http://localhost:$$P/geocode?q=nguyen+hue&limit=2" \
	  | python3 -c "import sys,json;[print('   ',r['name']) for r in json.load(sys.stdin)['results']]"; \
	echo ">> reverse 10.7725,106.6980:"; \
	curl -fsS "http://localhost:$$P/reverse?lat=10.7725&lon=106.6980" \
	  | python3 -c "import sys,json;r=json.load(sys.stdin)['result'];print('   ',r['name'] if r else None, (str(r['distance_m'])+'m') if r else '')"

.PHONY: adapter-test
adapter-test: ## (Phase 4) Smoke-test the Google-compat adapter (Directions+Matrix live, geocode pending)
	@K=$$(grep -E '^API_KEY=' .env | cut -d= -f2); P=$(ADAPTER_PORT); \
	echo ">> directions (motorbike):"; \
	curl -fsS "http://localhost:$$P/maps/api/directions/json?origin=10.7725,106.6980&destination=10.7951,106.7218&key=$$K" \
	  | python3 -c "import sys,json;l=json.load(sys.stdin)['routes'][0]['legs'][0];print('   ',l['distance']['text'],l['duration']['text'])"; \
	echo ">> distancematrix (2x2):"; \
	curl -fsS "http://localhost:$$P/maps/api/distancematrix/json?origins=10.7725,106.6980|10.7800,106.7010&destinations=10.7626,106.6822|10.7691,106.7000&key=$$K" \
	  | python3 -c "import sys,json;b=json.load(sys.stdin);print('   ',[[e.get('distance',{}).get('text','-') for e in r['elements']] for r in b['rows']])"; \
	echo ">> geocode (address=nguyen hue):"; \
	curl -fsS "http://localhost:$$P/maps/api/geocode/json?address=nguyen+hue&key=$$K" \
	  | python3 -c "import sys,json;r=json.load(sys.stdin)['results'][0];print('   ',r['formatted_address'],r['geometry']['location'])"; \
	echo ">> autocomplete (input=ben thanh):"; \
	curl -fsS "http://localhost:$$P/maps/api/place/autocomplete/json?input=ben+thanh&key=$$K" \
	  | python3 -c "import sys,json;[print('   ',p['description']) for p in json.load(sys.stdin)['predictions'][:2]]"

NORMALIZER_PORT ?= 8100

.PHONY: norm-test
norm-test: ## (Phase 5) Check the AI normalizer (no-op until an LLM is configured)
	@curl -fsS "http://localhost:$(NORMALIZER_PORT)/healthz" \
	  | python3 -c "import sys,json;d=json.load(sys.stdin);print('   enabled:',d['enabled'],'model:',d['model'])"; \
	curl -fsS "http://localhost:$(NORMALIZER_PORT)/normalize?q=Q1+P.Ben+Nghe" \
	  | python3 -c "import sys,json;d=json.load(sys.stdin);print('   ',repr(d['original']),'->',repr(d['normalized']),'('+d['engine']+')')"

.PHONY: fleet-test
fleet-test: ## (fleet) Optimized multi-stop route, isochrone, snap-to-roads
	@K=$$(grep -E '^API_KEY=' .env | cut -d= -f2); P=$(ADAPTER_PORT); \
	echo ">> optimized 4-stop (waypoints=optimize:true):"; \
	curl -fsS "http://localhost:$$P/maps/api/directions/json?origin=10.7725,106.6980&destination=10.7951,106.7218&waypoints=optimize:true%7C10.8231,106.6297%7C10.7546,106.6655&key=$$K" \
	  | python3 -c "import sys,json;r=json.load(sys.stdin)['routes'][0];print('   legs',len(r['legs']),'order',r.get('waypoint_order'))"; \
	echo ">> isochrone (10,20 min):"; \
	curl -fsS "http://localhost:$$P/v1/isochrone?location=10.7725,106.6980&contours=10,20&key=$$K" \
	  | python3 -c "import sys,json;g=json.load(sys.stdin);print('   contour polygons:',len(g['features']))"; \
	echo ">> snap-to-roads (3 pts):"; \
	curl -fsS "http://localhost:$$P/v1/snap?path=10.7725,106.6980%7C10.7760,106.7000%7C10.7800,106.7010&key=$$K" \
	  | python3 -c "import sys,json;d=json.load(sys.stdin);print('   matched',d['distance']['text'],d['duration']['text'])"

.PHONY: smoke
smoke: ## Verify the whole live stack (tiles + routing + adapter) end-to-end
	@echo "== tiles ==" ; \
	curl -fsS -o /dev/null -w "  TileJSON HTTP %{http_code}\n" http://localhost:$(DEMO_PORT)/tiles/vietnam ; \
	curl -fsS -o /dev/null -w "  demo page HTTP %{http_code}\n" http://localhost:$(DEMO_PORT)/ ; \
	echo "== routing ==" ; $(MAKE) -s route-test ; $(MAKE) -s matrix-test ; \
	echo "== geocoder ==" ; $(MAKE) -s geo-test ; \
	echo "== normalizer ==" ; $(MAKE) -s norm-test ; \
	echo "== adapter ==" ; $(MAKE) -s adapter-test

DEMO_PORT ?= 8080

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

.PHONY: backup-test
backup-test: ## (ops) Verify the latest R2 backup is restorable (sqlite + pmtiles checks)
	bash infra/restore-test.sh

.PHONY: style-lint
style-lint: ## (tiles) Validate styles vs the MapLibre spec + check icon/sprite coverage
	npx -y -p @maplibre/maplibre-gl-style-spec gl-style-validate services/tiles/style/style.json
	npx -y -p @maplibre/maplibre-gl-style-spec gl-style-validate services/tiles/style/style-dark.json
	npx -y -p @maplibre/maplibre-gl-style-spec gl-style-validate services/tiles/style/style-terrain.json
	node services/tiles/check-icons.mjs

.PHONY: bug-hunt bug-hunt-be bug-hunt-fe bug-hunt-theme
bug-hunt: ## (harness) Run the BE + FE bug harness against NM_BASE (reads .env for API_KEY)
	bash harness/run.sh all
bug-hunt-be: ## (harness) Backend only: fuzz (robustness) + probe (correctness)
	bash harness/run.sh be
bug-hunt-fe: ## (harness) Frontend only: headless feature smoke + theme visual regression
	bash harness/run.sh fe
bug-hunt-theme: ## (harness) Just the Playwright theme-switcher visual regression
	@cd harness && { node -e "require.resolve('playwright-core')" 2>/dev/null || \
	  { [ -f package.json ] || echo '{"name":"nm-harness","private":true}' > package.json; \
	    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i --silent playwright-core@1.48; }; } && node fe_theme.mjs
