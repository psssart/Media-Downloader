from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging

from .config import settings
from .routes import downloads, settings as settings_router
from .services.file_manager import file_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Scheduler for cleanup tasks
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    # Startup
    logger.info(f"Starting {settings.app_name}")

    # Schedule cleanup job
    scheduler.add_job(
        file_manager.cleanup_old_files,
        "interval",
        minutes=settings.cleanup_interval_minutes,
        id="cleanup_old_files",
    )
    scheduler.start()
    logger.info("Cleanup scheduler started")

    yield

    # Shutdown
    scheduler.shutdown()
    logger.info("Application shutdown complete")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="Self-hosted media downloader using yt-dlp",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(downloads.router)
app.include_router(settings_router.router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": "1.0.0",
    }


@app.get("/api")
async def root():
    """API root."""
    return {
        "message": f"Welcome to {settings.app_name} API",
        "docs": "/docs",
        "health": "/api/health",
    }

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

static_path = Path("static")

# Проверяем, существует ли папка со сборкой фронтенда (создается в Docker)
if static_path.exists():
    # Раздаем папку assets (картинки, стили, скрипты)
    app.mount("/assets", StaticFiles(directory=str(static_path / "assets")), name="assets")


    # Catch-all route для SPA (React/Vue)
    # Если пользователь введет любой путь, не относящийся к API,
    # мы отдаем ему index.html, а дальше разберется React Router.
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Если это запрос файла (напр. favicon.ico), и он есть — отдаем его
        file_path = static_path / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)

        # В остальных случаях отдаем главную страницу фронтенда
        return FileResponse(static_path / "index.html")
