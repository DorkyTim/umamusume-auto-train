# logging_tools.py
import logging
import os
from logging.handlers import RotatingFileHandler

# ============================================================
# LOGGING VERBOSITY CONTROL (PRIMARY SWITCH)
# ------------------------------------------------------------
# Change THIS value to control overall verbosity:
#
#   logging.ERROR    → production (recommended)
#   logging.WARNING  → warnings + errors
#   logging.INFO     → normal operational logs
#   logging.DEBUG    → very verbose (development / troubleshooting)
# ============================================================
LOG_LEVEL = logging.DEBUG   # ← CHANGE THIS WHEN YOU WANT MORE LOGS

# -------------------------
# Root logger configuration
# -------------------------
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(levelname)s] %(message)s",
)

root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)

# ============================================================
# THIRD-PARTY LIBRARY NOISE CONTROL
# ------------------------------------------------------------
# These libraries are very chatty at INFO/DEBUG.
# Keep them at ERROR unless you're actively debugging them.
# ============================================================
logging.getLogger("pytesseract").setLevel(logging.ERROR)
logging.getLogger("PIL").setLevel(logging.ERROR)

# Discord-specific noise
logging.getLogger("discord").setLevel(logging.ERROR)
logging.getLogger("discord.http").setLevel(logging.ERROR)
logging.getLogger("discord.gateway").setLevel(logging.ERROR)

# ============================================================
# FILE LOGGING (ROTATING)
# ------------------------------------------------------------
# File logging is usually kept LESS verbose than console output.
# You can change FILE_LOG_LEVEL independently if needed.
# ============================================================
FILE_LOG_LEVEL = LOG_LEVEL  # ← change independently if desired

log_dir = os.path.join(os.getcwd(), "logs")
os.makedirs(log_dir, exist_ok=True)

file_handler = RotatingFileHandler(
    os.path.join(log_dir, "log.txt"),
    maxBytes=1_000_000,
    backupCount=10,
    encoding="utf-8",
)

file_handler.setLevel(FILE_LOG_LEVEL)
file_handler.setFormatter(
    logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
)

root_logger.addHandler(file_handler)

# ============================================================
# CONVENIENCE ALIASES
# ------------------------------------------------------------
# Safe to use everywhere in your codebase.
# Actual output is controlled ONLY by LOG_LEVEL above.
# ============================================================
info = logging.info
warning = logging.warning
error = logging.error
debug = logging.debug
