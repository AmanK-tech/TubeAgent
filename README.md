# TubeAgent
Agentic YouTube video summariser

Notes:
- Transcription and summarization are powered by Google Gemini. Per‑chunk transcripts and summaries are generated during the ASR step, and a final global summary is produced via a single Gemini call depending on video length (direct multimodal for short videos, map‑reduce for longer ones).

## YouTube cookies (when YouTube blocks anonymous access)

Some videos require an authenticated session (consent/age/region/bot checks). Configure yt‑dlp to use your browser cookies without exposing credentials:

- Option A — cookies.txt (recommended for servers)
  - Export once on your laptop: `yt-dlp --cookies-from-browser chrome --cookies ~/yt-cookies.txt "https://www.youtube.com/watch?v=VIDEO_ID"`
  - Deploy and mount the file as a secret and set: `YT_COOKIES_FILE=/run/secrets/yt_cookies.txt`

- Option B — read cookies from a local browser (workstations only)
  - Set env vars: `YT_COOKIES_FROM_BROWSER=chrome` and optionally `YT_COOKIES_BROWSER_PROFILE=Default`

Security notes:
- The app never logs cookie contents and only reads them from env/secret at runtime.
- Do not commit cookie files; mount them as read‑only secrets.
