# --- СТАДИЯ 1: Базовый образ с Python и FFmpeg ---
FROM python:3.11-slim AS base-python
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- СТАДИЯ 2: Сборка фронтенда (только для продакшена) ---
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# --- СТАДИЯ 3: DEV-STAGE (используется для разработки) ---
FROM base-python AS dev-stage
ENV MD_DEBUG=true
# В dev-stage мы не копируем код, а будем пробрасывать его через volumes
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- СТАДИЯ 4: PRODUCTION (финальный образ) ---
FROM base-python AS production
RUN useradd -m -u 1000 appuser
# Копируем код бэкенда
COPY backend/app ./app
# Копируем билд фронтенда из второй стадии
COPY --from=frontend-builder /app/frontend/dist ./static
# Настройка прав
RUN mkdir -p /app/downloads /app/cookies && \
    chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]