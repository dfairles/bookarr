# Copyright (C) 2024-2026 Bookarr Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import AuthError, authenticate_audiobookshelf, authenticate_jellyfin, authenticate_local, hash_password
from app.charts import get_enriched_top_audiobooks
from app.config import get_settings
from app.database import SessionLocal, db_session, init_db
from app.listenarr import ListenarrClient, ListenarrError
from app.models import AudiobookRequest, RequestStatus, Role, User, utcnow


settings = get_settings()
# Cookies are signed (tamper-evident) but not encrypted — role/username are readable
# in the browser, but cannot be forged without the secret_key.
serializer = URLSafeSerializer(settings.secret_key, salt="bookarr-session")

# Flash messages are passed as ?flash=<key> query params after redirects.
# Unknown keys are silently ignored — the template receives flash=None.
FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "requested": ("success", "Your request has been submitted."),
    "pending": ("info", "Your request is awaiting admin approval."),
    "duplicate": ("info", "You've already requested that audiobook."),
    "polled": ("success", "Statuses refreshed."),
    "poll_error": ("warn", "Statuses refreshed, but some requests could not be checked — see errors below."),
    "deleted": ("success", "Request deleted."),
    "approved": ("success", "Request approved and sent to Listenarr."),
    "denied": ("info", "Request denied."),
    "user_created": ("success", "User created."),
    "user_deleted": ("success", "User deleted."),
    "user_updated": ("success", "User updated."),
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
    """Return the signed session data, or None if the cookie is absent or tampered."""
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


def require_admin(request: Request) -> dict[str, str]:
    user = require_user(request)
    if user["role"] != Role.admin.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
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
            "auth_mode": settings.auth_mode,
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


@app.get("/cover")
async def cover_proxy(url: str, request: Request):
    # Proxy cover images so the browser never needs direct access to the Listenarr host.
    # Only Listenarr-origin and public http(s) URLs are allowed to prevent open-redirect abuse.
    require_user(request)
    if not url:
        raise HTTPException(status_code=400)
    listenarr_base = settings.listenarr_url.rstrip("/")
    allowed_prefixes = (listenarr_base, "https://", "http://")
    if not any(url.startswith(p) for p in allowed_prefixes):
        raise HTTPException(status_code=400)
    auth_mode = settings.listenarr_auth_mode.lower()
    headers: dict[str, str] = {}
    if settings.listenarr_token:
        if auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {settings.listenarr_token}"
        elif auth_mode == "x-api-key":
            headers["X-Api-Key"] = settings.listenarr_token
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError:
        raise HTTPException(status_code=502)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code)
    content_type = resp.headers.get("content-type", "image/jpeg")
    return Response(content=resp.content, media_type=content_type)


@app.get("/login")
async def login_page(request: Request):
    return render(request, "login.html", error="")


