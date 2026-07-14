"""
Telegram Bot for BOM Agent.

Commands:
  /start              — welcome
  /prices             — show current RM prices
  /setprice MAT PRICE — update a material RM price
  /setmargin GTP ITEM PCT — update margin for a specific cable item

PDF upload → agent runs → summary returned with A/B/C inline selector.
"""

import sys
import os
import json
import logging
import asyncio
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode
from telegram.request import BaseRequest
from telegram.error import NetworkError, TimedOut

import requests as _requests
from typing import Optional


class RequestsBackend(BaseRequest):
    """Uses the synchronous `requests` library (in a thread) instead of httpx.
    Needed because Python 3.12 async-TLS via anyio fails on this machine while
    blocking TLS via requests works fine."""

    def __init__(self):
        self._session = _requests.Session()
        self._session.headers.update({"User-Agent": "python-telegram-bot"})

    @property
    def read_timeout(self) -> Optional[float]:
        return 60.0

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._session.close)

    async def do_request(
        self,
        url: str,
        method: str,
        request_data=None,
        read_timeout: Optional[float] = None,
        write_timeout: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        pool_timeout: Optional[float] = None,
    ):
        timeout = (connect_timeout or 30, read_timeout or 60)
        kwargs: dict = {"timeout": timeout}

        if request_data is not None:
            if request_data.json_parameters:
                kwargs["data"] = request_data.json_parameters
            if request_data.multipart_data:
                kwargs["files"] = request_data.multipart_data

        def _do():
            resp = self._session.request(method, url, **kwargs)
            return resp.status_code, resp.content

        try:
            code, payload = await asyncio.to_thread(_do)
        except _requests.Timeout as e:
            raise TimedOut from e
        except _requests.ConnectionError as e:
            raise NetworkError(str(e)) from e

        return code, payload

from agents.bom_agent import run_bom_agent, _build_rm_prices_used, _build_item_detail
from core.local_registry import upsert_row, get_margin

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_IDS_RAW = os.getenv("TELEGRAM_ALLOWED_IDS", "").split("#")[0].strip()
ALLOWED_IDS: set[int] = (
    {int(x.strip()) for x in ALLOWED_IDS_RAW.split(",") if x.strip()}
    if ALLOWED_IDS_RAW else set()
)

DATA_DIR   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
RM_PRICES_PATH = os.path.join(DATA_DIR, "rm_prices.json")


def _load_rm_prices() -> dict:
    if os.path.exists(RM_PRICES_PATH):
        with open(RM_PRICES_PATH) as f:
            return json.load(f)
    return {}


def _save_rm_prices(prices: dict):
    with open(RM_PRICES_PATH, "w") as f:
        json.dump(prices, f, indent=2)


# ── Auth guard ────────────────────────────────────────────────────────────────

