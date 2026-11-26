# Meeting Minutes Bot — System Design

## 1. Goal and scope

Turn a short meeting recording (≤ 3 minutes, ≤ 25 MB) into structured minutes:
decisions, action items with owner and due date, key topics, sentiment, a spoken recap,
and Markdown / calendar (.ics) exports. Each signed-in user sees only their own meetings,
which are kept for 30 days.

**Non-goals:** long recordings, speaker diarization, real-time streaming, multi-tenant orgs.

## 2. Architecture

```mermaid
flowchart LR
    U[Browser<br/>mic or upload] -->|HTTPS multipart| W

    subgraph AppService["Azure App Service F1 (Linux, Python 3.11)"]
        W[gunicorn → Flask<br/>minutes_bot.app]
        F[ffmpeg<br/>imageio-ffmpeg binary]
        X[minutes.py<br/>rule-based extraction]
        W --> F
        W --> X
    end

    W -->|continuous recognition| S[Azure AI Speech F0<br/>STT + Neural TTS]
    W -->|key phrases, entities, sentiment| L[Azure AI Language F0]
    W -->|meetings 30-day TTL, users| C[(Azure Cosmos DB<br/>free tier)]
    W -.->|OpenTelemetry traces + metrics| AI[Application Insights<br/>→ Log Analytics]

    GH[GitHub Actions] -->|pytest, then OIDC deploy| AppService
```

## 3. Main flow — `POST /process` sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Browser (static/index.html)
    participant API as Flask /process
    participant FF as ffmpeg
    participant STT as Azure Speech (STT)
    participant LANG as Azure AI Language
    participant EXT as minutes.py
    participant DB as Cosmos DB
    participant TTS as Azure Speech (TTS)
    participant AI as App Insights

    User->>UI: Record or choose file, title, date, voice
    UI->>API: POST /process (multipart: audio, title, meeting_date, voice)
    API->>API: validate extension, size ≤ 25 MB, date, voice
    Note over API,AI: span pipeline.process (child of the HTTP request span)

    rect rgb(235, 245, 255)
    Note over API,STT: span stage.speech_to_text
    API->>FF: convert to 16 kHz mono WAV (reject > 3 min)
    FF-->>API: audio.wav
    API->>STT: continuous recognition
    STT-->>API: transcript, confidence, language, duration
    end

    rect rgb(235, 255, 240)
    Note over API,LANG: span stage.language_analysis
    API->>LANG: key phrases + entities + sentiment (chunked)
    LANG-->>API: analysis
    end

    rect rgb(255, 248, 230)
    Note over API,EXT: span stage.minutes_extraction
    API->>EXT: extract_minutes(transcript, entities, meeting_date)
    EXT-->>API: decisions, action_items (owner, due_date, needs_review)
    end

    rect rgb(245, 240, 255)
    Note over API,DB: span stage.meeting_storage
    API->>DB: upsert meeting {owner_id, ...} (TTL 30 days)
    DB-->>API: ok
    end

    rect rgb(255, 238, 238)
    Note over API,TTS: span stage.text_to_speech
    API->>TTS: synthesize summary_text with chosen neural voice
    TTS-->>API: audio (base64)
    end

    API-)AI: metrics: stage_*_ms, stt_confidence, action_items_per_meeting
    API-->>UI: 200 JSON (minutes + summary_audio)
    UI-->>User: decisions, action items, recap player, export buttons
```

**Error path:** any stage raises `PipelineError(message, status)`. The `stage()` context manager
tags it with the stage key, the request span gets `event.name=pipeline_error` and `error.stage`,
and the client receives `{"error": "..."}` with the right status code (400/413/415/422/502/504).

## 4. Auth flow

```mermaid
sequenceDiagram
    actor User
    participant UI as Browser
    participant API as Flask /auth
    participant DB as Cosmos users container

    User->>UI: sign up (name, email, password ≥ 8)
    UI->>API: POST /auth/signup
    API->>API: id = sha256(email), hash password (werkzeug)
    API->>DB: create_item(user) — fails if it exists
    API-->>UI: Set-Cookie session (signed with FLASK_SECRET_KEY, 7 days, Secure on App Service)
    UI->>API: GET /meetings (cookie)
    API->>DB: SELECT ... WHERE c.owner_id = @owner_id
    API-->>UI: only this user's meetings
