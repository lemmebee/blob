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
RUN python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["./entrypoint.sh"]
