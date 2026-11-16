# Prompt para instalar el sistema en el servidor

Para instalar el sistema en el servidor de producción:

1. Abra Claude Code en el servidor.
2. Pegue el texto del bloque de abajo.

Claude seguirá `docs/INSTALACION.md` y le pedirá los datos que solo usted tiene: dominio, credenciales SMTP, contacto, claves de Turnstile y Administrador inicial.

```text
Vas a instalar en producción, en este servidor Linux, el Sistema WorkFlow de matrícula de UNACH.
- Repositorio público: https://github.com/eduardosanmartin/workflow
- Guía oficial: docs/INSTALACION.md. Léela completa antes de empezar y sigue su orden.
- Arquitectura (docker-compose.prod.yml), con 4 contenedores:
  - db: postgres:16, sin puertos publicados
  - redis: redis:7-alpine
  - web: Flask + Gunicorn, imagen construida desde el Dockerfile con python:3.12-slim
  - caddy: caddy:2, con HTTPS automático de Let's Encrypt en los puertos 80 y 443
- Python, PostgreSQL y Redis NO se instalan en el servidor: todo corre en contenedores. Las dependencias de Python (requirements.txt) se instalan solas al construir la imagen.

Reglas generales:
- Antes de cada paso que instale paquetes, abra puertos o modifique el sistema, dime qué vas a hacer y espera mi visto bueno.
- No borres ni sobrescribas nada que ya exista (carpetas, contenedores, volúmenes, crontab): avísame primero.
- No muestres en el chat ni en logs ninguna contraseña, clave ni hash.

## 1. Diagnóstico (solo lectura)
Reporta:
- Distribución y versión, arquitectura de la CPU, RAM y disco libre. Mínimo recomendado: 2 GB de RAM y 10 GB libres.
- Si están instalados git, curl, Docker Engine y el plugin `docker compose` v2, con sus versiones.
- Usuario actual, si tiene sudo y si pertenece al grupo docker.
- Qué ocupa los puertos 80 y 443 (`ss -tlnp`), por ejemplo Apache o nginx, y el estado del firewall (ufw, firewalld o iptables).
- IP pública (`curl -s ifconfig.me`) y zona horaria del sistema.

## 2. Instalar los requisitos del sistema (con mi visto bueno)
- Paquetes base: git, curl, ca-certificates, dnsutils (para dig) y cron, si faltan.
- Docker Engine y el plugin compose desde el repositorio oficial de Docker, siguiendo docs.docker.com/engine/install para la distribución detectada. No uses el paquete docker.io de la distribución.
- Habilita Docker al arranque: `systemctl enable --now docker`.
- Agrega mi usuario al grupo docker y explícame que debo volver a iniciar sesión.
- Firewall:
  - Permite 22/tcp (no me dejes fuera), 80/tcp, 443/tcp y 443/udp.
  - Si hay otro servidor web usando el 80 o el 443, no lo detengas: avísame.
- Zona horaria: si no es America/Santiago, propónme cambiarla. El cron usa la hora del sistema.
- Verifica la instalación con `docker run --rm hello-world` y `docker compose version`.

## 3. Clonar
sudo mkdir -p /opt/workflow && sudo chown $USER:$USER /opt/workflow
git clone https://github.com/eduardosanmartin/workflow.git /opt/workflow
(Si /opt/workflow ya tiene contenido, detente y avísame.)

## 4. Configurar el .env
cd /opt/workflow && cp .env.example .env && chmod 600 .env
Completa el .env siguiendo las indicaciones del propio archivo.

Genera tú, aleatorios, y no los muestres:
- FLASK_SECRET_KEY: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Si no hay python3, usa `openssl rand -base64 48`.
- DATABASE_PASSWORD: solo letras y números.

Fija:
- PROXIES_CONFIABLES=1
- REDIS_URL vacío (el compose lo asigna)
- CORREO_EN_SEGUNDO_PLANO=true
- SMTP_FROM_EMAIL=matricula-noreply@unach.cl

Pídeme estos valores (no inventes ninguno):
- DOMINIO (sin https://). APP_BASE_URL será https://<DOMINIO>.
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD y SMTP_USE_TLS.
- CONTACTO_MATRICULA (correo o teléfono de la unidad de matrícula).
- TURNSTILE_SITE_KEY y TURNSTILE_SECRET_KEY. Si aún no las tengo, quedan vacías y el CAPTCHA queda desactivado. Anótalo como pendiente.
- SUPERADMIN_EMAIL y SUPERADMIN_NAME.
- Hash bcrypt de la contraseña del primer Administrador. No me pidas la contraseña. Dame este comando para que lo ejecute yo y te pase el hash:
  docker run --rm -it python:3.12-slim sh -c "pip install -q bcrypt && python -c \"import bcrypt, getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())\""

Antes de seguir, comprueba con `dig +short <DOMINIO>` que el dominio apunte a la IP pública del servidor. Si no apunta, detente: Caddy no obtendrá el certificado.

## 5. Desplegar
./deploy.sh
El script construye la imagen, levanta db y redis, aplica las migraciones (migrate.py) y levanta web y caddy.

Después verifica:
- `docker compose -f docker-compose.prod.yml ps`: los 4 servicios están "running"/"healthy".
- `docker compose -f docker-compose.prod.yml run --rm web python migrate.py --estado`: no quedan migraciones pendientes.
- `curl -s https://<DOMINIO>/salud` responde {"estado": "ok"} con un certificado válido.
- Revisa los logs de web y de caddy (`logs --tail 100`) y explícame cualquier error.

## 6. Tareas programadas
Antes de modificar el crontab, muéstrame el actual. Luego agrega:
15 3 * * *  cd /opt/workflow && docker compose -f docker-compose.prod.yml exec -T web flask --app app:create_app purgar-datos >> /var/log/workflow-purga.log 2>&1
30 2 * * *  /opt/workflow/scripts/respaldo.sh >> /var/log/workflow-respaldo.log 2>&1

Prepara el entorno:
- Crea los archivos de log y /var/backups/workflow, con dueño mi usuario y permisos 700 para la carpeta.
- Ejecuta una vez `scripts/respaldo.sh` y confirma que generó un .sql.gz.
- Ejecuta `purgar-datos --simular` y confirma que no da errores. NO lo ejecutes sin --simular: es irreversible.

## 7. Prueba del correo
Pídeme un correo de destino y envía un mensaje de prueba con las credenciales SMTP del .env desde el contenedor web (por ejemplo, un script corto con smtplib). Confirma que llegó o muéstrame el error.

## 8. Resumen final
Entrégame:
- Lo que se instaló y sus versiones.
- La URL del sistema.
- El estado de los contenedores.
- Las tareas programadas.
- Lo que quedó pendiente. Normalmente:
  - Claves de Turnstile, si faltaron.
  - Primer ingreso del Administrador con cambio de contraseña.
  - Importar el catálogo real de carreras (Carreras → Importar catálogo; NO usar seed_carreras.py).
  - Imprimir el póster del QR (Procesos → Póster del QR).
```
