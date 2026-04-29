# Bookarr

Bookarr is a small Python web app for audiobook requests. Users sign in, search Listenarr, send requests directly to Listenarr, and track request status.

It is built for a personal self-hosted setup: one shared requester password, one shared admin password, SQLite storage, and Docker deployment.

## Features

- Requester and admin roles
- Shared password per role from environment variables
- Requesters see only their own requests
- Admins see everyone's requests and can manually refresh statuses
- Search is proxied through Listenarr
- Requests are sent straight to Listenarr with auto-approval fields
- Status polling keeps local history updated
- Dark, mobile-friendly interface inspired by the `*arr` ecosystem

## Configuration

Bookarr is configured with environment variables.

| Variable | Purpose |
| --- | --- |
| `BOOKARR_SECRET_KEY` | Secret used for signed login cookies. Use a long random value. |
| `BOOKARR_REQUESTER_PASSWORD` | Shared password for normal requesters. |
| `BOOKARR_ADMIN_PASSWORD` | Shared password for admins. |
| `BOOKARR_DATABASE_URL` | Database URL. Docker default is `sqlite:////data/bookarr.db`. |
| `LISTENARR_URL` | Base URL for Listenarr. In Docker this is often `http://listenarr:4545`. |
| `LISTENARR_TOKEN` | Optional Listenarr API key. Leave blank when local auth is disabled. |
| `LISTENARR_AUTH_MODE` | Auth style: `bearer`, `x-api-key`, or `query`. Default: `x-api-key`. |
| `LISTENARR_API_KEY_NAME` | Query-string key name when `LISTENARR_AUTH_MODE=query`. Default: `apikey`. |
| `LISTENARR_SEARCH_PATH` | Search endpoint path. Default: `/api/v1/search/intelligent`. |
| `LISTENARR_SEARCH_QUERY_PARAM` | Search query parameter name. Default: `query`. |
| `LISTENARR_SEARCH_REGION` | Listenarr metadata/search region. Default: `us`. |
| `LISTENARR_REQUEST_PATH` | Request endpoint path. Default: `/api/v1/library/add`. |
| `LISTENARR_ANTIFORGERY_PATH` | Antiforgery token endpoint path. Default: `/api/v1/antiforgery/token`. |
| `LISTENARR_STATUS_PATH` | Status endpoint path. Use `{listenarr_id}` as the placeholder. |
| `BOOKARR_STATUS_POLL_SECONDS` | How often Bookarr checks Listenarr for status updates. |

The defaults follow Listenarr's versioned API (`/api/v1/...`). The paths remain configurable because self-hosted services and forks can change route names.

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --reload-dir ./app --reload-exclude ".venv/*" --reload-exclude "data/*"
```

Then open `http://localhost:8000`.

If the reloader still loops because your virtual environment is inside the project folder, run without reload:

```bash
uvicorn app.main:app
```

## Docker

Build and run:

```bash
docker compose up -d --build
```

Bookarr will be available at `http://localhost:8000`.

## Deploy On Sooner

Your server is `sooner` at `192.168.1.20`, and Listenarr already runs there in Docker. The simplest setup is to place Bookarr on the same Docker network as Listenarr.

1. SSH into `sooner`.
2. Copy this project to a directory such as `/opt/bookarr`.
3. Confirm the Docker network used by Listenarr:

```bash
docker network ls
docker inspect listenarr --format '{{json .NetworkSettings.Networks}}'
```

4. If Listenarr is on a network named `arr`, the included `docker-compose.yml` can be used as-is. If the network name is different, update the `networks` section.
5. Set the passwords, secret key, token, Listenarr URL, and endpoint paths in `docker-compose.yml`.
6. Start Bookarr:

```bash
docker compose up -d --build
```

From your LAN, open `http://192.168.1.20:8000`.

Cloudflare Tunnel can point at `http://bookarr:8000` from the same Docker network, or at `http://192.168.1.20:8000` from the host. We can wire that up in the later Cloudflare session.

## Data

The compose file stores SQLite data in the `bookarr-data` Docker volume. Removing the container will not remove request history. Removing the volume will.

## Notes On Listenarr API Shape

Bookarr sends request payloads like:

```json
{
  "metadata": {
    "title": "Book title",
    "authors": ["Author name"],
    "imageUrl": "https://...",
    "asin": "B08G9PRS1K"
  },
  "monitored": true,
  "autoSearch": true
}
```

Search results are normalized from common fields such as `asin`, `isbn`, `id`, `title`, `authors`, and `imageUrl`. Status values are mapped into `sent`, `downloading`, `completed`, or `failed`.
