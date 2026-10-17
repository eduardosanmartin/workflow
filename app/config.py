"""Configuración desde variables de entorno (.env), según la §14.1 de la SPEC."""

import os

from dotenv import load_dotenv

load_dotenv()


def _bool(nombre, defecto=False):
    valor = os.getenv(nombre)
    if valor is None or valor.strip() == "":
        return defecto
    return valor.strip().lower() in ("1", "true", "yes", "si", "sí")


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me")
    APP_BASE_URL = (os.getenv("APP_BASE_URL") or "http://localhost:5001").rstrip("/")

    DATABASE_NAME = os.getenv("DATABASE_NAME", "workflow")
    DATABASE_USER = os.getenv("DATABASE_USER", "workflow")
    DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", "")
    DATABASE_HOST = os.getenv("DATABASE_HOST", "localhost")
    DATABASE_PORT = int(os.getenv("DATABASE_PORT") or 5432)

    # Flask-Limiter: Redis en producción (compartido por los workers); en memoria si no hay REDIS_URL (desarrollo).
    RATELIMIT_STORAGE_URI = os.getenv("REDIS_URL") or "memory://"

    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT") or 587)
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "matricula-noreply@unach.cl")
    SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Matrícula UNACH")
    SMTP_USE_TLS = _bool("SMTP_USE_TLS", True)
    CONTACTO_MATRICULA = os.getenv("CONTACTO_MATRICULA", "")

    # RF-10: CAPTCHA del formulario QR (Cloudflare Turnstile). Vacíos: desactivado.
    TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")
    TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")

    SUPERADMIN_EMAIL = os.getenv("SUPERADMIN_EMAIL", "")
    SUPERADMIN_NAME = os.getenv("SUPERADMIN_NAME", "Administrador")
    SUPERADMIN_PASSWORD_HASH = os.getenv("SUPERADMIN_PASSWORD_HASH", "")
    SUPERADMIN_PASSWORD = os.getenv("SUPERADMIN_PASSWORD", "")

    # RF-22: la sesión expira tras 8 horas; la inactividad (30 min) se controla por request.
    PERMANENT_SESSION_LIFETIME = 8 * 60 * 60
    SESION_INACTIVIDAD_SEGUNDOS = 30 * 60
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_BASE_URL.startswith("https://")

    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # RF-08: archivos de hasta 10 MB
    BCRYPT_ROUNDS = 12


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    BCRYPT_ROUNDS = 4  # pruebas rápidas; producción usa 12
    DATABASE_NAME = os.getenv("TEST_DATABASE_NAME", "workflow_test")
    SMTP_HOST = ""
    TURNSTILE_SITE_KEY = ""
    TURNSTILE_SECRET_KEY = ""
    SUPERADMIN_EMAIL = ""
    SUPERADMIN_PASSWORD_HASH = ""
    SUPERADMIN_PASSWORD = ""
