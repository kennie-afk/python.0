FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip && pip install ".[api]"

RUN useradd --create-home --uid 10001 sifa
USER sifa

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "sifa.serving.api:app", "--host", "0.0.0.0", "--port", "8000"]
