# Copyright (C) 2024-2026 Bookarr Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import bcrypt
import httpx

from app.models import Role


class AuthError(Exception):
    pass


async def authenticate_audiobookshelf(username: str, password: str, url: str) -> tuple[str, Role]:
    abs_url = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{abs_url}/login",
                json={"username": username, "password": password},
                headers={"Accept": "application/json"},
            )
    except Exception:
        raise AuthError("Could not reach Audiobookshelf — please try again.")
    if resp.status_code != 200:
        raise AuthError("Invalid username or password.")
    data = resp.json()
    abs_user = data.get("user", {})
    abs_type = abs_user.get("type", "user")
    role = Role.admin if abs_type in ("root", "admin") else Role.requester
    return abs_user.get("username") or username, role


async def authenticate_jellyfin(username: str, password: str, url: str) -> tuple[str, Role]:
    jf_url = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{jf_url}/Users/AuthenticateByName",
                json={"Username": username, "Pw": password},
                headers={
                    "Accept": "application/json",
                    "X-Emby-Authorization": (
                        'MediaBrowser Client="Bookarr", Device="Bookarr", '
                        'DeviceId="bookarr", Version="1"'
                    ),
                },
            )
    except Exception:
        raise AuthError("Could not reach Jellyfin — please try again.")
    if resp.status_code != 200:
        raise AuthError("Invalid username or password.")
    data = resp.json()
    jf_user = data.get("User", {})
    is_admin = jf_user.get("Policy", {}).get("IsAdministrator", False)
    role = Role.admin if is_admin else Role.requester
    return jf_user.get("Name") or username, role


def authenticate_local(username: str, password: str, db) -> tuple[str, Role]:
    from sqlalchemy import select
    from app.models import User

    user = db.scalar(select(User).where(User.username == username))
    if not user or not bcrypt.checkpw(password.encode(), user.hashed_password.encode()):
        raise AuthError("Invalid username or password.")
    return user.username, user.role


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