@app.post("/login")
async def login(
    request: Request,
    db: Annotated[Session, Depends(db_session)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    auth_mode = settings.auth_mode.lower()
    try:
        if auth_mode == "audiobookshelf":
            if not settings.audiobookshelf_url:
                return render(request, "login.html", error="Audiobookshelf URL is not configured on this server.")
            name, role = await authenticate_audiobookshelf(username, password, settings.audiobookshelf_url)
        elif auth_mode == "jellyfin":
            if not settings.jellyfin_url:
                return render(request, "login.html", error="Jellyfin URL is not configured on this server.")
            name, role = await authenticate_jellyfin(username, password, settings.jellyfin_url)
        elif auth_mode == "local":
            name, role = authenticate_local(username, password, db)
        else:
            return render(request, "login.html", error="Unknown authentication mode configured.")
    except AuthError as exc:
        return render(request, "login.html", error=str(exc))

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        "bookarr_session",
        serializer.dumps({"name": name, "role": role.value}),
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
    is_admin = user["role"] == Role.admin.value
    # auto_approve_all bypasses approval for everyone; admin_auto_approve only for admins.
    auto_send = settings.auto_approve_all or (is_admin and settings.admin_auto_approve)

    if auto_send:
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
    else:
        row = AudiobookRequest(user_name=user["name"], **book, status=RequestStatus.pending_approval)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return RedirectResponse("/?flash=duplicate", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse("/?flash=pending", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin")
async def admin_page(request: Request, db: Annotated[Session, Depends(db_session)]):
    require_admin(request)
    rows = db.scalars(select(AudiobookRequest).order_by(AudiobookRequest.updated_at.desc())).all()
    return render(request, "admin.html", requests=rows)


@app.post("/poll")
async def poll_now(request: Request):
    require_admin(request)
    had_errors = await poll_statuses()
    flash = "poll_error" if had_errors else "polled"
    return RedirectResponse(f"/admin?flash={flash}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/requests/{request_id}/approve")
async def approve_request(
    request: Request,
    request_id: int,
    db: Annotated[Session, Depends(db_session)],
):
    require_admin(request)
    row = db.get(AudiobookRequest, request_id)
    if not row or row.status != RequestStatus.pending_approval:
        return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    book = {"source_id": row.source_id, "title": row.title, "author": row.author, "cover_url": row.cover_url}
    try:
        async with ListenarrClient(settings) as client:
            listenarr_response = await client.request_book(book)
        row.listenarr_id = listenarr_response["listenarr_id"]
        row.status = RequestStatus.sent
        row.error_message = ""
    except ListenarrError as exc:
        row.status = RequestStatus.failed
        row.error_message = str(exc)
    db.commit()
    return RedirectResponse("/admin?flash=approved", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/requests/{request_id}/deny")
async def deny_request(
    request: Request,
    request_id: int,
    db: Annotated[Session, Depends(db_session)],
    reason: Annotated[str, Form()] = "",
):
    require_admin(request)
    row = db.get(AudiobookRequest, request_id)
    if not row or row.status != RequestStatus.pending_approval:
        return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    row.status = RequestStatus.denied
    row.denied_reason = reason.strip()
    db.commit()
    return RedirectResponse("/admin?flash=denied", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/requests/{request_id}/delete")
async def delete_request(
    request: Request,
    request_id: int,
    db: Annotated[Session, Depends(db_session)],
):
    require_admin(request)
    row = db.get(AudiobookRequest, request_id)
    if row and (row.status in (RequestStatus.failed, RequestStatus.denied) or row.error_message):
        db.delete(row)
        db.commit()
    return RedirectResponse("/admin?flash=deleted", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/users")
async def admin_users_page(request: Request, db: Annotated[Session, Depends(db_session)]):
    require_admin(request)
    if settings.auth_mode.lower() != "local":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    users = db.scalars(select(User).order_by(User.username)).all()
    return render(request, "admin_users.html", users=users, error="")


@app.post("/admin/users")
async def create_user(
    request: Request,
    db: Annotated[Session, Depends(db_session)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role: Annotated[str, Form()] = "requester",
):
    require_admin(request)
    if settings.auth_mode.lower() != "local":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    user_role = Role.admin if role == "admin" else Role.requester
    new_user = User(username=username.strip(), hashed_password=hash_password(password), role=user_role)
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        users = db.scalars(select(User).order_by(User.username)).all()
        return render(request, "admin_users.html", users=users, error="Username already exists.")
    return RedirectResponse("/admin/users?flash=user_created", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/delete")
async def delete_user(
    request: Request,
    user_id: int,
    db: Annotated[Session, Depends(db_session)],
):
    require_admin(request)
    if settings.auth_mode.lower() != "local":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    row = db.get(User, user_id)
    if row:
        db.delete(row)
        db.commit()
    return RedirectResponse("/admin/users?flash=user_deleted", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/role")
async def update_user_role(
    request: Request,
    user_id: int,
    db: Annotated[Session, Depends(db_session)],
    role: Annotated[str, Form()],
):
    require_admin(request)
    if settings.auth_mode.lower() != "local":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    row = db.get(User, user_id)
    if row:
        row.role = Role.admin if role == "admin" else Role.requester
        db.commit()
    return RedirectResponse("/admin/users?flash=user_updated", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/password")
async def reset_user_password(
    request: Request,
    user_id: int,
    db: Annotated[Session, Depends(db_session)],
    new_password: Annotated[str, Form()],
):
    require_admin(request)
    if settings.auth_mode.lower() != "local":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    row = db.get(User, user_id)
    if row:
        row.hashed_password = hash_password(new_password)
        db.commit()
    return RedirectResponse("/admin/users?flash=user_updated", status_code=status.HTTP_303_SEE_OTHER)


def request_stmt(user: dict[str, str]):
    """Build the dashboard query: admins see all requests, requesters see only their own."""
    stmt = select(AudiobookRequest).order_by(AudiobookRequest.updated_at.desc())
    if user["role"] != Role.admin.value:
        stmt = stmt.where(AudiobookRequest.user_name == user["name"])
    return stmt


async def poll_statuses() -> bool:
    """Poll Listenarr for the current status of all in-flight requests.

    For rows whose listenarr_id is not yet a numeric library ID (e.g. it's still
    an ASIN/ISBN from the original request), the ID is first resolved via the
    library lookup before status is fetched. Returns True if any row errored.
    """
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
