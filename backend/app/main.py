"""FastAPI application factory.

Route handlers here are synchronous ``def``, not ``async def``. FastAPI runs
sync handlers on a worker thread pool, which is exactly right for this
workload: rendering DOCX/PPTX and computing embeddings are CPU-bound, and the
LLM/search SDKs are used synchronously. Going async would add plumbing without
adding concurrency, and would risk blocking the event loop on CPU work.
See docs/DESIGN_DECISIONS.md.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.deps import guard
from backend.app.api.routes import artifacts, chat, health, rag, uploads
from backend.app.config import get_settings
from backend.app.core.errors import AtelierError
from backend.app.core.logging import configure_logging, get_logger, secrets_from_settings
from backend.app.db.session import init_db

logger = get_logger(__name__)


def _error_body(code: str, message: str, fields: object | None = None) -> dict[str, object]:
    """The single error envelope every failure path returns."""
    error: dict[str, object] = {"code": code, "message": message}
    if fields is not None:
        error["fields"] = fields
    return {
        "success": False,
        "error": error,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, secrets_from_settings(settings))
    init_db(settings)
    logger.info("Atelier ready | llm=%s vector=%s", settings.llm_provider, settings.vector_backend)
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Atelier",
        description=(
            "Multi-agent document and presentation studio. Analyses uploaded "
            "templates, researches, and generates editable DOCX and PPTX in the "
            "template's own style."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    _register_exception_handlers(app)

    app.include_router(health.router)
    for module in (uploads, chat, artifacts, rag):
        app.include_router(module.router, prefix="/api", dependencies=[Depends(guard)])

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AtelierError)
    async def _handle_atelier_error(_request: Request, exc: AtelierError) -> JSONResponse:
        # detail goes to the log, public_message goes to the caller.
        log = logger.warning if exc.status_code < 500 else logger.error
        log("%s (%s): %s", exc.code, exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.public_message),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {"field": ".".join(str(part) for part in err["loc"][1:]), "message": err["msg"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_body("VALIDATION_ERROR", "The request was invalid.", fields),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("INTERNAL_ERROR", "An internal error occurred."),
        )


app = create_app()
