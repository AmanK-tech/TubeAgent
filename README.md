# TubeAgent

Agentic YouTube video summarizer with a FastAPI backend and a modern React (Vite) web UI. Paste a YouTube link and ask anything — TubeAgent fetches metadata, downloads and chunks media with ffmpeg, transcribes with Google Gemini, synthesizes a grounded global answer, and streams tokens to the UI over WebSockets. For short videos, it can summarize directly from the public URL (no local ASR).

## Stack
- Backend: FastAPI with WebSocket streaming and a thin adapter around the agent pipeline (planner + tools). See `src/app/main.py:1`.
- Frontend: React 18 + Vite + Tailwind. See `web/package.json:1` and `web/src/pages/ChatPage.tsx:1`.
- Agent layer: Deterministic planner + function-calling controller + tools for fetch/extract/transcribe/emit. See `src/agent/core/planner.py:1`, `src/agent/core/controller.py:1`, `src/agent/core/toolkit.py:1`.

## Key Technologies
- FastAPI, Pydantic, WebSocket, yt-dlp, ffmpeg/ffprobe, Google Gemini (genai/generativeai)
- DeepSeek chat completions for planning and tool calling

## Features
- Chat UX with token streaming over WebSocket (`/ws/chat/{session_id}`)
- Paste a YouTube link and ask any follow-up (context is saved per session)
- Automatic strategy selection:
  - Short videos (≤ ~20 min): direct Gemini URL ingestion
  - Longer videos: video-first download → audio normalization → chunking → per-chunk ASR → global synthesis
- Caching and idempotency for manifests, chunks, and transcripts under `runtime/`
- Clean deliverables (md/txt/json) with metadata front matter via `emit_output`
- YouTube cookie support when anonymous access is blocked (no credentials stored)
- Session lifecycle cleanup for artifacts and optional runtime purge

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js 18+
- `ffmpeg` and `ffprobe` available on `PATH`
- API keys: `DEEPSEEK_API_KEY` (planning/tools) and `GOOGLE_API_KEY` (ASR/global summary)

### Backend (API)
1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install fastapi uvicorn[standard] pydantic PyYAML yt-dlp google-genai google-generativeai audioop-lts
   ```
2. Run the API (CORS allows the web dev server by default):
   ```bash
   uvicorn src.app.main:app --reload --port 5050
   ```
   Or run `make api` (uses `PYTHONPATH=./src`, see `Makefile:1`).

### Frontend (Web)
1. Install dependencies and start the dev server:
   ```bash
   cd web
   npm install
   npm run dev
   ```
2. The web app uses `VITE_API_URL` or `http://localhost:5050` by default (`web/src/api/client.ts:1`). Set `VITE_API_URL` before `npm run dev` if the API runs elsewhere.
3. Open `http://localhost:5173` and navigate to `/chat`.

### First Run
- Paste a public YouTube URL and ask, for example, “Give me a concise summary and key takeaways.”
- Watch tokens stream into the UI. The first run downloads media and builds caches under `runtime/`.

## Local Runtime Notes
- TubeAgent ships without cloud/serverless deployment scaffolding; run the backend locally via the Quick Start commands above.
- Runtime artifacts live under `./runtime` by default; override with `RUNTIME_DIR` or `TUBEAGENT_RUNTIME_DIR` if you prefer a different path.
- Export required API keys (e.g., `DEEPSEEK_API_KEY`, `GOOGLE_API_KEY`) in your shell before launching the backend.

## How It Works

### Pipeline
- `fetch_task` → `extract_audio` → `transcribe_asr` → `emit_output`
- The planner decides whether to run tools step-by-step or delegate to function-calling for complex analyses. See `src/agent/core/planner.py:1` and `src/agent/core/controller.py:1`.

### Short Video Fast Path
- For short videos, TubeAgent can call Gemini with the public URL to produce a quick take without local ASR (falls back on error). See `src/agent/core/toolkit.py:1` and `src/agent/tools/transcribe.py:430` (`summarise_gemini`).

### Artifacts and Caching
- Extract manifests and chunks live in `runtime/cache/extract/<job-id>/` with `extract_audio.manifest.json` recording inputs, processing, and outputs. See `src/agent/tools/extract/manifest.py:1`.
- Combined transcripts and per-chunk outputs are saved alongside the manifest; global summaries go to `runtime/summaries/<job-id>/`.
- Final deliverables (md/txt/json) are written by `emit_output` with video metadata in YAML front matter. See `src/agent/tools/emit_output.py:1`.

### Streaming
- The API pushes tokens and lifecycle events over WebSockets: `token`, `message_complete`, `error`. See `src/app/sockets/ws.py:1` and `src/app/sockets/manager.py:1`.

## Backend API

### Health
- `GET /health` → `{ status: "ok" }` (`src/app/api/routes/health.py:1`).

### Sessions
- `POST /sessions` → `{ id, title, created_at }`
- `GET /sessions` → `{ items: Session[] }`
- `GET /sessions/{id}` → `Session`
- `DELETE /sessions/{id}` → `{ ok: true }` (best-effort artifact cleanup)
- `POST /sessions/{id}/close` → `{ ok: true }` (tries to clean up when a tab closes)

