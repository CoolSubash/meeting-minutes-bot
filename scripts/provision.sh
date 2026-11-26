#!/bin/bash
# Creates every Azure resource, writes .env, and configures the web app.
# Usage: ./scripts/provision.sh <globally-unique-app-name>
# Keys go straight into .env and App Settings and are never printed.
set -euo pipefail

APP_NAME="${1:?Usage: ./scripts/provision.sh <globally-unique-app-name>}"
RG="cs345-speech-rg"
# This Azure for Students subscription blocks eastus, so the default is an allowed region.
LOCATION="${AZURE_LOCATION:-northcentralus}"
SPEECH="cs345-speech"
LANGUAGE="cs345-language"
WORKSPACE="cs345-logs"
INSIGHTS="cs345-insights"
PLAN="cs345-speech-plan"
COSMOS="${APP_NAME}-cosmos"

for provider in Microsoft.CognitiveServices Microsoft.DocumentDB Microsoft.Insights \
  Microsoft.OperationalInsights Microsoft.Web; do
  az provider register --namespace "$provider" --wait --output none
done
az extension add --name application-insights --upgrade --only-show-errors

az group create --name "$RG" --location "$LOCATION" --output none

echo "Creating Speech and Language (F0)..."
az cognitiveservices account show --name "$SPEECH" --resource-group "$RG" --output none 2>/dev/null ||
  az cognitiveservices account create --name "$SPEECH" --resource-group "$RG" \
    --kind SpeechServices --sku F0 --location "$LOCATION" --yes --output none
az cognitiveservices account show --name "$LANGUAGE" --resource-group "$RG" --output none 2>/dev/null ||
  az cognitiveservices account create --name "$LANGUAGE" --resource-group "$RG" \
    --kind TextAnalytics --sku F0 --location "$LOCATION" --custom-domain "${APP_NAME}-language" \
    --yes --output none

echo "Creating Log Analytics and Application Insights..."
az monitor log-analytics workspace create --resource-group "$RG" \
  --workspace-name "$WORKSPACE" --location "$LOCATION" --output none
# Cap log ingestion at 0.1 GB a day, well under the 5 GB a month free allowance.
az monitor log-analytics workspace update --resource-group "$RG" \
  --workspace-name "$WORKSPACE" --quota 0.1 --output none
WORKSPACE_ID=$(az monitor log-analytics workspace show --resource-group "$RG" \
  --workspace-name "$WORKSPACE" --query id --output tsv)
az monitor app-insights component create --app "$INSIGHTS" --location "$LOCATION" \
  --resource-group "$RG" --workspace "$WORKSPACE_ID" --output none

echo "Creating Cosmos DB (free tier). This can take several minutes..."
cosmos_state() {
  az cosmosdb show --name "$COSMOS" --resource-group "$RG" --query provisioningState --output tsv 2>/dev/null || true
}
# Cosmos DB regions can run out of capacity, so try each allowed region in turn.
if [ "$(cosmos_state)" != "Succeeded" ]; then
  for region in ${COSMOS_REGIONS:-canadacentral westus francecentral norwayeast}; do
    if [ -n "$(cosmos_state)" ]; then
      az cosmosdb delete --name "$COSMOS" --resource-group "$RG" --yes --output none
    fi
    echo "Trying Cosmos DB in $region..."
    if az cosmosdb create --name "$COSMOS" --resource-group "$RG" --enable-free-tier true \
      --default-consistency-level Session --locations regionName="$region" --output none; then
      echo "Cosmos DB created in $region."
      break
    fi
  done
fi
if [ "$(cosmos_state)" != "Succeeded" ]; then
  echo "Cosmos DB could not be created in any allowed region." >&2
  exit 1
fi

