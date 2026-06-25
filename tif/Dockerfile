FROM python:3.12-slim

# Dependencias de sistema para rasterio/shapely (GDAL/GEOS).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 5300

# Producción: sin --reload.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5300", "--reload"]
