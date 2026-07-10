"""
config/settings.py
──────────────────
Central configuration module.  All settings are read from environment
variables (populated via the .env file at project root).  Nothing is
ever hardcoded here.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve project root relative to this file's location (config/)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

# Load .env if it exists (silently skip if not — CI/CD may inject vars directly)
load_dotenv(dotenv_path=ENV_FILE)

# ── Database ──────────────────────────────────────────────────────────────────
DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
DB_USER: str = os.getenv("DB_USER", "root")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
DB_NAME: str = os.getenv("DB_NAME", "budgetiq")

# ── Application ───────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Data Paths ────────────────────────────────────────────────────────────────
DATA_DIR: Path = PROJECT_ROOT / "data"
RAW_DATA_PATH: Path = DATA_DIR / "Details_of_Tax_Revenue.csv"
DATABASE_DIR: Path = PROJECT_ROOT / "database"
