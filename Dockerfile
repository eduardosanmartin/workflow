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

EXPOSE 5001
CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:5001", "app:create_app()"]
