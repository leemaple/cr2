"""Local HTTP API and browser application."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import logging
import sqlite3

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import NAME, VERSION
from .config import Settings
from .database import Database, canonical
from .engines import engine_status
from .reporting import (
    result_csv, result_json, report_html, dataset_csv, audit_csv, compare_runs,
)
from .schemas import (
    Login, PasswordChange, ProjectCreate, ProjectUpdate, DatasetCreate,
    DatasetGenerate, DatasetImport, ProfileCreate, RunCreate, RunClone,
    CompareRequest,
)
from .security import Auth, AccessError, Identity, COOKIE_NAME
from .services import Workbench, DomainError

STATIC = Path(__file__).parent / "static"
LOGGER = logging.getLogger("veclab")


class BodyLimitMiddleware:
    def __init__(self, app, maximum: int):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.maximum:
                response = JSONResponse({"detail": "请求体超过2MB限制"}, 413)
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare()
    database = Database(settings.database)
    database.initialize()
    auth = Auth(database, settings.session_seconds)
    service = Workbench(database, settings.backups)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.recover_interrupted_runs()
        yield

    app = FastAPI(
        title=NAME,
        version=VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.auth = auth
    app.state.service = service
    app.add_middleware(BodyLimitMiddleware, maximum=settings.max_body_bytes)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def browser_security(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            expected = urlsplit(str(request.base_url))
            supplied = urlsplit(origin)
            if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
                return JSONResponse({"detail": "拒绝跨站写入请求"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(AccessError)
    async def access_error(request: Request, exc: AccessError):
        return JSONResponse({"detail": str(exc)}, exc.status)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse({"detail": str(exc)}, exc.status)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request: Request, exc: sqlite3.IntegrityError):
        return JSONResponse({"detail": "数据关联冲突，请刷新后重试"}, 409)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = []
        for error in exc.errors():
            location = ".".join(str(item) for item in error["loc"])
            errors.append(location + ": " + error["msg"])
        return JSONResponse({"detail": "；".join(errors)[:1000]}, 422)

    def current_user(request: Request) -> Identity:
        identity = auth.authenticate(request.cookies.get(COOKIE_NAME))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            auth.check_csrf(identity, request.headers.get("X-CSRF-Token"))
        return identity

    def attachment(content: str, filename: str, media_type: str) -> Response:
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "software": NAME, "version": VERSION}

    @app.get("/api/setup-status")
    def setup_status():
        return {"initialized": auth.is_initialized(), "version": VERSION}

    @app.post("/api/login")
    def login(model: Login, response: Response):
        token, identity = auth.login(model.username, model.password)
        response.set_cookie(
            COOKIE_NAME, token, max_age=settings.session_seconds,
            httponly=True, secure=settings.secure_cookie, samesite="strict",
        )
        return auth.public_identity(identity)

    @app.get("/api/session")
    def session(identity: Identity = Depends(current_user)):
        return auth.public_identity(identity)

    @app.post("/api/logout")
    def logout(response: Response, identity: Identity = Depends(current_user)):
        auth.logout(identity)
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @app.post("/api/password")
    def password_change(
        model: PasswordChange, response: Response,
        identity: Identity = Depends(current_user),
    ):
        auth.change_password(identity, model.old_password, model.new_password)
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True, "message": "口令已修改，所有会话已撤销"}

    @app.get("/api/dashboard")
    def dashboard(identity: Identity = Depends(current_user)):
        return service.dashboard()

    @app.get("/api/engine")
    def engine(identity: Identity = Depends(current_user)):
        return engine_status()

    @app.get("/api/projects")
    def projects(identity: Identity = Depends(current_user)):
        return service.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(model: ProjectCreate, identity: Identity = Depends(current_user)):
        return service.create_project(identity.username, model)

    @app.put("/api/projects/{identifier}")
    def update_project(
        identifier: str, model: ProjectUpdate,
        identity: Identity = Depends(current_user),
    ):
        return service.update_project(identity.username, identifier, model)

    @app.delete("/api/projects/{identifier}")
    def delete_project(identifier: str, identity: Identity = Depends(current_user)):
        service.delete_project(identity.username, identifier)
        return {"ok": True}

    @app.get("/api/datasets")
    def datasets(project_id: str = "", identity: Identity = Depends(current_user)):
        return service.list_datasets(project_id)

    @app.post("/api/datasets", status_code=201)
    def create_dataset(model: DatasetCreate, identity: Identity = Depends(current_user)):
        return service.create_dataset(identity.username, model)

    @app.post("/api/datasets/generate", status_code=201)
    def generate_dataset(
        model: DatasetGenerate, identity: Identity = Depends(current_user)
    ):
        return service.generate_dataset(identity.username, model)

    @app.post("/api/datasets/import", status_code=201)
    def import_dataset(model: DatasetImport, identity: Identity = Depends(current_user)):
        return service.import_dataset(identity.username, model)

    @app.get("/api/datasets/{identifier}")
    def dataset_detail(identifier: str, identity: Identity = Depends(current_user)):
        return service.get_dataset(identifier)

    @app.get("/api/datasets/{identifier}/export")
    def export_dataset(identifier: str, identity: Identity = Depends(current_user)):
        dataset = service.get_dataset(identifier)
        content = dataset_csv(dataset)
        service.record_export(identity.username, identifier, "dataset-csv")
        return attachment(content, identifier + ".csv", "text/csv")

    @app.delete("/api/datasets/{identifier}")
    def delete_dataset(identifier: str, identity: Identity = Depends(current_user)):
        service.delete_dataset(identity.username, identifier)
        return {"ok": True}

    @app.get("/api/profiles")
    def profiles(identity: Identity = Depends(current_user)):
        return service.list_profiles()

    @app.post("/api/profiles", status_code=201)
    def create_profile(model: ProfileCreate, identity: Identity = Depends(current_user)):
        return service.create_profile(identity.username, model)

    @app.delete("/api/profiles/{identifier}")
    def delete_profile(identifier: str, identity: Identity = Depends(current_user)):
        service.delete_profile(identity.username, identifier)
        return {"ok": True}

    @app.get("/api/runs")
    def runs(
        project_id: str = "", status: str = "",
        identity: Identity = Depends(current_user),
    ):
        return service.list_runs(project_id, status)

    @app.post("/api/runs", status_code=201)
    def create_run(model: RunCreate, identity: Identity = Depends(current_user)):
        return service.create_run(identity.username, model)

    @app.get("/api/runs/{identifier}")
    def run_detail(identifier: str, identity: Identity = Depends(current_user)):
        return service.get_run(identifier)

    @app.post("/api/runs/{identifier}/execute")
    def execute_run(identifier: str, identity: Identity = Depends(current_user)):
        return service.execute_run(identity.username, identifier)

    @app.post("/api/runs/{identifier}/clone", status_code=201)
    def clone_run(
        identifier: str, model: RunClone,
        identity: Identity = Depends(current_user),
    ):
        return service.clone_run(identity.username, identifier, model)

    @app.post("/api/runs/{identifier}/cancel")
    def cancel_run(identifier: str, identity: Identity = Depends(current_user)):
        return service.cancel_run(identity.username, identifier)

    @app.get("/api/runs/{identifier}/report", response_class=HTMLResponse)
    def report(identifier: str, identity: Identity = Depends(current_user)):
        run = service.get_run(identifier)
        content = report_html(run)
        service.record_export(identity.username, identifier, "html-preview")
        return content

    @app.get("/api/runs/{identifier}/export/{format_name}")
    def export_run(
        identifier: str, format_name: str,
        identity: Identity = Depends(current_user),
    ):
        run = service.get_run(identifier)
        if format_name == "csv":
            content, media_type = result_csv(run), "text/csv"
        elif format_name == "json":
            content, media_type = result_json(run), "application/json"
        elif format_name == "html":
            content, media_type = report_html(run), "text/html"
        else:
            raise DomainError("不支持的导出格式", 404)
        service.record_export(identity.username, identifier, format_name)
        return attachment(content, identifier + "." + format_name, media_type)

    @app.post("/api/compare")
    def compare(model: CompareRequest, identity: Identity = Depends(current_user)):
        return compare_runs(service.get_run(model.left), service.get_run(model.right))

    @app.get("/api/audit")
    def audit(
        action: str = "", limit: int = Query(default=200, ge=1, le=1000),
        identity: Identity = Depends(current_user),
    ):
        return service.audit_list(action, limit)

    @app.get("/api/audit/verify")
    def verify_audit(identity: Identity = Depends(current_user)):
        return database.verify_audit()

    @app.get("/api/audit/export")
    def export_audit(identity: Identity = Depends(current_user)):
        rows = service.audit_list(limit=1000)
        return attachment(audit_csv(rows), "audit-latest-1000.csv", "text/csv")

    @app.get("/api/backups")
    def backups(identity: Identity = Depends(current_user)):
        return service.list_backups()

    @app.post("/api/backups", status_code=201)
    def backup(identity: Identity = Depends(current_user)):
        return service.create_backup(identity.username)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
