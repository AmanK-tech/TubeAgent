from __future__ import annotations

import math
from pathlib import Path
from typing import List, Tuple

from agent.core.config import ExtractAudioConfig


def compute_chunk_boundaries_duration(total_dur: float, cfg: ExtractAudioConfig) -> List[Tuple[float, float]]:
    step = max(1, int(cfg.chunk_duration_sec))
    ov = max(0.0, float(cfg.chunk_overlap_sec))
    cuts: List[Tuple[float, float]] = []
    t = 0.0
    while t < total_dur:
        end = min(total_dur, t + step)
        cuts.append((t, end))
        if end >= total_dur:
            break
        t = end - ov
    if len(cuts) >= 2 and (cuts[-1][1] - cuts[-1][0]) < 30.0:
        prev_s, _ = cuts[-2]
        cuts[-2] = (prev_s, cuts[-1][1])
        cuts.pop()
    return cuts


def chunk_vad_energy(pcm_wav_path: Path, cfg: ExtractAudioConfig) -> List[Tuple[float, float]]:
    """Energy-based VAD over mono/16k PCM WAV.
    Prefer audioop_lts; fall back to pure-Python RMS.
    """
    import wave

    try:  # pip install audioop-lts
        import audioop_lts as audioop  # type: ignore
        _rms = lambda data, sw: float(audioop.rms(data, sw))  # noqa: E731
    except Exception:  # pragma: no cover - environment dependent
        audioop = None  # type: ignore
        _rms = None  # type: ignore

    def rms_fallback_s16(data: bytes, sample_width: int) -> float:
        if sample_width != 2 or not data:
            return 0.0
        from array import array
        arr = array("h")
        try:
            arr.frombytes(data)
        except Exception:
            return 0.0
        if not arr:
            return 0.0
        ssum = 0
        for v in arr:
            ssum += v * v
        mean = ssum / float(len(arr))
        return math.sqrt(mean)

    segments: List[Tuple[float, float]] = []
    with wave.open(str(pcm_wav_path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        if sr != cfg.sample_rate or ch != 1:
            total = wf.getnframes() / float(sr) if sr else 0.0
            return [(0.0, total)] if total > 0 else []

        frame_ms = 30
        frames_per_step = max(1, int(sr * (frame_ms / 1000.0)))
        threshold = 300.0
        speech_regions: List[Tuple[int, int]] = []
        in_speech = False
        start_idx = 0
        idx = 0
        data = wf.readframes(frames_per_step)
        while data:
            if _rms is not None:
                val = _rms(data, sw)
            else:
                val = rms_fallback_s16(data, sw)
            if val >= threshold:
                if not in_speech:
                    in_speech = True
                    start_idx = idx
            else:
                if in_speech:
                    in_speech = False
                    speech_regions.append((start_idx, idx))
            idx += 1
            data = wf.readframes(frames_per_step)
        if in_speech:
            speech_regions.append((start_idx, idx))

        merged: List[Tuple[int, int]] = []
        for seg in speech_regions:
            if not merged:
                merged.append(seg)
                continue
            prev_s, prev_e = merged[-1]
            if (seg[0] - prev_e) * frame_ms < 300:
                merged[-1] = (prev_s, seg[1])
            else:
                merged.append(seg)

        max_frames = max(1, int(cfg.chunk_max_sec * 1000 / frame_ms))
        for s, e in merged:
            while (e - s) > max_frames:
                segments.append((s * frame_ms / 1000.0, (s + max_frames) * frame_ms / 1000.0))
                s += max_frames
            segments.append((s * frame_ms / 1000.0, e * frame_ms / 1000.0))

        if cfg.chunk_overlap_sec > 0 and segments:
            ov = cfg.chunk_overlap_sec
            out: List[Tuple[float, float]] = []
            for i, (s, e) in enumerate(segments):
                s2 = max(0.0, s - (ov if i > 0 else 0.0))
                e2 = e + (ov if i < len(segments) - 1 else 0.0)
                out.append((s2, e2))
            segments = out

    return segments