SPEECH_KEY=$(az cognitiveservices account keys list --name "$SPEECH" --resource-group "$RG" --query key1 --output tsv)
LANGUAGE_KEY=$(az cognitiveservices account keys list --name "$LANGUAGE" --resource-group "$RG" --query key1 --output tsv)
LANGUAGE_ENDPOINT=$(az cognitiveservices account show --name "$LANGUAGE" --resource-group "$RG" --query properties.endpoint --output tsv)
INSIGHTS_CONNECTION=$(az monitor app-insights component show --app "$INSIGHTS" --resource-group "$RG" --query connectionString --output tsv)
COSMOS_ENDPOINT=$(az cosmosdb show --name "$COSMOS" --resource-group "$RG" --query documentEndpoint --output tsv)
COSMOS_KEY=$(az cosmosdb keys list --name "$COSMOS" --resource-group "$RG" --query primaryMasterKey --output tsv)
# Reuse the existing key so running this script again does not sign everyone out.
FLASK_SECRET_KEY=$(grep -s '^FLASK_SECRET_KEY=' .env | cut -d= -f2- || true)
FLASK_SECRET_KEY=${FLASK_SECRET_KEY:-$(openssl rand -hex 32)}

umask 077
cat > .env <<EOF
AZURE_SPEECH_KEY=$SPEECH_KEY
AZURE_SPEECH_REGION=$LOCATION
AZURE_LANGUAGE_KEY=$LANGUAGE_KEY
AZURE_LANGUAGE_ENDPOINT=$LANGUAGE_ENDPOINT
APPLICATIONINSIGHTS_CONNECTION_STRING=$INSIGHTS_CONNECTION
COSMOS_ENDPOINT=$COSMOS_ENDPOINT
COSMOS_KEY=$COSMOS_KEY
FLASK_SECRET_KEY=$FLASK_SECRET_KEY
EOF
echo "Local settings saved to .env."

echo "Creating App Service (F1)..."
# Free App Service quota differs by region, so try each allowed region in turn.
if ! az appservice plan show --name "$PLAN" --resource-group "$RG" --query id --output tsv 2>/dev/null | grep -q .; then
  for region in ${APP_REGIONS:-westus canadacentral francecentral norwayeast northcentralus}; do
    echo "Trying App Service in $region..."
    if az appservice plan create --name "$PLAN" --resource-group "$RG" --location "$region" \
      --sku F1 --is-linux --output none; then
      echo "App Service plan created in $region."
      break
    fi
  done
fi
if ! az appservice plan show --name "$PLAN" --resource-group "$RG" --query id --output tsv 2>/dev/null | grep -q .; then
  echo "No allowed region has free App Service quota. Local development can still continue." >&2
  exit 2
fi
az webapp show --resource-group "$RG" --name "$APP_NAME" --output none 2>/dev/null ||
  az webapp create --resource-group "$RG" --plan "$PLAN" --name "$APP_NAME" \
    --runtime "PYTHON:3.11" --output none

az webapp config appsettings set --resource-group "$RG" --name "$APP_NAME" --output none --settings \
  AZURE_SPEECH_KEY="$SPEECH_KEY" \
  AZURE_SPEECH_REGION="$LOCATION" \
  AZURE_LANGUAGE_KEY="$LANGUAGE_KEY" \
  AZURE_LANGUAGE_ENDPOINT="$LANGUAGE_ENDPOINT" \
  APPLICATIONINSIGHTS_CONNECTION_STRING="$INSIGHTS_CONNECTION" \
  COSMOS_ENDPOINT="$COSMOS_ENDPOINT" \
  COSMOS_KEY="$COSMOS_KEY" \
  FLASK_SECRET_KEY="$FLASK_SECRET_KEY" \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true
az webapp config set --resource-group "$RG" --name "$APP_NAME" --startup-file "bash startup.sh" --output none
# The site is reachable only over HTTPS.
az webapp update --resource-group "$RG" --name "$APP_NAME" --https-only true --output none

echo "Done. Deploy with: ./scripts/deploy.sh $APP_NAME"
