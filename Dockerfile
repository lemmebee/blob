FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data is the named-volume mountpoint. Creating it here owned by the runtime
# user makes docker copy that ownership onto the empty volume on first mount,
# which is the only way a non-root container can write to it.
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /data \
    && chown app:app /data /app

USER app

# Hashed static filenames, gzip/brotli variants, all baked into the image.
# Importing settings generates a signing key when SECRET_KEY is unset, and a
# build-time write to /data would be stored in the image layer and then copied
# into every fresh volume. A throwaway key and a throwaway DATA_DIR keep the
# real one out of the image.
RUN SECRET_KEY=build-only-not-used-at-runtime \
    DATA_DIR=/tmp/blob-build \
    python manage.py collectstatic --noinput \
    && rm -rf /tmp/blob-build

EXPOSE 8000
CMD ["./entrypoint.sh"]
