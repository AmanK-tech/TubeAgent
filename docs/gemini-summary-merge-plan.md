# Merge Plan: Gemini-Only Summarization Integrated Into `transcribe.py`

Goal: merge `summarise_chunks.py` and `summarise_global.py` into `src/agent/tools/transcribe.py`, so transcription and summarization run via Gemini with minimal API calls. Keep planner/tool compatibility and existing artifacts.

## Goals
- Use Gemini for both per‑chunk and global summaries; remove DeepSeek dependency for summaries.
- Reuse uploads and per‑chunk work to avoid extra API calls.
- Preserve artifacts/state used downstream and by local check scripts.

## High-Level Approach
- Keep `transcribe_task(...)` as the single ASR entry point.
- During each chunk call, ask Gemini for: transcript + a concise “Summary:” in one response.
- Store per‑chunk summaries on disk and in artifacts.
- Add `summarise_gemini(...)` (in this file) that:
  - For short videos (≤ `GLOBAL_DIRECT_MINUTES_LIMIT`, default 20m): direct multimodal global summary using Gemini file handles (no re‑upload).
  - For longer videos: map‑reduce using already generated per‑chunk summaries + one final global call.

## TODOs

### Transcribe Module Enhancements (src/agent/tools/transcribe.py)
- [ ] Add prompt loader helper for `chunk_prompt.txt` and `global_prompt.txt`.
- [ ] Add `_fmt_ts(seconds)` to format timestamps.
- [ ] Capture Gemini file handle per chunk (e.g., `gemini_file_name`) and include in `state.artifacts["transcribe_asr"]["chunks"]`.
- [ ] Extend per‑chunk prompt to request transcript + short summary in the same response.
- [ ] Parse and save `chunk_{idx:04d}.summary.txt` alongside `.gemini.txt/.json`.
- [ ] Record `summary_path` and summary char counts in artifacts.

### Global Summarization (implemented inside transcribe.py)
- [ ] Implement `summarise_gemini(state, user_req, intent=None, include_metadata=False)`:
  - [ ] Strategy selection: direct multimodal (≤ limit) vs map‑reduce (> limit).
  - [ ] Direct multimodal: use stored Gemini file handles (no re‑upload), `global_prompt.txt` + user request (+ optional metadata block).
  - [ ] Map‑reduce: compose prompt from per‑chunk summaries and short raw excerpts; make a single global Gemini call.
  - [ ] Persist `global_summary.gemini.txt` in same output directory and capture stats under `state.artifacts["summarise_global"]`.
  - [ ] Respect env/model knobs: `GEMINI_MODEL`, `GLOBAL_DIRECT_MINUTES_LIMIT`, `GEMINI_FILE_WAIT_TIMEOUT`.

### Backward Compatibility
- [ ] Update `src/agent/tools/summarise_global.py` to be a thin wrapper around `transcribe.summarise_gemini` (same signature).
- [ ] Update `src/agent/tools/summarise_chunks.py` to prefer reading `summary_path` from `state.artifacts["transcribe_asr"]["chunks"]`; only if absent, call a small Gemini helper in `transcribe.py` (fallback).

### Planner + Toolkit
- [ ] Keep planner step 4 as `summarise_global` (now Gemini‑only wrapper) to minimize change.
- [ ] Optionally add args to `transcribe_asr` (`user_req`, `intent`, `include_metadata`, `summarise_mode=auto`) to auto‑run `summarise_gemini` after ASR so planner can skip a separate step.
- [ ] Update `src/agent/core/toolkit.py` tool descriptions to reflect Gemini‑only summarization and reuse of chunk summaries.
- [ ] Update `src/agent/prompts/planner_prompt.txt` to remove DeepSeek mention for summaries.

### Config and Env
- [ ] Ensure `GOOGLE_API_KEY` is the only required key for both ASR and summaries.
- [ ] Reuse `GEMINI_MODEL`, `GEMINI_FILE_WAIT_TIMEOUT`, and add/read `GLOBAL_DIRECT_MINUTES_LIMIT`.

### Artifacts & File Outputs
- [ ] Per‑chunk: keep `chunk_XXXX.gemini.txt/.json`; add `chunk_XXXX.summary.txt` and `summary_path` in artifacts.
- [ ] Global: write `global_summary.gemini.txt` and add stats under `state.artifacts["summarise_global"]`.
- [ ] Include per‑chunk `gemini_file_name` (or equivalent) to enable direct multimodal summary without re‑upload.

### Docs + Scripts
- [ ] Update `docs/gemini-transcription-plan.md` to note Gemini‑only summaries and strategies.
- [ ] Update `README.md` summarization notes (Gemini‑only; integrated path).
- [ ] Adjust `local_check_transcribe.py` to optionally emit/show the global summary.
- [ ] Deprecate or adapt `local_check_summarise_*.py` to use the unified Gemini path.

### Validation
- [ ] Short video (≤20m): one direct global call post‑ASR; verify no re‑uploads; outputs include `global_summary.gemini.txt`.
- [ ] Long video (>20m): no extra per‑chunk calls (summaries already created during ASR); exactly one global call.
- [ ] Verify artifact paths and stats in `state.artifacts["summarise_global"]` and per‑chunk `summary_path`.
- [ ] Confirm planner flow end‑to‑end remains functional.

## File References
- `src/agent/tools/transcribe.py`
- `src/agent/tools/summarise_global.py`
- `src/agent/tools/summarise_chunks.py`
- `src/agent/core/toolkit.py`
- `src/agent/prompts/global_prompt.txt`
- `src/agent/prompts/chunk_prompt.txt`
- `docs/gemini-transcription-plan.md`

## Open Questions
- Remove DeepSeek usage entirely now, or keep wrappers for transition?
- For ≤20m videos, do we optionally skip per‑chunk transcripts and only produce a direct global summary to reduce API calls, or should transcripts always be persisted?

