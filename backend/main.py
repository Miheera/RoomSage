from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from config import settings
from database import engine
from models import Base
from routers import auth, catalog, designs, jobs, projects
from services.image_generation_service import log_image_provider


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log_image_provider("API", log=logging.getLogger("uvicorn.error"))
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(projects.router, prefix="/api/v1")
app.include_router(projects.room_router, prefix="/api/v1")
app.include_router(designs.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema is not None:
        return app.openapi_schema

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    photo_upload = schema["paths"]["/api/v1/projects/{project_id}/photos"]["post"]
    body_schema = photo_upload["requestBody"]["content"]["multipart/form-data"]["schema"]
    body_reference = body_schema["$ref"].rsplit("/", 1)[-1]
    file_schema = schema["components"]["schemas"][body_reference]["properties"]["files"]
    file_schema["items"] = {"type": "string", "format": "binary"}

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
