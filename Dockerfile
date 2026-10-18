FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
ARG INSTALAR_DEV=false
RUN if [ "$INSTALAR_DEV" = "true" ]; then pip install -r requirements-dev.txt; \
    else pip install -r requirements.txt; fi

COPY . .

# Sin privilegios de root; instance/ guarda las cargas masivas pendientes (temporales)
RUN useradd --create-home --uid 1000 app && mkdir -p /app/instance && chown -R app:app /app/instance
USER app

EXPOSE 5001
# 3 workers (RNF-04). Timeout amplio para cargas masivas y exportaciones grandes.
CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:5001", "--timeout", "120",      "--access-logfile", "-", "app:create_app()"]
