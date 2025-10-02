# Auto URL‑Direct Plan (≤ 20‑Minute Videos)

Objective
- Automatically use Gemini’s URL ingestion to answer questions for YouTube videos with duration ≤ 20 minutes. No UI toggle, no local download/ASR required. Fall back to the standard pipeline for longer videos or when URL ingestion fails.

Key Rules
- Threshold: 20 minutes (configurable via `URL_DIRECT_MINUTES`, default 20).
- Model: `gemini-2.5-flash` (configurable via `URL_DIRECT_MODEL`).
- Output budget: derived from `contextengineering.allocate_tokens(...)` (same as normal pipeline), optionally scaled by `URL_DIRECT_TOKEN_MULTIPLIER`.
- Timeout: 15 seconds (configurable via `URL_DIRECT_TIMEOUT_S`).
- Fallback: On error/timeout/private video, switch to the existing extract → ASR → summary pipeline.

---

## TODOs (Implementation)

- [ ] Add URL‑direct summarizer
  - File(s): `src/agent/tools/transcribe.py` (or a new `src/agent/tools/url_direct.py`)
  - Function: `summarise_url_direct(state, url: str, user_req: str, model?: str, max_tokens?: int, timeout_s?: float) -> str`
  - Behavior:
    - Build Gemini `contents=[ FileData(file_uri=url), user_req ]` and call `client.models.generate_content`.
    - Apply generation_config using `contextengineering.allocate_tokens(...)` to compute a budget from video duration + query (same as the standard path).
      - Use `to_generation_config(alloc)` and optionally scale via `URL_DIRECT_TOKEN_MULTIPLIER` (e.g., 0.8–1.2).
    - Enforce `URL_DIRECT_TIMEOUT_S`; raise ToolError on failure.
    - Return text and record timings/metadata to `state.artifacts['summarise_url_direct']`.
    - Persist quick take to `<runtime>/summaries/<job-id>/quick_take.txt`.

- [ ] Wire into toolkit
  - File: `src/agent/core/toolkit.py`
  - Add tool spec `summarise_url_direct` with params `{ url, user_req, model? }`.
  - Implement dispatcher branch to call the new function and wrap via `run_tool_json`.

- [ ] Planner routing (auto for ≤ 20 min)
  - File: `src/agent/core/planner.py`
  - After `fetch_task` on a new link, if `duration_s <= URL_DIRECT_MINUTES`, return `{ action: 'tool_call', tool: 'summarise_url_direct', arguments: { url, user_req } }`.
  - Else route to `transcribe_asr` (existing behavior).

- [ ] Controller fallback integrity
  - Files: `src/agent/core/controller.py`, `src/app/services/agent.py`
  - Ensure ToolError from `summarise_url_direct` produces immediate delegation to the standard pipeline and streams status (“Switching to detailed processing…”).

- [ ] Config flags
  - Add env with defaults:
    - `URL_DIRECT_MINUTES=20`
    - `URL_DIRECT_MODEL=gemini-2.5-flash`
    - `URL_DIRECT_TIMEOUT_S=15`
    - `URL_DIRECT_TOKEN_MULTIPLIER=1.0` (optional scale applied to `allocate_tokens` result)

- [ ] Observability
  - Record durations: `url_direct_ms`, `fallback_ms`, `total_ms` under `state.artifacts['timings']`.
  - Record token budget decision from `contextengineering.allocate_tokens` (requested vs clamped) under `state.artifacts['summarise_url_direct']`.
  - Planner log entry: `{ kind: 'routing', data: { url_direct: true, reason: 'duration<=threshold' } }`.

- [ ] UI (optional polish)
  - Show a small “Quick take” note on URL‑direct responses; no changes required otherwise.

---

## Edge Cases & Safeguards
- Private/blocked/regional videos: URL‑direct fails → fallback to standard pipeline (cookies already supported).
- Missing duration: treat as unknown; attempt URL‑direct, fallback on failure.
- Link switch mid‑chat: already handled (planner forces `fetch_task` when a new YT URL appears).

---

## Rollout Steps
1) Implement `summarise_url_direct` + toolkit wiring.
2) Update planner to branch on `duration_s <= URL_DIRECT_MINUTES`.
3) Add timings + quick_take persistence.
4) Validate fallback with simulated failures (timeouts, private videos).
5) Measure P50/P75 time‑to‑first‑answer; tune `URL_DIRECT_TOKEN_MULTIPLIER` and timeout.

---

## Acceptance Criteria
- For videos ≤ 20 minutes:
  - P75 time‑to‑first‑answer ≤ 6–8 seconds using URL ingestion.
  - Automatic fallback works with clear status when URL‑direct is unavailable.
- For videos > 20 minutes: unchanged behavior/performance.
