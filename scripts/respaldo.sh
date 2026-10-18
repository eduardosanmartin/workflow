#!/usr/bin/env bash
# Respaldo diario de la base (RNF-05). Conserva DIAS_RESPALDO días: los datos eliminados al fin
# del período lectivo (RF-26) desaparecen también de los respaldos dentro de ese plazo.
set -euo pipefail
cd "$(dirname "$0")/.."

DESTINO="${DESTINO_RESPALDOS:-/var/backups/workflow}"
DIAS="${DIAS_RESPALDO:-14}"
mkdir -p "$DESTINO"
chmod 700 "$DESTINO"   # contienen datos personales

set -a; . ./.env; set +a
ARCHIVO="$DESTINO/workflow_$(date +%Y%m%d_%H%M).sql.gz"
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U "$DATABASE_USER" -d "$DATABASE_NAME" --no-owner | gzip > "$ARCHIVO"
chmod 600 "$ARCHIVO"
find "$DESTINO" -name 'workflow_*.sql.gz' -mtime +"$DIAS" -delete
echo "Respaldo: $ARCHIVO"
