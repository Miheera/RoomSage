from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import build_container
from app.api.routes import catalog, designs, uploads
from app.api.schemas.design import ErrorResponse
from app.config import Settings, get_settings
from app.core.errors import RoomSageError

API_PREFIX = "/api/v1/design-engine"
ERROR_RESPONSES = {code: {"model": ErrorResponse} for code in (404, 409, 422, 502, 503)}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.container = build_container(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RoomSageError)
    def handle_domain_error(_: Request, exc: RoomSageError) -> JSONResponse:
        body = ErrorResponse(error=exc.code, message=exc.message, details=exc.details)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [{"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in exc.errors()]
        body = ErrorResponse(error="invalid_request", message="Request validation failed.",
                             details={"problems": problems})
        return JSONResponse(status_code=422, content=body.model_dump())

    for module in (catalog, designs, uploads):
        app.include_router(module.router, prefix=API_PREFIX, responses=ERROR_RESPONSES)

    @app.get("/health", tags=["health"])
    def health_check() -> dict[str, str]:
        return {
            "status": "ok",
            "llm_provider": settings.llm_provider,
            "redesign_mode": settings.redesign_mode,
            "person_a_mode": settings.person_a_mode,
        }

    return app
