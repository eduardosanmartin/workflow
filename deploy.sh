#!/usr/bin/env bash
# Despliegue o actualización en el servidor: código, imagen, migraciones y servicios.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.prod.yml"

if [ ! -f .env ]; then
  echo "Falta el archivo .env (copie .env.example y complételo)." >&2
  exit 1
fi

git pull --ff-only
$COMPOSE build web
$COMPOSE up -d db redis
$COMPOSE run --rm web python migrate.py
$COMPOSE up -d web caddy
$COMPOSE ps
echo "Listo. Revise https://$(grep -E '^DOMINIO=' .env | cut -d= -f2 | tr -d ' ')/salud"