```

## 5. Data model (Cosmos DB, database `minutes`)

| Container | Partition key | TTL | Throughput | Document |
| --- | --- | --- | --- | --- |
| `meetings` | `/id` | 30 days | 400 RU/s | `id, meeting_id, owner_id?, title, meeting_date, transcript, language, duration_seconds, confidence, decisions[], action_items[{id, task, owner, due_date, needs_review}], key_phrases[], entities[], sentiment{}, summary_text` |
| `users` | `/id` | none | 400 RU/s | `id = sha256(email), name, email, password_hash, created_at` |

Total 800 RU/s, inside the 1 000 RU/s free-tier allowance. Without `COSMOS_ENDPOINT`, `MemoryStore` is used (local dev, tests).

## 6. API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/process` | full pipeline |
| POST | `/transcribe` | STT only |
| POST | `/analyze` | Language only (`{"text": ...}`) |
| GET | `/meetings`, `/meetings/{id}` | history, one meeting |
| GET | `/meetings/{id}/export?format=md\|ics` | exports |
| DELETE | `/meetings/{id}` | delete (owner only) |
| POST | `/auth/signup`, `/auth/signin`, `/auth/signout`; GET `/auth/me` | accounts |
| GET | `/telemetry-summary` | in-process session stats |

## 7. Observability

```mermaid
flowchart TB
    R["HTTP request span: POST /process"] --> P[pipeline.process]
    P --> S1[stage.speech_to_text]
    P --> S2[stage.language_analysis]
    P --> S3[stage.minutes_extraction]
    P --> S4[stage.meeting_storage]
    P --> S5[stage.text_to_speech]
```

- **Traces:** Application Insights → Transaction search → a `POST /process` request → end-to-end view shows the stage spans (screenshot for Part G).
- **Domain metric:** `action_items_per_meeting` (histogram). Evidence query:

  ```kusto
  customMetrics
  | where name == "action_items_per_meeting"
  | summarize meetings = sum(valueCount), avg_items = sum(valueSum) / sum(valueCount) by bin(timestamp, 1h)
  ```
- **Other metrics:** `stt_confidence`, `stage_stt_ms`, `stage_language_ms`, `stage_extraction_ms`, `stage_storage_ms`, `stage_tts_ms`, entity/keyphrase counts.
- **Alert (G7):** `low-stt-confidence` — log alert on `stt_confidence` average below a threshold (not created yet).

## 8. Deployment and CI/CD

```mermaid
sequenceDiagram
    actor Dev
    participant GH as GitHub
    participant GA as GitHub Actions
    participant Entra as Microsoft Entra (OIDC)
    participant AS as App Service

    Dev->>GH: git push main
    GH->>GA: workflow "Test and deploy"
    GA->>GA: pip install, pytest -q
    alt tests fail
        GA-->>Dev: red run, no deploy
    else tests pass and vars.AZURE_WEBAPP_NAME set
        GA->>Entra: federated token (repo:...:ref:refs/heads/main)
        Entra-->>GA: Azure access token
        GA->>AS: azure/webapps-deploy (zip, Oryx build)
        AS->>AS: bash startup.sh → gunicorn app:app
    end
```

## 9. Key decisions and trade-offs

| Decision | Why | Trade-off |
| --- | --- | --- |
| Rule-based extraction (`minutes.py`) instead of an LLM | free, deterministic, testable with 5 fixture scripts | misses unusual phrasing; flags `needs_review` |
| F0 / F1 / Cosmos free tier everywhere | ~$0 credit spend | F1 has 60 CPU-min/day and cold starts; one slow worker |
| `imageio-ffmpeg` binary instead of `apt-get ffmpeg` at boot | apt-get on every start used up the F1 CPU quota | larger package |
| Regions `northcentralus` (AI) / `westus` (Cosmos, App Service) | Student subscription blocks `eastus`; capacity limits | cross-region latency (small) |
| Histogram for the domain metric | gauges keep only the last value per export | need `valueSum/valueCount` in KQL |
| `owner_id` filter, meetings without owner kept separate | per-user privacy for the UI | API callers without a session share an anonymous space |
