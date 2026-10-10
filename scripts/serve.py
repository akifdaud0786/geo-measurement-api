"""Run the API from any working directory (python scripts/serve.py [port])."""

import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)
