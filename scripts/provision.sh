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
az cognitiveservices account create --name "$SPEECH" --resource-group "$RG" \
  --kind SpeechServices --sku F0 --location "$LOCATION" --yes --output none
az cognitiveservices account create --name "$LANGUAGE" --resource-group "$RG" \
  --kind TextAnalytics --sku F0 --location "$LOCATION" --custom-domain "${APP_NAME}-language" \
  --yes --output none

echo "Creating Log Analytics and Application Insights..."
az monitor log-analytics workspace create --resource-group "$RG" \
  --workspace-name "$WORKSPACE" --location "$LOCATION" --output none
WORKSPACE_ID=$(az monitor log-analytics workspace show --resource-group "$RG" \
  --workspace-name "$WORKSPACE" --query id --output tsv)
az monitor app-insights component create --app "$INSIGHTS" --location "$LOCATION" \
  --resource-group "$RG" --workspace "$WORKSPACE_ID" --output none

echo "Creating Cosmos DB (free tier). This can take several minutes..."
az cosmosdb create --name "$COSMOS" --resource-group "$RG" --enable-free-tier true \
  --default-consistency-level Session --locations regionName="$LOCATION" --output none

echo "Creating App Service (F1)..."
az appservice plan create --name "$PLAN" --resource-group "$RG" --sku F1 --is-linux --output none
az webapp create --resource-group "$RG" --plan "$PLAN" --name "$APP_NAME" \
  --runtime "PYTHON:3.11" --output none

SPEECH_KEY=$(az cognitiveservices account keys list --name "$SPEECH" --resource-group "$RG" --query key1 --output tsv)
LANGUAGE_KEY=$(az cognitiveservices account keys list --name "$LANGUAGE" --resource-group "$RG" --query key1 --output tsv)
LANGUAGE_ENDPOINT=$(az cognitiveservices account show --name "$LANGUAGE" --resource-group "$RG" --query properties.endpoint --output tsv)
INSIGHTS_CONNECTION=$(az monitor app-insights component show --app "$INSIGHTS" --resource-group "$RG" --query connectionString --output tsv)
COSMOS_ENDPOINT=$(az cosmosdb show --name "$COSMOS" --resource-group "$RG" --query documentEndpoint --output tsv)
COSMOS_KEY=$(az cosmosdb keys list --name "$COSMOS" --resource-group "$RG" --query primaryMasterKey --output tsv)

umask 077
cat > .env <<EOF
AZURE_SPEECH_KEY=$SPEECH_KEY
AZURE_SPEECH_REGION=$LOCATION
AZURE_LANGUAGE_KEY=$LANGUAGE_KEY
AZURE_LANGUAGE_ENDPOINT=$LANGUAGE_ENDPOINT
APPLICATIONINSIGHTS_CONNECTION_STRING=$INSIGHTS_CONNECTION
COSMOS_ENDPOINT=$COSMOS_ENDPOINT
COSMOS_KEY=$COSMOS_KEY
EOF

az webapp config appsettings set --resource-group "$RG" --name "$APP_NAME" --output none --settings \
  AZURE_SPEECH_KEY="$SPEECH_KEY" \
  AZURE_SPEECH_REGION="$LOCATION" \
  AZURE_LANGUAGE_KEY="$LANGUAGE_KEY" \
  AZURE_LANGUAGE_ENDPOINT="$LANGUAGE_ENDPOINT" \
  APPLICATIONINSIGHTS_CONNECTION_STRING="$INSIGHTS_CONNECTION" \
  COSMOS_ENDPOINT="$COSMOS_ENDPOINT" \
  COSMOS_KEY="$COSMOS_KEY" \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true
az webapp config set --resource-group "$RG" --name "$APP_NAME" --startup-file "startup.sh" --output none

echo "Done. Deploy with: ./scripts/deploy.sh $APP_NAME"
