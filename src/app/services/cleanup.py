from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional
import json

try:
    from google import genai  # type: ignore
except Exception:  # pragma: no cover
    genai = None  # type: ignore


def _resolve_runtime_path() -> Path:
    """Resolve the runtime directory for deployments.

    Priority:
      1) Env var RUNTIME_DIR
      2) Env var TUBEAGENT_RUNTIME_DIR
      3) repo-relative ./runtime (default used across the codebase)
    """
    env = os.getenv("RUNTIME_DIR") or os.getenv("TUBEAGENT_RUNTIME_DIR")
    if env:
        p = Path(env).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return p
    # default to repo runtime folder
    return Path(__file__).resolve().parents[3] / "runtime"


RUNTIME_PATH = _resolve_runtime_path()


def safe_purge_runtime(path: Path = RUNTIME_PATH) -> None:
    """Delete the configured runtime folder if it matches the expected root.

    Guards against accidental deletion of arbitrary paths by checking that the
    path exists and is exactly the known runtime directory under the project.
    """
    try:
        if not path.exists():
            return
        # Ensure we only delete the precise runtime directory
        expected = RUNTIME_PATH.resolve()
        actual = path.resolve()
        if actual != expected:
            return
        shutil.rmtree(actual, ignore_errors=True)
    except Exception:
        # Best-effort cleanup; swallow errors to avoid crashing request handlers
        pass


def delete_gemini_uploads(files: Iterable[object], client: object | None) -> None:
    """Best-effort deletion of Gemini uploads.

    Expects each file to have a `name` attribute and the client to support
    `client.files.delete(name=...)`. Silently ignores errors or missing client.
    """
    if client is None:
        return
    for f in files or []:
        try:
            name = getattr(f, "name", None) or getattr(f, "id", None)
            if not name:
                continue
            client.files.delete(name=name)  # type: ignore[attr-defined]
        except Exception:
            continue


# ---------------------- Session-targeted cleanup ----------------------------

def _is_under_runtime(p: Path) -> bool:
    try:
        return RUNTIME_PATH.resolve() in p.resolve().parents or p.resolve() == RUNTIME_PATH.resolve()
    except Exception:
        return False


def _safe_rmtree(p: Optional[Path]) -> None:
    try:
        if not p:
            return
        if p.exists() and _is_under_runtime(p):
            shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


def delete_gemini_uploads_by_names(names: Iterable[str]) -> None:
    """Delete Gemini files by name/id. Best effort; requires GOOGLE_API_KEY.

    Accepts iterable of strings like 'files/abc123' or raw ids. Ignores errors.
    """
    if not names:
        return
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key or genai is None:
        return
    try:
        client = genai.Client(api_key=api_key)  # type: ignore
    except Exception:
        return
    seen = set()
    for n in names:
        try:
            name = str(n or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            # client accepts either 'files/..' or id; pass as-is
            client.files.delete(name=name)  # type: ignore[attr-defined]
        except Exception:
            continue


def cleanup_session_artifacts(agent_ctx: dict) -> None:
    """Best‑effort cleanup of runtime caches, summaries and Gemini files for one session.

    - Deletes extract folder (parent of manifest_path) under runtime/cache/extract/
    - Deletes summaries/<job-id>/ where job-id is the manifest parent folder name
    - Deletes uploaded Gemini files referenced in transcribe_asr artifacts
    """
    try:
        arts = (agent_ctx or {}).get("artifacts", {}) if isinstance(agent_ctx, dict) else {}
        ta = arts.get("transcribe_asr", {}) if isinstance(arts, dict) else {}
        manifest = ta.get("manifest_path") if isinstance(ta, dict) else None
        manifest_p = Path(manifest).resolve() if manifest else None
    except Exception:
        manifest_p = None

    # Compute job id from manifest parent
    job_id = None
    extract_dir = None
    if manifest_p and manifest_p.exists():
        try:
            extract_dir = manifest_p.parent
            job_id = extract_dir.name
        except Exception:
            job_id = None

    # Try to capture any downloaded media path before deleting extract dir
    dl_candidates: set[Path] = set()
    try:
        # 1) From manifest JSON if available
        if manifest_p and manifest_p.exists():
            try:
                data = json.loads(manifest_p.read_text(encoding="utf-8"))
                cand = data.get("downloaded_path") or (data.get("result", {}) or {}).get("downloaded_path")
                if isinstance(cand, str) and cand:
                    dl_candidates.add(Path(cand))
                # Sometimes video_path is stored under result
                vpath = (data.get("result", {}) or {}).get("video_path")
                if isinstance(vpath, str) and vpath:
                    dl_candidates.add(Path(vpath))
            except Exception:
                pass
        # 2) From extract_audio artifacts
        try:
            ea = arts.get("extract_audio", {}) if isinstance(arts, dict) else {}
            v = ea.get("video_path") if isinstance(ea, dict) else None
            if isinstance(v, str) and v:
                dl_candidates.add(Path(v))
        except Exception:
            pass
    except Exception:
        pass

    # Delete extract cache dir
    _safe_rmtree(extract_dir)

    # Delete summaries/<job-id>
    if job_id:
        _safe_rmtree(RUNTIME_PATH / "summaries" / job_id)

    # Delete Gemini uploaded files
    names: list[str] = []
    try:
        chunks = ta.get("chunks", []) if isinstance(ta, dict) else []
        for ch in chunks or []:
            n = (ch or {}).get("gemini_file_name")
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
    except Exception:
        pass
    delete_gemini_uploads_by_names(names)

    # Delete only this session's downloaded media in runtime/downloads
    try:
        downloads_root = (RUNTIME_PATH / "downloads").resolve()
        for p in list(dl_candidates):
            try:
                rp = Path(p).resolve()
                if not rp.exists() or not rp.is_file():
                    continue
                # Only delete files inside runtime/downloads to avoid accidental data loss
                if downloads_root in rp.parents or rp.parent == downloads_root:
                    # Optionally limit to common media extensions
                    if rp.suffix.lower() in {".webm", ".mp4", ".mkv", ".mov", ".m4a"}:
                        rp.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass


# NOTE: Removed global cleaner that wiped all extract jobs; per-session cleanup is safer.
