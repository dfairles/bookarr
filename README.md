<img width="989" height="453" alt="image" src="https://github.com/user-attachments/assets/3b0cd106-a345-41b4-a447-5583292ac1a0" />

# Bookarr

Bookarr is a self-hosted audiobook request manager. Users search for audiobooks via Listenarr, submit requests, and track their status. Admins approve requests and manage the queue.

## Features

- Three auth backends: Audiobookshelf, Jellyfin, or local accounts managed in Bookarr
- Requester and admin roles derived from your auth server, or set manually for local accounts
- Admin approval workflow — requester submissions wait for sign-off before being sent to Listenarr
- Optional auto-approve for all requests or admins only
- Search proxied through Listenarr with "In Library" status badges
- Status polling keeps request history up to date
- Dark, mobile-friendly interface inspired by the `*arr` ecosystem

---

## Quick Start (Docker)

The fastest path is to pull the pre-built image and run it with Docker Compose.

**1. Create a `docker-compose.yml`** — pick the example for your auth setup below, then fill in your values.

**2. Start Bookarr:**

```bash
docker compose up -d
```

Bookarr will be available at `http://localhost:8000` (or whatever port you mapped).

---

## Docker Compose Examples

### Audiobookshelf auth

Users log in with their existing Audiobookshelf account. Audiobookshelf `root` and `admin` users become Bookarr admins; all others become requesters.

```yaml
services:
  bookarr:
    image: ghcr.io/dfairles/bookarr:latest
    container_name: bookarr
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      # Required
      BOOKARR_SECRET_KEY: "change-this-to-a-long-random-string"
      BOOKARR_AUTH_MODE: "audiobookshelf"
      AUDIOBOOKSHELF_URL: "http://audiobookshelf:13378"
      LISTENARR_URL: "http://listenarr:4545"
      LISTENARR_TOKEN: ""
      # Optional — defaults shown
      LISTENARR_AUTH_MODE: "x-api-key"
      BOOKARR_STATUS_POLL_SECONDS: "300"
      BOOKARR_COMPLETED_RETENTION_DAYS: "30"
    volumes:
      - bookarr-data:/data
    networks:
      - arr

networks:
  arr:
    external: true

volumes:
  bookarr-data:
```

### Jellyfin auth

Users log in with their Jellyfin account. Jellyfin administrators become Bookarr admins.

```yaml
services:
  bookarr:
    image: ghcr.io/dfairles/bookarr:latest
    container_name: bookarr
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      # Required
      BOOKARR_SECRET_KEY: "change-this-to-a-long-random-string"
      BOOKARR_AUTH_MODE: "jellyfin"
      JELLYFIN_URL: "http://jellyfin:8096"
      LISTENARR_URL: "http://listenarr:4545"
      LISTENARR_TOKEN: ""
      # Optional — defaults shown
      LISTENARR_AUTH_MODE: "x-api-key"
      BOOKARR_STATUS_POLL_SECONDS: "300"
      BOOKARR_COMPLETED_RETENTION_DAYS: "30"
    volumes:
      - bookarr-data:/data
    networks:
      - arr

networks:
  arr:
    external: true

volumes:
  bookarr-data:
```

### Local auth

Bookarr manages its own user accounts. Set `BOOKARR_ADMIN_SEED_PASSWORD` and an `admin` account will be created automatically on first run. Log in, then use **Admin → Users** to add more users.

```yaml
services:
  bookarr:
    image: ghcr.io/dfairles/bookarr:latest
    container_name: bookarr
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      # Required
      BOOKARR_SECRET_KEY: "change-this-to-a-long-random-string"
      BOOKARR_AUTH_MODE: "local"
      BOOKARR_ADMIN_SEED_PASSWORD: "change-this-after-first-login"
      LISTENARR_URL: "http://listenarr:4545"
      LISTENARR_TOKEN: ""
      # Optional — defaults shown
      LISTENARR_AUTH_MODE: "x-api-key"
      BOOKARR_STATUS_POLL_SECONDS: "300"
      BOOKARR_COMPLETED_RETENTION_DAYS: "30"
    volumes:
      - bookarr-data:/data
    networks:
      - arr

networks:
  arr:
    external: true

volumes:
  bookarr-data:
```

