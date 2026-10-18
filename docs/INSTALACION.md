# Instalación en el servidor

Guía para instalar y mantener el Sistema WorkFlow de matrícula en un servidor Linux con
Docker. La aplicación corre en cuatro contenedores (`docker-compose.prod.yml`):

| Servicio | Qué hace |
|---|---|
| `db` | PostgreSQL 16. No publica puertos: solo la aplicación accede. |
| `redis` | Límites de intentos (login y formulario QR), compartidos por los workers. |
| `web` | La aplicación con Gunicorn (3 workers). |
| `caddy` | Proxy inverso con HTTPS automático (Let's Encrypt). |

## 1. Requisitos

- Servidor Linux con Docker Engine y el plugin `docker compose`.
- Un **dominio público** apuntando a la IP del servidor (registro DNS tipo A), con los puertos
  80 y 443 abiertos. Caddy los necesita para obtener el certificado.
- Acceso a la cuenta SMTP del remitente `matricula-noreply@unach.cl`.

## 2. Primera instalación

```bash
git clone <repositorio> /opt/workflow
cd /opt/workflow
cp .env.example .env
chmod 600 .env          # contiene contraseñas
nano .env
```

Valores que hay que completar en el `.env`:

| Variable | Valor |
|---|---|
| `FLASK_SECRET_KEY` | Una clave larga y aleatoria: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `APP_BASE_URL` | `https://<dominio>` (se usa en el QR y en los enlaces de los correos) |
| `DOMINIO` | `<dominio>`, sin `https://` (lo usa Caddy) |
| `DATABASE_PASSWORD` | Una contraseña aleatoria. `DATABASE_HOST` no importa: el compose lo fija en `db`. |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` | Credenciales del remitente (Gmail: `smtp.gmail.com`, puerto 587, contraseña de aplicación) |
| `CONTACTO_MATRICULA` | Correo o teléfono de la unidad de matrícula que aparece en los correos |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Claves de Cloudflare Turnstile para el dominio (CAPTCHA del QR) |
| `SUPERADMIN_EMAIL`, `SUPERADMIN_NAME` | El primer Administrador |
| `SUPERADMIN_PASSWORD_HASH` | Hash bcrypt de su contraseña (ver abajo). Al primer ingreso deberá cambiarla. |

Para generar el hash de la contraseña del super administrador:

```bash
docker run --rm python:3.12-slim sh -c \
  "pip install -q bcrypt && python -c \"import bcrypt, getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())\""
```

> En el `.env`, escriba el hash entre comillas simples: contiene caracteres `$`.

Luego:

```bash
./deploy.sh
```

El script construye la imagen, levanta la base, aplica las migraciones y deja corriendo la
aplicación y Caddy. Al terminar, `https://<dominio>/salud` debe responder `{"estado": "ok"}`.

### Catálogo de carreras

El catálogo se carga desde la aplicación: **Carreras → Importar catálogo**, con un archivo
`.csv` o `.xlsx` con las columnas `codigo_carrera` y `nombre_carrera`. No use
`seed_carreras.py` en producción: carga un catálogo ficticio de referencia.

## 3. Actualizar a una nueva versión

```bash
cd /opt/workflow
./deploy.sh
```

Aplica las migraciones pendientes antes de reiniciar la aplicación. Para ver el estado de las
migraciones:

```bash
docker compose -f docker-compose.prod.yml run --rm web python migrate.py --estado
```

## 4. Tareas programadas (cron)

Agregue al crontab del usuario que administra Docker (`crontab -e`):

```cron
# Eliminación de datos personales al fin del período lectivo (RF-26), todos los días a las 03:15
15 3 * * *  cd /opt/workflow && docker compose -f docker-compose.prod.yml exec -T web flask --app app:create_app purgar-datos >> /var/log/workflow-purga.log 2>&1

# Respaldo diario de la base (RNF-05), a las 02:30
30 2 * * *  /opt/workflow/scripts/respaldo.sh >> /var/log/workflow-respaldo.log 2>&1
```

**Eliminación de datos (`purgar-datos`):**
- Solo actúa sobre procesos **cerrados** cuya fecha de eliminación ya llegó (por defecto, el
  30 de noviembre). Si un proceso sigue activo con la fecha vencida, no elimina nada y escribe
  una alerta. El Administrador también la ve en su panel.
- Es **irreversible**. Para ver qué eliminaría sin borrar nada:
  ```bash
  docker compose -f docker-compose.prod.yml exec -T web flask --app app:create_app purgar-datos --simular
  ```

**Respaldos:**
- `scripts/respaldo.sh` guarda un `pg_dump` comprimido en `/var/backups/workflow` (variable
  `DESTINO_RESPALDOS`) y borra los de más de 14 días (variable `DIAS_RESPALDO`).
- Los respaldos contienen datos personales: la carpeta queda con permisos solo para su dueño.
  Si se copian a otro lugar, deben rotar en el mismo plazo. Así, los datos eliminados al fin
  del período lectivo desaparecen también de los respaldos (RF-26).
- Para restaurar un respaldo (reemplaza los datos actuales):
  ```bash
  gunzip -c /var/backups/workflow/workflow_AAAAMMDD_HHMM.sql.gz | \
    docker compose -f docker-compose.prod.yml exec -T db psql -U workflow -d workflow
  ```

## 5. Cambiar el dominio

1. Actualice `APP_BASE_URL` y `DOMINIO` en el `.env`.
2. Reinicie: `docker compose -f docker-compose.prod.yml up -d web caddy`.
3. Genere claves de Turnstile para el nuevo dominio.
4. Reimprima el póster del QR (**Procesos → Póster del QR**).

## 6. Revisar el funcionamiento

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f web      # errores de la aplicación
docker compose -f docker-compose.prod.yml logs -f caddy    # certificados y accesos
```

- Los correos con envío fallido aparecen en **Reportes → Correos fallidos**.
- Los correos automáticos (inicio, matrícula, reenvío por QR) se envían en segundo plano: el
  usuario no espera al servidor de correo. Si el contenedor se detiene justo en ese momento,
  un envío en curso puede perderse; el staff puede reenviarlo desde la ficha.
