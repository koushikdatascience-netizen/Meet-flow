#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py bootstrap_admin
python manage.py recover_admin
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers "${WEB_CONCURRENCY:-2}" --access-logfile - --error-logfile -
