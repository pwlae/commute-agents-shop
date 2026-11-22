# Build from the repo root: docker build -f card-provider/Dockerfile .
FROM python:3.13.18-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY card-provider/requirements.txt .
RUN pip install -r requirements.txt
COPY card-provider/app.py .

RUN useradd --system --uid 10001 app
USER app
EXPOSE 8100
CMD ["python", "app.py"]