---

## Networking

Bookarr needs to reach Listenarr (and your auth server if using Audiobookshelf or Jellyfin) by hostname. The easiest way is to put Bookarr on the same Docker network.

**Find your existing network:**

```bash
docker network ls
docker inspect listenarr --format '{{json .NetworkSettings.Networks}}'
```

Replace `arr` in the examples above with the actual network name. If you don't have an existing network, create one:

```bash
docker network create arr
```

Then add `arr` (or your chosen name) to every relevant container's `networks:` block.

---

## Configuration Reference

All configuration is via environment variables.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOOKARR_SECRET_KEY` | — | **Required.** Secret for signed session cookies. Use a long random value. |
| `BOOKARR_DATABASE_URL` | `sqlite:////data/bookarr.db` | Database path. The default works with the Docker volume mount. |
| `BOOKARR_AUTH_MODE` | `audiobookshelf` | Auth backend: `audiobookshelf`, `jellyfin`, or `local`. |
| `AUDIOBOOKSHELF_URL` | — | Base URL of your Audiobookshelf server. Required when `BOOKARR_AUTH_MODE=audiobookshelf`. |
| `JELLYFIN_URL` | — | Base URL of your Jellyfin server. Required when `BOOKARR_AUTH_MODE=jellyfin`. |
| `BOOKARR_ADMIN_SEED_PASSWORD` | — | Local auth only. Password for the auto-created `admin` account on first run. |
| `BOOKARR_AUTO_APPROVE_ALL` | `false` | When `true`, all requests go straight to Listenarr without approval. |
| `BOOKARR_ADMIN_AUTO_APPROVE` | `true` | When `true`, admin requests skip the approval queue. Ignored when `BOOKARR_AUTO_APPROVE_ALL=true`. |
| `LISTENARR_URL` | `http://listenarr:4545` | Base URL for Listenarr. |
| `LISTENARR_TOKEN` | — | Listenarr API key. Leave blank if auth is disabled. |
| `LISTENARR_AUTH_MODE` | `x-api-key` | How to send the token: `bearer`, `x-api-key`, or `query`. |
| `BOOKARR_STATUS_POLL_SECONDS` | `300` | How often to poll Listenarr for status updates. |
| `BOOKARR_COMPLETED_RETENTION_DAYS` | `30` | Days to keep completed requests. Set to `0` to disable cleanup. |
| `BOOKARR_VERSION` | `0.3` | Version label in the UI. CI builds stamp this automatically. |

---

## Data

The Docker volume `bookarr-data` is mounted at `/data` inside the container. The SQLite database lives there. Removing the container does not remove the volume or your request history. To start fresh:

```bash
docker compose down -v
```

---

## Deploy on a Home Server

1. SSH into your server.
2. Create a directory and write your `docker-compose.yml`:

```bash
mkdir -p /opt/bookarr && cd /opt/bookarr
nano docker-compose.yml   # paste and fill in one of the examples above
```

3. Start Bookarr:

```bash
docker compose up -d
```

4. Access it from your LAN at `http://<server-ip>:8000`.

A Cloudflare Tunnel pointing at `http://bookarr:8000` (same Docker network) or `http://<server-ip>:8000` (from the host) works well for external access.

---

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your local values
uvicorn app.main:app --reload --reload-dir ./app
```

Then open `http://localhost:8000`.

If the reloader loops because the virtual environment is inside the project folder, run without reload:

```bash
uvicorn app.main:app
```

### Build the Docker image locally

```bash
docker compose up -d --build
```

---

## Notes on Listenarr API Shape

Bookarr sends request payloads like:

ASIN books:

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

ISBN books send `"isbn": ["978..."]` (an array, as required by the Listenarr schema).

Search results are normalized from common fields (`asin`, `isbn`, `id`, `title`, `authors`, `imageUrl`). Status values are mapped to `pending_approval`, `sent`, `downloading`, `completed`, `failed`, or `denied`.
