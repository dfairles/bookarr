#!/bin/bash

export BOOKARR_SECRET_KEY="dev-secret"
export BOOKARR_REQUESTER_PASSWORD="requester"
export BOOKARR_ADMIN_PASSWORD="admin"
export BOOKARR_DATABASE_URL="sqlite:///./bookarr-dev.db"

export LISTENARR_URL="http://192.168.1.20:4545"
export LISTENARR_TOKEN=""
export LISTENARR_AUTH_MODE="x-api-key"
export LISTENARR_API_KEY_NAME="apikey"

export LISTENARR_SEARCH_PATH="/api/v1/search/intelligent"
export LISTENARR_SEARCH_QUERY_PARAM="query"
export LISTENARR_SEARCH_REGION="us"
export LISTENARR_REQUEST_PATH="/api/v1/library/add"
export LISTENARR_STATUS_PATH="/api/v1/library/{listenarr_id}"

export BOOKARR_STATUS_POLL_SECONDS="300"

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
