# Multi-arch (works on Oracle Ampere arm64 and x86). One image, three commands.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY botspotter ./botspotter
COPY web ./web
RUN pip install --no-cache-dir . granian
RUN useradd -r -u 10001 bs && chown -R bs /app
USER bs
# Overridden per service in docker-compose.yml
CMD ["botspotter-api"]
