# --- STAGE 1: Base image with Python and FFmpeg ---
FROM python:3.11-slim AS base-python
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- STAGE 2: Frontend build (production only) ---
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# --- STAGE 3: DEV-STAGE (used for development) ---
FROM base-python AS dev-stage
ENV MD_DEBUG=true
# In dev-stage we don't copy code — it's mounted via volumes
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- STAGE 4: PRODUCTION (final image) ---
FROM base-python AS production
RUN useradd -m -u 1000 appuser
# Copy backend code
COPY backend/app ./app
# Copy frontend build from stage 2
COPY --from=frontend-builder /app/frontend/dist ./static
# Set up permissions
RUN mkdir -p /app/downloads /app/cookies && \
    chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