### Messages
- `GET /sessions/{id}/messages?cursor=&limit=` → paged history (ephemeral memory store)
- `POST /sessions/{id}/messages` with `{ role: 'user'|'system', content, user_req? }`
  - If `user_req` is present, it drives the end-to-end summary flow (`transcribe_asr` → `summarise_gemini`).
- `WS /ws/chat/{session_id}` → `token`, `message_complete`, `error`

**Notes**
- History is stored in memory and trimmed for context hygiene. Optional on-disk persistence per job ID is available via `PERSIST_CHAT_HISTORY=1`.
- The agent service pseudo-streams by chunking the final text into small parts for WebSockets. See `src/app/services/agent.py:1`.

## Environment Variables

### Core Keys
- `DEEPSEEK_API_KEY` — required for planning/tool-calling with DeepSeek
- `GOOGLE_API_KEY` — required for Gemini uploads and generation

### Web + CORS
- `WEB_ORIGIN` — allowed frontend origin for CORS (default `http://localhost:5173`)
- `VITE_API_URL` — base URL the web client calls (default `http://localhost:5050`)

### Runtime & Persistence
- `RUNTIME_DIR` or `TUBEAGENT_RUNTIME_DIR` — override `./runtime` root used for caches/downloads/summaries
- `PERSIST_CHAT_HISTORY` — if set to `1/true`, persists minimal chat history per job under `runtime/sessions/`

### Session Cleanup
- `SESSION_IDLE_TTL_MINUTES` — minutes before idle sessions are auto-cleaned (default 60)
- `CLEANUP_SWEEP_INTERVAL_SECONDS` — sweep cadence (default 300)
- `CLEANUP_ON_SHUTDOWN` — best-effort per-session cleanup on API shutdown (default on)
- `PURGE_RUNTIME_ON_SHUTDOWN` — remove entire `runtime/` on shutdown (default on)
- `PURGE_RUNTIME_ON_SESSION_DELETE` — purge `runtime/` when deleting a session (default off)

### YouTube Access
- `YT_COOKIES_FILE` — path to exported `cookies.txt` (recommended for local setups requiring authentication)
- `YT_COOKIES_FROM_BROWSER` — browser to read cookies from (`chrome`, `brave`, `edge`, `firefox`, `safari`)
- `YT_COOKIES_BROWSER_PROFILE` — optional browser profile name (e.g., `Default`)

### Gemini Tuning
- `GEMINI_MODEL` (default `gemini-2.5-flash`)
- `GEMINI_FILE_POLL_INTERVAL`
- `GEMINI_FILE_WAIT_TIMEOUT`
- `ASR_AUDIO_ONLY_MINUTES` — prefer audio-only path above this length (default 60)
- `GLOBAL_EXCERPT_CHARS` — excerpt size for map-reduce prompts (default 400)
- `GLOBAL_DIRECT_MINUTES_LIMIT` — threshold for URL-direct quick take (default 20)
- `FAST_FOLLOWUP_TEXT_ONLY` — prefer transcript-only fast path for follow-ups (default on)

### DeepSeek Tuning
- `DEEPSEEK_API_BASE`
- `DEEPSEEK_TIMEOUT`
- `DEEPSEEK_RETRIES`
- `DEEPSEEK_BACKOFF`
- Optional config: `AGENT_PROVIDER`, `AGENT_MODEL`, `AGENT_MAX_TOKENS`, `AGENT_COST_LIMIT`, `AGENT_STEP_LIMIT` (see `src/agent/core/config.py:1`)

## Directory Layout
- API: `src/app/main.py:1`, routes in `src/app/api/routes/`, WebSockets in `src/app/sockets/`, state store in `src/app/state.py:1`
- Agent: planner/controller/toolkit in `src/agent/core/`, tools in `src/agent/tools/`, prompts in `src/agent/prompts/`
- Web: Vite app in `web/` (entry `web/src/main.tsx:1`, routes `web/src/app/routes.tsx:1`)
- Runtime outputs: `runtime/` (downloads, cache/extract, summaries, tmp, outputs)

## YouTube Cookies (Private/Blocked Videos)
- Some videos need an authenticated session (consent/age/region/bot checks). Configure yt-dlp cookies without exposing credentials:
  - **Option A — cookies.txt (recommended on laptops/desktops)**
    - Export on your machine: `yt-dlp --cookies-from-browser chrome --cookies ~/yt-cookies.txt "https://www.youtube.com/watch?v=VIDEO_ID"`
    - Point `YT_COOKIES_FILE` at the exported file.
  - **Option B — read cookies from a local browser**
    - Set `YT_COOKIES_FROM_BROWSER=chrome` and optionally `YT_COOKIES_BROWSER_PROFILE=Default`.
- Cookie contents are never logged; only read from env/secret at runtime.
- Do not commit cookie files; keep them outside version control.

## Limitations
- Memory store only (no database); sessions reset on API restart.
- No authentication or multi-tenant isolation (intended for local/dev use).
- Gemini and DeepSeek usage is subject to their respective quotas and terms.

## License
Licensed under the MIT License. See `LICENSE:1`.

## Acknowledgements
- Uses yt-dlp and ffmpeg for robust media handling.
- Built with FastAPI, React, Tailwind, and modern LLM tooling.
