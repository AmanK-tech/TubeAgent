#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

# Import project modules
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.core.state import AgentState, Config
from agent.tools.transcribe import summarise_gemini


def _find_latest_manifest(runtime_dir: Path) -> Path | None:
    base = runtime_dir / "cache" / "extract"
    if not base.exists():
        return None
    newest: tuple[float, Path | None] = (0.0, None)
    for child in base.iterdir():
        if not child.is_dir():
            continue
        mp = child / "extract_audio.manifest.json"
        if mp.exists():
            try:
                mt = mp.stat().st_mtime
            except Exception:
                mt = 0.0
            if mt >= newest[0]:
                newest = (mt, mp)
    return newest[1]


def _load_manifest(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _rehydrate_state(manifest_path: Path) -> AgentState:
    cfg = Config(
        profile="local",
        provider="none",
        model="none",
        max_tokens=0,
        cost_limit_usd=0.0,
        step_limit=0,
        runtime_dir=ROOT / "runtime",
    )
    state = AgentState(config=cfg)
    man = _load_manifest(manifest_path)
    result = (man.get("result") or {})
    chunks = result.get("chunks") or []
    out_dir = manifest_path.parent

    # Compose minimal artifacts for summarise_gemini to work without re-transcribing
    ta = {"manifest_path": str(manifest_path), "chunks": []}
    for ent in chunks:
        idx = int(ent.get("idx", 0))
        start_s = float(ent.get("start_sec", 0.0) or 0.0)
        end_s = float(ent.get("end_sec", 0.0) or 0.0)
        vid = ent.get("video_path") or ent.get("path")
        text_path = out_dir / f"chunk_{idx:04d}.gemini.txt"
        sum_path = out_dir / f"chunk_{idx:04d}.summary.txt"
        ta["chunks"].append(
            {
                "idx": idx,
                "start_sec": start_s,
                "end_sec": end_s,
                "video_path": vid,
                "text_path": str(text_path) if text_path.exists() else None,
                "summary_path": str(sum_path) if sum_path.exists() else None,
            }
        )

    state.artifacts["transcribe_asr"] = ta
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask further questions on an existing transcript (no re-transcription)")
    ap.add_argument("--manifest", help="Path to extract_audio.manifest.json (auto-discovered if omitted)")
    ap.add_argument("--user-req", required=True, help="Your question or request")
    ap.add_argument("--intent", help="Optional intent hint (summary, question, search, fact_extraction, analysis, comparison, time_based)")
    ap.add_argument("--include-metadata", action="store_true", help="Include title/channel/URL metadata if available")
    ap.add_argument("--force-map-reduce", action="store_true", help="Force map-reduce (avoid re-uploading media for short videos)")
    args = ap.parse_args()

    runtime_dir = ROOT / "runtime"
    manifest_path = Path(args.manifest).resolve() if args.manifest else _find_latest_manifest(runtime_dir)
    if not manifest_path or not manifest_path.exists():
        print("No manifest found. Run extract/transcribe first or pass --manifest.")
        return 2

    if args.force_map_reduce:
        # Ensure we don't do a direct multimodal pass that might re-upload files
        os.environ["GLOBAL_DIRECT_MINUTES_LIMIT"] = "0"

    state = _rehydrate_state(manifest_path)
    text = summarise_gemini(
        state,
        user_req=args.user_req,
        intent=args.intent,
        include_metadata=bool(args.include_metadata),
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

