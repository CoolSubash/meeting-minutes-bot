# Meeting Minutes Bot

Meeting Minutes Bot turns a meeting recording of up to 3 minutes into decisions and action items with owners and due dates. Teams can review the results, hear a spoken recap, and export Markdown or calendar (.ics) files.

## Architecture

```text
Browser mic or upload
  -> ffmpeg (16 kHz mono WAV)
  -> Azure AI Speech (continuous recognition)
  -> Azure AI Language (key phrases, entities, sentiment)
  -> Minutes extraction (decisions, tasks, owners, due dates)
  -> Azure Cosmos DB (30-day meeting history)
  -> Azure Neural TTS (spoken recap)
  -> Results page, Markdown export, .ics export

Application Insights records traces and metrics for every stage.
```

## Part I extensions

- **Track 1: Action item tracker with calendar export.** The app extracts owners and due dates, flags incomplete tasks, saves history in Cosmos DB, and exports Markdown and .ics files.
- **Track 2: CI/CD.** GitHub Actions runs the tests on every push and deploys `main` to App Service using OpenID Connect.

## Run locally

You need Python 3.11 or newer, ffmpeg, and the Azure CLI.

```bash
brew install ffmpeg azure-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Create the Azure resources. This also writes your `.env` file:

```bash
az login
./scripts/provision.sh <globally-unique-app-name>
PORT=5001 python app.py
```

Open http://localhost:5001. Port 5001 avoids the macOS AirPlay service, which often uses port 5000.

The resources use `northcentralus` because this Azure for Students subscription does not allow `eastus`. Set `AZURE_LOCATION` before running the script to use another allowed region.

## Deploy

```bash
./scripts/deploy.sh <globally-unique-app-name>
```

To turn on automatic deployment after the repository is on GitHub:

```bash
./scripts/setup-github-deploy.sh <globally-unique-app-name> <github-owner/repo>
```

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /process` | Run the full pipeline. Form fields: `audio`, `title`, `meeting_date`, `voice` |
| `POST /transcribe` | Transcribe an `audio` upload |
| `POST /analyze` | Analyze JSON `{ "text": "..." }` |
| `GET /meetings` | List saved meetings |
| `GET /meetings/{id}` | Get saved minutes |
| `GET /meetings/{id}/export?format=md` | Download Markdown |
| `GET /meetings/{id}/export?format=ics` | Download calendar events |
| `DELETE /meetings/{id}` | Delete a meeting |
| `GET /telemetry-summary` | View session health |

```bash
curl -X POST http://localhost:5001/process \
  -F "audio=@recording.wav" -F "title=Sprint planning" -F "meeting_date=2026-11-18"
```

## Observability

Each `/process` call creates these spans: `stage.speech_to_text`, `stage.language_analysis`, `stage.minutes_extraction`, `stage.meeting_storage`, and `stage.text_to_speech`.

The domain metric is `action_items_per_meeting`. Because it is a histogram, use `valueSum / valueCount` in KQL:

```kusto
customMetrics
| where timestamp > ago(24h)
| where name == "action_items_per_meeting"
| summarize meetings = sum(valueCount), avg_action_items = sum(valueSum) / sum(valueCount)
```

Per-stage latency and confidence:

```kusto
customMetrics
| where timestamp > ago(1h)
| where name in ("stt_confidence", "stage_stt_ms", "stage_language_ms", "stage_extraction_ms", "stage_storage_ms", "stage_tts_ms")
| summarize avg_value = sum(valueSum) / sum(valueCount), min_value = min(valueMin), max_value = max(valueMax), samples = sum(valueCount) by name
| order by name asc
```

Create the `low-stt-confidence` alert in the Azure Portal using the Part G7 steps.

## Cleanup

After grading:

```bash
az group delete --name cs345-speech-rg --yes --no-wait
```
