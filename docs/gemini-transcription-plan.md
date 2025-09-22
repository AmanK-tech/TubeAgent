# Switch ASR to Gemini 2.5 Flash Lite — Plan & TODOs

Goal: Use Google Gemini (`gemini-2.5-flash-lite`) for transcription while preserving the existing core logic: manifest discovery, chunk-wise processing, concurrency, retries/backoff, per‑chunk artifacts, and combined transcript — and move to a video‑first pipeline (always process video, not audio).

Update: Summarization is now integrated with Gemini inside `transcribe.py` (per‑chunk summaries during ASR and a global summary via `summarise_gemini`).

## Outcomes
- Keep the public tool API (`transcribe_asr`) and overall flow unchanged for callers.
- Use Gemini via `google.genai` for chunk transcription.
- Maintain chunk concurrency, retry/backoff behavior, and artifact shapes (with provider‑appropriate filenames).
- Update prompts/tooling/docs for Gemini and video-first.

## Prereqs
- Python package: `pip install google-genai`
- Credentials: set `GOOGLE_API_KEY` in the environment.
- Default model: `gemini-2.5-flash-lite` (override via `GEMINI_MODEL`).
- Video first: Gemini accepts common containers like `.mp4` (H.264/AAC). We will always upload video chunks (preferred `.mp4`), not audio. Audio is only a fallback if video is unavailable.
 - Concurrency defaults: set `GEMINI_CONCURRENCY=2` by default for video processing (tune up/down as needed).

Minimal API usage (for reference):

```python
from google import genai
client = genai.Client()  # uses GOOGLE_API_KEY
myfile = client.files.upload(file="path/to/sample.mp4")  # always send video chunks
resp = client.models.generate_content(
    model="gemini-2.5-flash-lite",
    contents=[
        myfile,
        "Transcribe the spoken audio (and any visible on-screen text) to plain text. Language: en-US. Return only the transcript.",
    ],
)
text = (resp.text or "").strip()
```

## Implementation Plan

- [x] Add Gemini client dependency and config
  - [x] Confirm `google-genai` available in runtime; document `GOOGLE_API_KEY`.
- [x] Add optional env knobs: `GEMINI_MODEL` (default `gemini-2.5-flash-lite`), `GEMINI_CONCURRENCY` (default 2), `GEMINI_RETRIES`, `GEMINI_BACKOFF`, `GEMINI_TIMEOUT_FACTOR`, `GEMINI_TIMEOUT_MIN`.

- [x] Implement `_transcribe_chunk_gemini(...)`
  - [x] Signature matches prior chunk transcriber shape (accept path, language, timeout).
  - [x] Always use video: `client.files.upload(file=<video_chunk_path>)` then `client.models.generate_content(...)`. Only fall back to WAV if no video chunk path exists.
  - [x] Prompt clarity: "Transcribe the spoken audio and any visible on-screen text. Language: <lang>. Do not summarize. Return only the transcript."
  - [x] Parse `response.text` and return `(text, raw)` where `raw` is a JSON‑serializable snapshot of the response (or a minimal dict containing key fields).
  - [x] Rate‑limit mapping: treat messages/exceptions containing `429`, `rate`, `quota`, `throttle`, `exceed`, `temporar`, `unavailable`, or HTTP `503` as transient; retry with exponential backoff.

- [x] Wire Gemini into `transcribe_task(...)`
- [x] Require `GOOGLE_API_KEY` and error clearly if missing.
  - [x] Preserve manifest discovery and work‑item preparation exactly as is.
  - [x] Keep `ThreadPoolExecutor` concurrency, retries, timeout calculation, and error aggregation.
  - [x] Update chunk artifact filenames to `.gemini.txt` and `.gemini.json` and combined to `transcript.gemini.txt`.
- [x] Update `artifacts` dict to include `gemini_model`.
- [x] Track minutes in a `gemini.usage.json` (non-gating).

- [x] Increase chunk size to 30 minutes
  - [x] Update default chunking in `ExtractAudioConfig` to `chunk_strategy="duration"`, `chunk_duration_sec=1800` (30 minutes), keep small `chunk_overlap_sec` (e.g., 1–2s).
  - [x] Ensure `extract_audio_task` honors this default; optionally allow overriding via planner/tool args for long videos.
  - [x] Recalculate per‑chunk ASR timeouts (already based on duration × factor) and consider lowering concurrency to avoid very long parallel jobs.
  - [x] Validate limits and fallback: if a 30‑minute chunk fails due to size/timeout, automatically downshift that chunk to 20‑minute subchunks (with small overlap) and retry.