def _is_allowed(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True  # open if no whitelist configured
    uid = update.effective_user.id if update.effective_user else None
    return uid in ALLOWED_IDS


async def _deny(update: Update):
    uid = update.effective_user.id if update.effective_user else "?"
    await update.effective_message.reply_text(
        f"Access denied. Your ID is `{uid}` — ask the admin to whitelist you.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    await update.message.reply_text(
        "*BOM Costing Agent*\n\n"
        "Send me a GTP PDF and I'll compute the Bill of Materials & pricing.\n\n"
        "*Commands*\n"
        "`/prices` — view current RM prices\n"
        "`/setprice <material> <price>` — update an RM price\n"
        "`/setmargin <GTP> <item_no> <margin_pct>` — override margin for one item\n\n"
        "Just send a PDF to get started.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    prices = _load_rm_prices()
    if not prices:
        return await update.message.reply_text("No RM prices loaded.")
    lines = ["*Current RM Prices (₹/kg)*\n```"]
    for mat, price in sorted(prices.items()):
        lines.append(f"{mat:<30} {price:>8,.0f}")
    lines.append("```")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    args = context.args
    if len(args) < 2:
        return await update.message.reply_text(
            "Usage: `/setprice <material_key> <price>`\n"
            "Example: `/setprice copper_conductor 1350`",
            parse_mode=ParseMode.MARKDOWN,
        )
    mat, price_str = args[0], args[1]
    try:
        price = float(price_str)
    except ValueError:
        return await update.message.reply_text("Price must be a number.")
    prices = _load_rm_prices()
    old = prices.get(mat, "N/A")
    prices[mat] = price
    _save_rm_prices(prices)
    await update.message.reply_text(
        f"Updated `{mat}`: ₹{old} → ₹{price:,.0f}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_setmargin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)
    args = context.args
    if len(args) < 3:
        return await update.message.reply_text(
            "Usage: `/setmargin <GTP_No> <item_no> <margin_pct>`\n"
            "Example: `/setmargin IS-17505-2-2 1 22.5`",
            parse_mode=ParseMode.MARKDOWN,
        )
    gtp_no, item_no, pct_str = args[0], args[1], args[2]
    try:
        margin = float(pct_str)
    except ValueError:
        return await update.message.reply_text("Margin must be a number.")

    # Read registry to get cable details, then upsert with new margin
    from core.local_registry import _load_wb, _find_row, REGISTRY_PATH
    import openpyxl
    if not os.path.exists(REGISTRY_PATH):
        return await update.message.reply_text("Registry not found. Run the agent on a GTP first.")

    wb = _load_wb()
    ws = wb["GTP_Registry"]
    row_num = _find_row(ws, gtp_no, item_no)
    if row_num is None:
        return await update.message.reply_text(
            f"Item `{item_no}` not found in GTP `{gtp_no}`.",
            parse_mode=ParseMode.MARKDOWN,
        )
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from core.local_registry import COL_MARGIN, YELLOW_FILL, THIN_BORDER
    cell = ws.cell(row_num, COL_MARGIN)
    old_val = cell.value
    cell.value = margin
    cell.fill = YELLOW_FILL
    cell.number_format = "0.0"
    wb.save(REGISTRY_PATH)

    await update.message.reply_text(
        f"Margin for GTP `{gtp_no}` Item `{item_no}`: {old_val}% → {margin}%\n"
        f"Re-run the agent to recalculate prices.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await _deny(update)

    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".pdf"):
        return await update.message.reply_text("Please send a PDF file.")

    status = await update.message.reply_text("Downloading GTP PDF...")

    # Save to output dir so the file persists and path is unambiguous
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUTPUT_DIR, f"_upload_{doc.file_id}.pdf")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(pdf_path)
        file_size = os.path.getsize(pdf_path)
        logger.info("Downloaded PDF: %s (%d bytes)", pdf_path, file_size)
    except Exception as e:
        logger.exception("PDF download failed")
        return await status.edit_text(f"Failed to download PDF:\n`{e}`", parse_mode=ParseMode.MARKDOWN)

    await status.edit_text("Processing GTP — computing BOM for all cables...")

    requested_type = "A"
    try:
        result = await asyncio.to_thread(
            run_bom_agent, pdf_path, requested_type, False, OUTPUT_DIR
        )
    except Exception as e:
        logger.exception("BOM agent failed")
        await status.edit_text(f"Error processing GTP:\n`{e}`", parse_mode=ParseMode.MARKDOWN)
        return

    gtp_no     = result["gtp_no"]
    new_count  = result["cables_processed"]
    skip_count = result["skipped_existing"]

    # Store per-user so A/B/C callback can switch without re-running agent.
    # Use a short numeric index as callback_data key (Telegram limit: 64 bytes).
    gtp_results = context.user_data.setdefault("gtp_results", {})
    gtp_results[gtp_no] = result
    gtp_index = context.user_data.setdefault("gtp_index", {})
    idx = str(len(gtp_index))
    gtp_index[idx] = gtp_no
    context.user_data["last_gtp_idx"] = idx

    header = (
        f"*{new_count} cables processed"
        + (f", {skip_count} already in registry" if skip_count else "")
        + f"*\n\n"
        + result["summary_table"]
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Type A", callback_data=f"bom:{idx}:A"),
            InlineKeyboardButton("Type B", callback_data=f"bom:{idx}:B"),
            InlineKeyboardButton("Type C", callback_data=f"bom:{idx}:C"),
        ]
    ])

    await status.edit_text(header, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

    # Full BOM + costing + raw material prices, per item, so mistakes in the
    # GTP reading or the price sheet are easy to catch before quoting.
    rm_prices_msg = _build_rm_prices_used(result["_all_items"], requested_type)
    if rm_prices_msg:
        await update.message.reply_text(rm_prices_msg, parse_mode=ParseMode.MARKDOWN)

    for item in result["_all_items"]:
        detail = _build_item_detail(item, requested_type)
        if detail:
            await update.message.reply_text(detail, parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.3)  # avoid Telegram flood limits on GTPs with many items


async def handle_bom_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, idx, bom_type = query.data.split(":", 2)
    gtp_no = context.user_data.get("gtp_index", {}).get(idx)
    stored = context.user_data.get("gtp_results", {}).get(gtp_no) if gtp_no else None

    if not stored:
        return await query.edit_message_text(
            "Session expired. Please re-upload the GTP PDF."
        )

    all_items = stored.get("_all_items", [])
    if not all_items:
        return await query.edit_message_text("No item data cached. Please re-upload.")

    price_key = f"price_{bom_type.lower()}"
    from agents.bom_agent import _build_summary
    summary = _build_summary(gtp_no, bom_type, all_items, price_key)

    header = f"*Showing BOM Type {bom_type}*\n\n" + summary
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Type A", callback_data=f"bom:{idx}:A"),
            InlineKeyboardButton("Type B", callback_data=f"bom:{idx}:B"),
            InlineKeyboardButton("Type C", callback_data=f"bom:{idx}:C"),
        ]
    ])
    await query.edit_message_text(header, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    async def on_startup(application):
        me = await application.bot.get_me()
        print(f"Bot ready: @{me.username} — send a GTP PDF to get started.")

    print(f"Bot starting. Allowed IDs: {ALLOWED_IDS or 'ALL (no whitelist)'}")
    app = Application.builder().token(BOT_TOKEN).request(RequestsBackend()).post_init(on_startup).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("prices",    cmd_prices))
    app.add_handler(CommandHandler("setprice",  cmd_setprice))
    app.add_handler(CommandHandler("setmargin", cmd_setmargin))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(CallbackQueryHandler(handle_bom_type_callback, pattern=r"^bom:"))

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
