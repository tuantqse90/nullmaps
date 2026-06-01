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

.PHONY: down
down: ## Stop all services
	docker compose down

.PHONY: logs
logs: ## Tail service logs
	docker compose logs -f

.PHONY: route-test
route-test: ## (Phase 2) Smoke-test a motorbike route via Valhalla — not yet wired
	@echo "route-test arrives in Phase 2 (Valhalla motor_scooter costing)."
