from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app_settings import get_settings, has_placeholder_ingest_token, has_placeholder_read_token
from db.database import init_db
from routers.health import router as health_router
from routers.reports import router as reports_router
from routers.read_api import router as read_router
from routers.webhook import router as webhook_router
from utils.logger import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting HealthQuery backend")
    settings = get_settings()
    warn_on_placeholder_tokens(settings)
    await init_db()
    yield
    logger.info("Stopping HealthQuery backend")


app = FastAPI(title="HealthQuery API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(read_router)
app.include_router(reports_router)
app.include_router(webhook_router)


def warn_on_placeholder_tokens(settings=None) -> None:
    settings = settings or get_settings()
    if has_placeholder_ingest_token(settings):
        logger.warning("HEALTHQUERY_INGEST_TOKEN is still set to a placeholder value")
    if has_placeholder_read_token(settings):
        logger.warning("HEALTHQUERY_READ_TOKEN is still set to a placeholder value")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=3136, reload=False)
