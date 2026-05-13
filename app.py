"""
Vercel entrypoint for the Jardamchy GO web test chatbot.

Railway still runs src/app.py. This root module exists so Vercel deploys only
the lightweight test harness instead of the production webhook application.
"""

from __future__ import annotations

import sys
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from web_test_app import create_app  # noqa: E402


app = create_app()
