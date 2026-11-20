#!/usr/bin/env bash
# Elimina un postulante por RUT, con sus expedientes y todo lo asociado (la auditoría queda
# anonimizada). Pide el RUT, muestra qué se borrará y pide confirmación.
#   ./scripts/eliminar-postulante.sh               pregunta el RUT
#   ./scripts/eliminar-postulante.sh 14319010-2    usa ese RUT
# No necesita reconstruir la imagen: envía scripts/eliminar_postulante.py al contenedor web.
set -euo pipefail
cd "$(dirname "$0")/.."

ejecutar() {
  docker compose -f docker-compose.prod.yml exec -T -w /app -e PYTHONPATH=/app web \
    python - "$@" < scripts/eliminar_postulante.py
}

RUT="${1:-}"
if [ -z "$RUT" ]; then
  read -rp "RUT del postulante a eliminar (ej.: 14319010-2): " RUT
fi
[ -n "$RUT" ] || { echo "No se ingresó un RUT." >&2; exit 2; }

echo
ejecutar "$RUT" || exit $?
echo
read -rp "Escriba ELIMINAR para borrarlo definitivamente: " RESPUESTA
if [ "$RESPUESTA" != "ELIMINAR" ]; then
  echo "Cancelado: no se borró nada."
  exit 1
fi
ejecutar "$RUT" --confirmar
