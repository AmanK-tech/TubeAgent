from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable


RUNTIME_PATH = Path("/Users/khatri/TubeAgent/runtime")


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

