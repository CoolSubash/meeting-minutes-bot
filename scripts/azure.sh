#!/bin/bash
# Checks what the project's Azure resources cost and turns the web app off and on.
# Usage:
#   ./scripts/azure.sh status   # resources, tiers, free-tier limits, site state
#   ./scripts/azure.sh pause    # stop the web app when you are not building
#   ./scripts/azure.sh resume   # start it again before a demo or grading
# Azure for Students does not report dollar amounts through the API.
# See the credit balance at https://www.microsoftazuresponsorships.com/Balance
set -euo pipefail

RG="cs345-speech-rg"
APP_NAME="${AZURE_WEBAPP_NAME:-cs345-minutes-bot-4f7a}"
COSMOS="${APP_NAME}-cosmos"
WORKSPACE="cs345-logs"

status() {
  echo "== Subscription"
  az account show --query "{name:name, state:state}" --output table

  echo; echo "== Resources in $RG (anything that is not F0/F1/free tier can cost money)"
  az resource list --resource-group "$RG" \
    --query "[?type!='microsoft.alertsmanagement/smartDetectorAlertRules'].{name:name, type:type, sku:sku.name, region:location}" \
    --output table

  echo; echo "== Web app"
  az webapp show --resource-group "$RG" --name "$APP_NAME" \
    --query "{state:state, url:defaultHostName, httpsOnly:httpsOnly}" --output table

  echo; echo "== Cosmos DB (free tier covers 1000 RU/s and 25 GB in total)"
  az cosmosdb show --resource-group "$RG" --name "$COSMOS" --query "{freeTier:enableFreeTier}" --output table
  total=0
  for database in $(az cosmosdb sql database list --resource-group "$RG" --account-name "$COSMOS" --query "[].name" --output tsv); do
    for container in $(az cosmosdb sql container list --resource-group "$RG" --account-name "$COSMOS" \
      --database-name "$database" --query "[].name" --output tsv); do
      ru=$(az cosmosdb sql container throughput show --resource-group "$RG" --account-name "$COSMOS" \
        --database-name "$database" --name "$container" --query resource.throughput --output tsv)
      echo "  $database/$container: $ru RU/s"
      total=$((total + ru))
    done
  done
  if [ "$total" -gt 1000 ]; then
    echo "  Total $total RU/s is OVER the free 1000 RU/s. The extra is billed."
  else
    echo "  Total $total RU/s is inside the free 1000 RU/s."
  fi

  echo; echo "== Log Analytics (first 5 GB a month free)"
  az monitor log-analytics workspace show --resource-group "$RG" --workspace-name "$WORKSPACE" \
    --query "{dailyCapGB:workspaceCapping.dailyQuotaGb, retentionDays:retentionInDays}" --output table
  echo "  A daily cap of -1 means no cap. Set one with:"
  echo "  az monitor log-analytics workspace update -g $RG -n $WORKSPACE --quota 0.1"

  echo; echo "== Meters used this month (all should say Free or F1)"
  az consumption usage list --start-date "$(date +%Y-%m-01)" --end-date "$(date +%Y-%m-%d)" \
    --query "[].product" --output tsv 2>/dev/null | sort | uniq -c || echo "  Usage data is not available yet."

  echo; echo "Credit balance: https://www.microsoftazuresponsorships.com/Balance"
}

case "${1:-status}" in
  status) status ;;
  pause)
    az webapp stop --resource-group "$RG" --name "$APP_NAME"
    echo "Stopped https://$APP_NAME.azurewebsites.net. Run './scripts/azure.sh resume' before a demo."
    ;;
  resume)
    az webapp start --resource-group "$RG" --name "$APP_NAME"
    echo "Started https://$APP_NAME.azurewebsites.net (the first request can take a minute)."
    ;;
  *) echo "Usage: $0 status|pause|resume" >&2; exit 1 ;;
esac
