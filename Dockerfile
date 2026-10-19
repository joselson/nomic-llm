# --- frontend build ---------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- server -----------------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/game ./game
RUN pip install --no-cache-dir .
COPY --from=web /web/dist /app/frontend/dist
ENV PN_FRONTEND_DIST=/app/frontend/dist \
    PN_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