- [x] Video‑first extraction and concurrent processing
  - [x] Replace audio‑only extraction with video extraction: download full YouTube video (best quality MP4 with audio) into `runtime/cache/extract/...` using yt‑dlp with format fallback: `bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best`. If output is not mp4, remux to `.mp4`.
  - [x] Export `.mp4` chunks for each boundary using the same 30‑min windows and small overlap (1–2s).
    - [x] Prefer stream copy for speed:
          `ffmpeg -ss <start> -i <src> -t <dur> -c copy -movflags +faststart -avoid_negative_ts make_zero <out>.mp4`.
    - [x] If cuts land poorly (keyframe issues) or container errors occur, fall back to accurate cut/re‑encode for that chunk:
          `-ss <start> -i <src> -t <dur> -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 128k -movflags +faststart`.
  - [x] Extend the extract manifest: top‑level `video_path` and per‑chunk `video_path` field; keep `wav_path` optional or omit it by default.
  - [x] In `transcribe_task`, always upload the video chunk to Gemini. Only if `video_path` missing, fall back to the WAV path.
  - [x] Keep concurrency with `ThreadPoolExecutor` across chunks; introduce `GEMINI_CONCURRENCY` (and `GEMINI_VIDEO_CONCURRENCY` alias) to tune parallel uploads. Default to 2 for video to balance throughput/RAM/network.
  - [x] Maintain a small overlap (1–2s) to avoid boundary word loss; deduplicate during combine by trusting chunk order (no extra merge logic needed).

- [x] Update prompts and tool specs
- [x] In `src/agent/prompts/planner_prompt.txt`, mention `GOOGLE_API_KEY` in validations.
  - [x] In `src/agent/core/toolkit.py`, update `extract_audio` description to reflect video‑first extraction (download video, produce `.mp4` chunks, optionally WAV). Update `transcribe_asr` description/parameters (accept `model`, `concurrency`). Remove `use_video` flag; video is always used.
  - [x] Ensure `controller_system.txt` references remain accurate for `transcribe_asr`.
- [x] Repo‑wide video‑only processing: update any code/docs that assume audio‑only so the pipeline always uses video by default; retain audio as fallback only.

- [x] Update local check script and docs
  - [x] Revise `local_check_transcribe.py` CLI flags (add `--model`, `--concurrency`).
  - [x] Update README with a short “Gemini transcription” section and env var notes.

- [ ] Validation
  - [ ] Run end‑to‑end on a short sample (≤2–3 chunks) to confirm per‑chunk `.gemini.*` and combined transcript are written.
  - [ ] Test concurrency >1 to ensure thread safety and performance.
  - [ ] Verify retries/backoff paths by simulating/transient errors if possible.
  - [ ] Validate both paths: video (default) and audio fallback when video missing.
  - [ ] Confirm yt‑dlp format selection yields `.mp4` with audio; remux if needed. Verify chunks are playable and uploadable (faststart present).

## Notes & Considerations
- Word-level timestamps: Gemini returns text only; we preserve chunk‑level time bounds from the manifest (start/end per chunk).
- File names change to `.gemini.*`. Downstream steps rely on `state.transcript` and `artifacts["transcribe_asr"]["combined_transcript_path"]`, which remain populated.
- Model choice: Default to `gemini-2.5-flash-lite`. Allow override with `GEMINI_MODEL` or a `model` arg to the tool.
- Storage impact: Video chunks are significantly larger than WAV; ensure disk space in `runtime/cache/extract`. Prefer stream copy to minimize processing time and CPU.

## Acceptance Criteria
- `transcribe_task` produces equivalent outputs (text quality aside) with Gemini: per‑chunk `.gemini.txt/.json`, combined `transcript.gemini.txt`, and populated `state.transcript`/`state.chunks`.
- Concurrency, retries, and error handling work as before.
- Planner/tool prompts validate `GOOGLE_API_KEY` is set.
- Local check script completes with “Transcription OK” on a sample manifest using Gemini.
- Chunks include `video_path`, and Gemini uses video for transcription by default with small overlaps and concurrent processing.
 - Repo updated to video‑first processing across tools/prompts; audio is used only as fallback.
