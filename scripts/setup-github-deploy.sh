#!/bin/bash
# Lets GitHub Actions deploy with OpenID Connect, so no Azure password is stored.
# Usage: ./scripts/setup-github-deploy.sh <app-name> <github-owner/repo>
set -euo pipefail

APP_NAME="${1:?Usage: ./scripts/setup-github-deploy.sh <app-name> <github-owner/repo>}"
REPO="${2:?Usage: ./scripts/setup-github-deploy.sh <app-name> <github-owner/repo>}"
RG="cs345-speech-rg"

SUBSCRIPTION_ID=$(az account show --query id --output tsv)
TENANT_ID=$(az account show --query tenantId --output tsv)
CLIENT_ID=$(az ad app create --display-name "${APP_NAME}-github" --query appId --output tsv)
az ad sp create --id "$CLIENT_ID" --output none

az ad app federated-credential create --id "$CLIENT_ID" --parameters "{
  \"name\": \"github-main\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:${REPO}:ref:refs/heads/main\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}" --output none

WEBAPP_ID=$(az webapp show --resource-group "$RG" --name "$APP_NAME" --query id --output tsv)
az role assignment create --assignee "$CLIENT_ID" --role "Website Contributor" \
  --scope "$WEBAPP_ID" --output none

gh secret set AZURE_CLIENT_ID --repo "$REPO" --body "$CLIENT_ID"
gh secret set AZURE_TENANT_ID --repo "$REPO" --body "$TENANT_ID"
gh secret set AZURE_SUBSCRIPTION_ID --repo "$REPO" --body "$SUBSCRIPTION_ID"
gh variable set AZURE_WEBAPP_NAME --repo "$REPO" --body "$APP_NAME"
echo "GitHub Actions can now deploy $APP_NAME."
