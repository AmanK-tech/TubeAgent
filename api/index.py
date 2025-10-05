from __future__ import annotations

import os
import sys
from pathlib import Path

from mangum import Mangum


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))
os.environ.setdefault("PYTHONPATH", str(SRC))

os.environ.setdefault("RUNTIME_DIR", "/tmp/tubeagent-runtime")
try:
    Path(os.environ["RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True)
except Exception:
    pass

from src.app.main import create_app  # noqa: E402


app = create_app()


class handler(Mangum):
    def __init__(self):
        super().__init__(app, lifespan="auto")
