# Build from the repo root: docker build -f traffic/Dockerfile .
FROM python:3.13.18-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY traffic/traffic.py .

RUN useradd --system --uid 10001 app
USER app
CMD ["python", "traffic.py"]
