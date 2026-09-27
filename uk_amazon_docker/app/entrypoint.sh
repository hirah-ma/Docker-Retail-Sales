#!/bin/sh
set -e

echo "[entrypoint] Running one-time data load into Postgres..."
python load_data.py

echo "[entrypoint] Starting Flask app via gunicorn..."
exec gunicorn --bind 0.0.0.0:5000 --workers 2 --access-logfile - app:app
