# Backend API image. Build:  docker build -t googlehora-api .
# Run:    docker run --env-file .env -p 8000:8000 googlehora-api
# Configuration comes from environment variables at run time (see
# .env.example); nothing secret is baked into the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# pyswisseph compiles a C extension when no prebuilt wheel matches.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py astro_rules.json ./
COPY app ./app

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
