FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src ./src

RUN uv pip install --system --no-cache ".[api,ml,db]"

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 aegis
WORKDIR /app

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --chown=aegis:aegis src ./src
COPY --chown=aegis:aegis pyproject.toml README.md alembic.ini ./
COPY --chown=aegis:aegis migrations ./migrations
COPY --chown=aegis:aegis --chmod=755 scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh

# A lone container applies migrations before serving. Compose and Kubernetes turn this off and run
# `alembic upgrade head` once as a one-shot service / Job (see docker-compose.yml, k8s/21-migrate-job.yaml).
ENV AEGIS_RUN_MIGRATIONS=true

USER 10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "aegis.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
