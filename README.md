# TubeAgent
Agentic YouTube video summariser

Notes:
- Transcription and summarization are powered by Google Gemini. Per‑chunk transcripts and summaries are generated during the ASR step, and a final global summary is produced via a single Gemini call depending on video length (direct multimodal for short videos, map‑reduce for longer ones).
