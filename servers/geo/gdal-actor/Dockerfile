# Phase 0b — GDAL Abaco actor image
#
# Base: official GDAL ubuntu-small image pinned to 3.9.2.
# This image already includes:
#   gdalinfo, gdalwarp, gdal_translate, gdaladdo, python3, python3-pip
#
# The actor is stdlib-only (urllib for the CKAN upload), so NO pip install is
# required — keeps the image minimal and the build offline-safe.

FROM ghcr.io/osgeo/gdal:ubuntu-small-3.9.2

LABEL org.opencontainers.image.title="dso-geo GDAL actor" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.description="Abaco actor that runs validated GDAL operations with /vsicurl/ HTTP reads" \
      gdal_version="3.9.2"

# No third-party Python deps: actor.py + register_to_ckan.py use only the
# standard library, and the GDAL CLIs ship in the base image.

# Create output directory (overridable via OUTPUT_DIR env var at runtime)
RUN mkdir -p /data/out

WORKDIR /app

# Copy Python modules (no __pycache__ or test files)
COPY actor.py          /app/actor.py
COPY validators.py     /app/validators.py
COPY register_to_ckan.py /app/register_to_ckan.py

# Abaco convention: entrypoint reads MSG env var for the operation message.
# Local testing: pass --message '<json>' or pipe JSON to stdin.
ENTRYPOINT ["python3", "/app/actor.py"]
