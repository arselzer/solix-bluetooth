"""Exercise ASGI routes with the same monitoring lifespan as Uvicorn."""
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")


@asynccontextmanager
async def api_client(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client
