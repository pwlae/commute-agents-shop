# Build from the repo root: docker build -f payments/Dockerfile .
FROM python:3.13.18-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY libs/http-py /tmp/http-py
COPY payments/requirements.txt .
RUN pip install -r requirements.txt /tmp/http-py && rm -rf /tmp/http-py

COPY payments/app app
COPY payments/migrations migrations

RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
CMD ["python", "-m", "app"]
