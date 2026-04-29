from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, db_session, init_db
from app.listenarr import ListenarrClient, ListenarrError
from app.models import AudiobookRequest, RequestStatus, Role


settings = get_settings()
app = FastAPI(title=settings.app_name)
serializer = URLSafeSerializer(settings.secret_key, salt="bookarr-session")
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
    return templates.TemplateResponse(
        request,
        template,
        {
            "app_name": settings.app_name,
            "user": current_user(request),
            **context,
        },
    )


@app.on_event("startup")
async def startup() -> None:
    init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_statuses, "interval", seconds=settings.status_poll_seconds, next_run_time=datetime.utcnow())
    scheduler.start()


@app.get("/")
async def home(request: Request, db: Annotated[Session, Depends(db_session)]):
    user = require_user(request)
    rows = db.scalars(request_stmt(user).limit(20)).all()
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
    name: Annotated[str, Form()],
    role: Annotated[Role, Form()],
    password: Annotated[str, Form()],
):
    expected = settings.admin_password if role == Role.admin else settings.requester_password
    if password != expected:
        return render(request, "login.html", error="That password did not match the selected role.")
    user_name = name.strip() or role.value
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
async def search_page(request: Request, q: str = ""):
    require_user(request)
    results: list[dict[str, str]] = []
    error = ""
    if q.strip():
        try:
            results = await ListenarrClient(settings).search(q.strip())
        except ListenarrError as exc:
            error = str(exc)
    return render(request, "search.html", q=q, results=results, error=error)


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
    book = {
        "source_id": source_id,
        "title": title,
        "author": author,
        "cover_url": cover_url,
    }
    row = AudiobookRequest(user_name=user["name"], **book, status=RequestStatus.sent)
    try:
        listenarr_response = await ListenarrClient(settings).request_book(book)
        row.listenarr_id = listenarr_response["listenarr_id"]
    except ListenarrError as exc:
        row.status = RequestStatus.failed
        row.error_message = str(exc)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


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
    await poll_statuses()
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


def request_stmt(user: dict[str, str]):
    stmt = select(AudiobookRequest).order_by(AudiobookRequest.updated_at.desc())
    if user["role"] != Role.admin.value:
        stmt = stmt.where(AudiobookRequest.user_name == user["name"])
    return stmt


async def poll_statuses() -> None:
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(AudiobookRequest).where(AudiobookRequest.status.in_([RequestStatus.sent, RequestStatus.downloading]))
        ).all()
        client = ListenarrClient(settings)
        for row in rows:
            try:
                new_status = await client.get_status(row.listenarr_id or row.source_id)
            except ListenarrError as exc:
                row.error_message = str(exc)
                continue
            if new_status and new_status != row.status:
                row.status = new_status
                row.error_message = ""
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(poll_statuses())
