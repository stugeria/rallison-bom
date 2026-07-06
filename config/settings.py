import os

# ── Anthropic / Claude ──────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"

# ── Google Sheets ───────────────────────────────────────────────────────────
# Path to your Google service-account JSON key file
GOOGLE_CREDENTIALS_FILE = os.environ.get(
    "GOOGLE_CREDENTIALS_FILE",
    os.path.expanduser("~/.config/cable_bom/google_credentials.json")
)
SPREADSHEET_NAME = "Cable_BOM_System"
# Once created, populate this with the actual sheet ID for faster access
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

# ── Telegram Bot ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Restrict bot to known chat IDs (leave empty to allow all)
ALLOWED_TELEGRAM_CHAT_IDS: list[int] = []

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── BOM defaults ────────────────────────────────────────────────────────────
# resistivity in Ω·mm²/m
RESISTIVITY = {
    "copper":    1 / 58,   # 0.017241
    "aluminium": 1 / 35,   # 0.028571
}
