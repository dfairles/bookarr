from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.charts import get_enriched_top_audiobooks
from app.config import get_settings
from app.database import SessionLocal, db_session, init_db
from app.listenarr import ListenarrClient, ListenarrError
from app.models import AudiobookRequest, RequestStatus, Role, utcnow


settings = get_settings()
serializer = URLSafeSerializer(settings.secret_key, salt="bookarr-session")

FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "requested": ("success", "Your request has been submitted."),
    "duplicate": ("info", "You've already requested that audiobook."),
    "polled": ("success", "Statuses refreshed."),
    "poll_error": ("warn", "Statuses refreshed, but some requests could not be checked — see errors below."),
    "deleted": ("success", "Request deleted."),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_statuses, "interval", seconds=settings.status_poll_seconds, next_run_time=datetime.now(timezone.utc))
    if settings.completed_retention_days > 0:
        scheduler.add_job(cleanup_completed_requests, "interval", hours=24, next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


def current_user(request: Request) -> dict[str, str] | None:
    cookie = request.cookies.get("bookarr_session")
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("role") not in {Role.requester.value, Role.admin.value}:
        return None
    return {"name": data.get("name", "user"), "role": data["role"]}


def require_user(request: Request) -> dict[str, str]:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def render(request: Request, template: str, **context):
    flash_key = request.query_params.get("flash", "")
    flash_type, flash_message = FLASH_MESSAGES.get(flash_key, ("", ""))
    flash = {"type": flash_type, "message": flash_message} if flash_message else None
    return templates.TemplateResponse(
        request,
        template,
        {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "user": current_user(request),
            "flash": flash,
            **context,
        },
    )


@app.get("/")
async def home(request: Request, db: Annotated[Session, Depends(db_session)]):
    user = require_user(request)
    rows = db.scalars(request_stmt(user)).all()
    return render(request, "dashboard.html", requests=rows)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/login")
async def login_page(request: Request):
    return render(request, "login.html", error="")


@app.post("/login")
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    abs_url = settings.audiobookshelf_url.rstrip("/")
    if not abs_url:
        return render(request, "login.html", error="Audiobookshelf URL is not configured on this server.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{abs_url}/login",
                json={"username": username, "password": password},
                headers={"Accept": "application/json"},
            )
    except Exception:
        return render(request, "login.html", error="Could not reach Audiobookshelf — please try again.")
    if resp.status_code != 200:
        return render(request, "login.html", error="Invalid username or password.")
    data = resp.json()
    abs_user = data.get("user", {})
    abs_type = abs_user.get("type", "user")
    role = Role.admin if abs_type in ("root", "admin") else Role.requester
    user_name = abs_user.get("username") or username
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        "bookarr_session",
        serializer.dumps({"name": user_name, "role": role.value}),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("bookarr_session")
    return response


@app.get("/search")
async def search_page(request: Request, q: str = "", db: Annotated[Session, Depends(db_session)] = None):
    user = require_user(request)
    results: list[dict[str, str]] = []
    error = ""
    user_source_ids: set[str] = set()
    in_library_ids: set[str] = set()
    top_books: list[dict[str, str]] = []
    if q.strip():
        try:
            async with ListenarrClient(settings) as client:
                results = await client.search(q.strip())
                checks = await asyncio.gather(
                    *[client.in_library(b["source_id"]) for b in results],
                    return_exceptions=True,
                )
            in_library_ids = {
                b["source_id"]
                for b, hit in zip(results, checks)
                if hit is True
            }
        except ListenarrError as exc:
            error = str(exc)
        user_source_ids = set(
            db.scalars(
                select(AudiobookRequest.source_id).where(AudiobookRequest.user_name == user["name"])
            ).all()
        )
    else:
        async with ListenarrClient(settings) as client:
            top_books = await get_enriched_top_audiobooks(client.search)
            chart_source_ids = [b.get("source_id", "") for b in top_books]
            checks = await asyncio.gather(
                *[client.in_library(sid) for sid in chart_source_ids],
                return_exceptions=True,
            )
        in_library_ids = {
            sid for sid, hit in zip(chart_source_ids, checks) if hit is True
        }
        user_source_ids = set(
            db.scalars(
                select(AudiobookRequest.source_id).where(AudiobookRequest.user_name == user["name"])
            ).all()
        )
    return render(request, "search.html", q=q, results=results, error=error,
                  user_source_ids=user_source_ids, in_library_ids=in_library_ids, top_books=top_books)


@app.post("/request")
async def request_book(
    request: Request,
    db: Annotated[Session, Depends(db_session)],
    source_id: Annotated[str, Form()],
    title: Annotated[str, Form()],
    author: Annotated[str, Form()] = "",
    cover_url: Annotated[str, Form()] = "",
):
    user = require_user(request)
    existing = db.scalar(
        select(AudiobookRequest).where(
            AudiobookRequest.user_name == user["name"],
            AudiobookRequest.source_id == source_id,
        )
    )
    if existing:
        return RedirectResponse("/?flash=duplicate", status_code=status.HTTP_303_SEE_OTHER)

    book = {"source_id": source_id, "title": title, "author": author, "cover_url": cover_url}
    row = AudiobookRequest(user_name=user["name"], **book, status=RequestStatus.sent)
    try:
        async with ListenarrClient(settings) as client:
            listenarr_response = await client.request_book(book)
        row.listenarr_id = listenarr_response["listenarr_id"]
    except ListenarrError as exc:
        row.status = RequestStatus.failed
        row.error_message = str(exc)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/?flash=duplicate", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/?flash=requested", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin")
async def admin_page(request: Request, db: Annotated[Session, Depends(db_session)]):
    user = require_user(request)
    if user["role"] != Role.admin.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    rows = db.scalars(select(AudiobookRequest).order_by(AudiobookRequest.updated_at.desc())).all()
    return render(request, "admin.html", requests=rows)


@app.post("/poll")
async def poll_now(request: Request):
    user = require_user(request)
    if user["role"] != Role.admin.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    had_errors = await poll_statuses()
    flash = "poll_error" if had_errors else "polled"
    return RedirectResponse(f"/admin?flash={flash}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/requests/{request_id}/delete")
async def delete_failed_request(
    request: Request,
    request_id: int,
    db: Annotated[Session, Depends(db_session)],
):
    user = require_user(request)
    if user["role"] != Role.admin.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    row = db.get(AudiobookRequest, request_id)
    if row and (row.status == RequestStatus.failed or row.error_message):
        db.delete(row)
        db.commit()
    return RedirectResponse("/admin?flash=deleted", status_code=status.HTTP_303_SEE_OTHER)


def request_stmt(user: dict[str, str]):
    stmt = select(AudiobookRequest).order_by(AudiobookRequest.updated_at.desc())
    if user["role"] != Role.admin.value:
        stmt = stmt.where(AudiobookRequest.user_name == user["name"])
    return stmt


async def poll_statuses() -> bool:
    db = SessionLocal()
    had_errors = False
    try:
        rows = db.scalars(
            select(AudiobookRequest).where(AudiobookRequest.status.in_([RequestStatus.sent, RequestStatus.downloading]))
        ).all()
        async with ListenarrClient(settings) as client:
            for row in rows:
                if not row.listenarr_id or not row.listenarr_id.isdigit():
                    try:
                        resolved_id = await client.resolve_library_id(row.source_id)
                    except ListenarrError as exc:
                        row.status = RequestStatus.failed
                        row.error_message = str(exc)
                        had_errors = True
                        continue
                    if not resolved_id:
                        row.status = RequestStatus.failed
                        row.error_message = "Could not find this request in Listenarr's library."
                        had_errors = True
                        continue
                    row.listenarr_id = resolved_id
                    row.error_message = ""
                try:
                    new_status = await client.get_status(row.listenarr_id)
                except ListenarrError as exc:
                    row.error_message = str(exc)
                    had_errors = True
                    continue
                if new_status and new_status != row.status:
                    row.status = new_status
                    row.error_message = ""
        db.commit()
    finally:
        db.close()
    return had_errors


async def cleanup_completed_requests() -> None:
    cutoff = utcnow() - timedelta(days=settings.completed_retention_days)
    db = SessionLocal()
    try:
        db.execute(
            delete(AudiobookRequest).where(
                AudiobookRequest.status == RequestStatus.completed,
                AudiobookRequest.updated_at < cutoff,
            )
        )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(poll_statuses())
