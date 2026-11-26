FROM python:3.12-slim

# opencv-python-headless needs libglib at runtime on slim images
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Everything the app writes (database, photos, 3D models, the session key)
# lives under /data, so one mounted volume is the whole backup surface.
ENV APP_DATA_DIR=/data
# writable by any user id, so hosts that run containers as a non-root user
# (for example Hugging Face Spaces) can still write the database and files
RUN mkdir -p /data && chmod 777 /data
VOLUME /data
EXPOSE 8000

# One process with several threads: the in-memory login rate limiter is
# per-process, so extra *workers* would each keep their own counters.
# PORT lets a host choose the port (Hugging Face Spaces uses 7860).
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT:-8000} wsgi:app"]
