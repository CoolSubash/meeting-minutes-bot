#!/bin/bash
# Deploys the app to App Service by hand.
# Only the files the app needs are uploaded, so .env and .venv stay on this computer.
# Usage: ./scripts/deploy.sh <app-name>
set -euo pipefail

APP_NAME="${1:?Usage: ./scripts/deploy.sh <app-name>}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

cp "$PROJECT_DIR/app.py" "$PROJECT_DIR/requirements.txt" "$PROJECT_DIR/startup.sh" "$STAGE_DIR/"
cp -R "$PROJECT_DIR/minutes_bot" "$STAGE_DIR/minutes_bot"
find "$STAGE_DIR" -name "__pycache__" -prune -exec rm -rf {} +

cd "$STAGE_DIR"
az webapp up --resource-group cs345-speech-rg --name "$APP_NAME" \
  --plan cs345-speech-plan --runtime "PYTHON:3.11" --sku F1
az webapp config set --resource-group cs345-speech-rg --name "$APP_NAME" \
  --startup-file "bash startup.sh" --output none
az webapp restart --resource-group cs345-speech-rg --name "$APP_NAME"
echo "Live at: https://$APP_NAME.azurewebsites.net"
