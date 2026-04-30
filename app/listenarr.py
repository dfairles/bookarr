from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import httpx

from app.config import Settings
from app.models import RequestStatus


class ListenarrError(RuntimeError):
    pass


class ListenarrClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.listenarr_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        auth_mode = self.settings.listenarr_auth_mode.lower()
        if self.settings.listenarr_token and auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self.settings.listenarr_token}"
        if self.settings.listenarr_token and auth_mode == "x-api-key":
            headers["X-Api-Key"] = self.settings.listenarr_token
        return headers

    def _params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(params or {})
        auth_mode = self.settings.listenarr_auth_mode.lower()
        if self.settings.listenarr_token and auth_mode == "query":
            merged[self.settings.listenarr_api_key_name] = self.settings.listenarr_token
        return merged

    async def search(self, query: str) -> list[dict[str, str]]:
        params = {self.settings.listenarr_search_query_param: query}
        if self.settings.listenarr_search_region:
            params["region"] = self.settings.listenarr_search_region
        payload = await self._request("GET", self.settings.listenarr_search_path, params=params)
        raw_results = payload.get("results", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_results, list):
            return []
        return [self._normalize_result(item) for item in raw_results if isinstance(item, dict)]

    async def request_book(self, book: dict[str, str]) -> dict[str, str]:
        payload = {
            "metadata": {
                "title": book["title"],
                "authors": [book.get("author", "")] if book.get("author") else [],
                "imageUrl": book.get("cover_url", ""),
            },
            "monitored": True,
            "autoSearch": True,
        }
        self._attach_external_id(payload["metadata"], book["source_id"])
        response = await self._request("POST", self.settings.listenarr_request_path, json=payload)
        listenarr_id = self._listenarr_id(response)
        return {"listenarr_id": str(listenarr_id or book["source_id"])}

    async def get_status(self, listenarr_id: str) -> RequestStatus | None:
        if not listenarr_id:
            return None
        path = self.settings.listenarr_status_path.format(listenarr_id=listenarr_id)
        payload = await self._request("GET", path)
        if not isinstance(payload, dict):
            return None
        return self._normalize_status_payload(payload)

    async def resolve_library_id(self, source_id: str) -> str:
        clean_id = (source_id or "").strip()
        compact_id = clean_id.replace("-", "")
        if len(clean_id) == 10 and clean_id.isalnum():
            payload = await self._request("GET", f"/api/v1/library/by-asin/{clean_id}")
        elif len(compact_id) in {10, 13} and compact_id.isdigit():
            payload = await self._request("GET", f"/api/v1/library/by-isbn/{clean_id}")
        else:
            return ""
        listenarr_id = self._listenarr_id(payload)
        return str(listenarr_id or "")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        kwargs["params"] = self._params(kwargs.get("params"))
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if method.upper() == "POST":
                    headers["X-XSRF-TOKEN"] = await self._antiforgery_token(client)
                response = await client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                if not response.content:
                    return {}
                try:
                    return response.json()
                except ValueError as exc:
                    raise ListenarrError("Listenarr returned a non-JSON response") from exc
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise ListenarrError(
                f"Listenarr returned {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ListenarrError(f"Could not reach Listenarr: {exc}") from exc

    async def _antiforgery_token(self, client: httpx.AsyncClient) -> str:
        path = self.settings.listenarr_antiforgery_path
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        response = await client.get(url, headers=self._headers(), params=self._params())
        response.raise_for_status()
        token = self._extract_antiforgery_token(response)
        if not token:
            content_type = response.headers.get("content-type", "unknown")
            raise ListenarrError(
                f"Listenarr did not return an antiforgery token from {path} "
                f"(content-type: {content_type})"
            )
        return token

    def _extract_antiforgery_token(self, response: httpx.Response) -> str:
        body_token = self._extract_antiforgery_body_token(response)
        if body_token:
            return body_token
        for name in ("X-XSRF-TOKEN", "XSRF-TOKEN", "X-CSRF-TOKEN", "RequestVerificationToken"):
            token = self._clean_header_token(response.headers.get(name, ""))
            if token:
                return token
        for name in ("XSRF-TOKEN", "X-XSRF-TOKEN", "CSRF-TOKEN", "ANTIFORGERY-TOKEN"):
            token = self._clean_header_token(response.cookies.get(name, ""))
            if token:
                return token
        for name, token in response.cookies.items():
            if any(part in name.lower() for part in ("xsrf", "csrf", "antiforgery")):
                clean_token = self._clean_header_token(token)
                if clean_token:
                    return clean_token
        return ""

    def _extract_antiforgery_body_token(self, response: httpx.Response) -> str:
        if not response.content:
            return ""
        try:
            payload = response.json()
        except ValueError:
            return self._clean_header_token(response.text)
        if isinstance(payload, str):
            return self._clean_header_token(payload)
        if isinstance(payload, dict):
            return self._clean_header_token(
                self._first_value(
                    payload,
                    [
                        "token",
                        "xsrfToken",
                        "csrfToken",
                        "antiForgeryToken",
                        "antiforgeryToken",
                        "requestToken",
                    ],
                )
            )
        return ""

    def _clean_header_token(self, value: Any) -> str:
        token = unquote(str(value or "").strip().strip('"'))
        if not token or any(char in token for char in "\r\n<>"):
            return ""
        return token

    def _normalize_result(self, item: dict[str, Any]) -> dict[str, str]:
        data = item.get("metadata") if isinstance(item.get("metadata"), dict) else item
        source_id = self._first_value(data, ["asin", "isbn", "id", "bookId", "foreignId", "goodreadsId", "titleSlug"])
        title = self._first_value(data, ["title", "bookTitle", "name"])
        author = self._first_value(data, ["author", "authorName", "authors"])
        cover = self._first_value(data, ["imageUrl", "coverUrl", "cover", "image", "posterUrl"])
        if isinstance(author, list):
            author = ", ".join(str(value) for value in author)
        if isinstance(cover, dict):
            cover = self._first_value(cover, ["url", "remoteUrl"])
        if isinstance(cover, str) and cover.startswith("/"):
            cover = f"{self.base_url}{cover}"
        return {
            "source_id": str(source_id or title or ""),
            "title": str(title or "Untitled audiobook"),
            "author": str(author or "Unknown author"),
            "cover_url": str(cover or ""),
        }

    def _attach_external_id(self, metadata: dict[str, Any], source_id: str) -> None:
        clean_id = (source_id or "").strip()
        if not clean_id:
            return
        compact_id = clean_id.replace("-", "")
        if len(compact_id) in {10, 13} and compact_id.isdigit():
            metadata["isbn"] = clean_id
        elif len(clean_id) == 10 and clean_id.isalnum():
            metadata["asin"] = clean_id
        else:
            metadata["externalId"] = clean_id

    def _listenarr_id(self, response: Any) -> Any:
        if not isinstance(response, dict):
            return ""
        listenarr_id = self._first_value(response, ["id", "bookId", "listenarrId", "requestId"])
        if listenarr_id:
            return listenarr_id
        audiobook = response.get("audiobook")
        if isinstance(audiobook, dict):
            return self._first_value(audiobook, ["id", "bookId", "listenarrId"])
        return ""

    def _normalize_status(self, value: str) -> RequestStatus | None:
        normalized = value.lower().replace("_", " ").replace("-", " ")
        if any(word in normalized for word in ["complete", "completed", "available", "downloaded"]):
            return RequestStatus.completed
        if any(word in normalized for word in ["fail", "error", "missing"]):
            return RequestStatus.failed
        if any(word in normalized for word in ["download", "grabbed", "importing", "processing"]):
            return RequestStatus.downloading
        if any(word in normalized for word in ["sent", "queued", "requested", "pending"]):
            return RequestStatus.sent
        return None

    def _normalize_status_payload(self, payload: dict[str, Any]) -> RequestStatus | None:
        status = self._normalize_status(str(self._first_value(payload, ["status", "state", "downloadStatus"]) or ""))
        if status:
            return status
        files = payload.get("files")
        if isinstance(files, list) and files:
            return RequestStatus.completed
        if self._first_value(payload, ["filePath", "fileSize"]):
            return RequestStatus.completed
        if payload.get("wanted") is True or payload.get("monitored") is True:
            return RequestStatus.sent
        return None

    def _first_value(self, data: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        return None
