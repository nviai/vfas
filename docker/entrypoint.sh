#!/bin/sh
# Migrate the schema, then start the web server. Admin commands can be run with:
#   docker compose exec vfas python -m app.cli create-admin --email admin@congty.vn
set -e
# Data files (DB, attachments, backups, keys) are readable only by the app user.
umask 077

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

python -m app.cli migrate

# SQLite: 1 worker (database write lock). PostgreSQL: WEB_CONCURRENCY workers
# (postings are serialized per company with row locks).
WORKERS=1
case "${DATABASE_URL:-}" in
  postgresql*) WORKERS="${WEB_CONCURRENCY:-2}" ;;
esac

# Trust X-Forwarded-For/-Proto only from the reverse proxy in front (Traefik). Default: localhost only;
# behind Traefik in Docker set FORWARDED_ALLOW_IPS to the Docker network range (e.g. 172.16.0.0/12).
# Never use "*" when the port is exposed directly.
exec uvicorn app.main:create_app --factory \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers "$WORKERS" \
  --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --no-server-header
