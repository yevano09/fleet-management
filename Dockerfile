# ── Stage 1: Tailwind CSS build (Node only lives here) ────────────────────────
FROM node:22-slim AS css

WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY assets/ ./assets/
COPY app/templates/ ./app/templates/
RUN npx @tailwindcss/cli -i ./assets/app.css -o /out/app.css --minify

# ── Stage 2: Python runtime (no Node) ─────────────────────────────────────────
FROM python:3.12-slim

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

WORKDIR /app

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc g++ \
    && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Tailwind stylesheet built in the css stage (Phase 0: served alongside the
# legacy inline <style> until the Phase 3 cleanup removes it).
COPY --from=css /out/app.css ./app/static/app.css

RUN mkdir -p firmware data

EXPOSE 8000

ENV PYTHONPATH=/app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
