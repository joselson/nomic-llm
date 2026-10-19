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
# Claude Code CLI for the default `claude_code` agent provider (needs Node from the web stage)
ARG INSTALL_CLAUDE_CODE=1
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN if [ "$INSTALL_CLAUDE_CODE" = "1" ]; then \
      ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
      npm install -g --no-fund --no-audit @anthropic-ai/claude-code && claude --version; \
    fi
COPY --from=web /web/dist /app/frontend/dist
ENV PN_FRONTEND_DIST=/app/frontend/dist \
    PN_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
