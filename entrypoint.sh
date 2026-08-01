#!/bin/sh
set -e

python manage.py migrate --noinput

# Few workers on purpose: SQLite serialises writers, and more of them just
# means more contention on the same file.
exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-3}" \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -
