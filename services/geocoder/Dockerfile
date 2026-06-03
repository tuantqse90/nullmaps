# NullMaps geocoder — builds the index (osmium) and serves it (FastAPI).
# One image for both: `make geo-index` runs importer.py; the service runs uvicorn.
FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
# pyosmium has no aarch64 wheel for the pinned version -> build from source.
# Build deps are installed, used, then purged to keep the image lean.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential cmake libexpat1-dev zlib1g-dev libbz2-dev libboost-dev \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y build-essential cmake libboost-dev \
 && apt-get autoremove -y \
 && apt-get install -y --no-install-recommends libexpat1 zlib1g libbz2-1.0 \
 && rm -rf /var/lib/apt/lists/*
COPY importer.py .
COPY indexops.py .
COPY legacy_districts.json .
COPY app ./app

EXPOSE 2322
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "2322"]
