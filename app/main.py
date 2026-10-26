from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import notifications
from app.api.errors import install_error_handlers
from app.db import make_engine
from app.settings import Settings, get_settings
from app.templates.registry import TemplateRegistry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Both loads raise on bad configuration, so the API refuses to start with
    # an invalid template set or lease settings.
    settings = app.state.settings or get_settings()
    app.state.settings = settings
    app.state.registry = TemplateRegistry.load(settings.template_dir)
    app.state.engine = make_engine(settings.database_url)
    try:
        yield
    finally:
        app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Notification service", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(notifications.router)
    return app


app = create_app()
