"""
Telegram Bot — receives GTP PDFs, runs BOM + Costing agents, replies with pricing.

Setup:
  1. Create bot via @BotFather → get token
  2. export TELEGRAM_BOT_TOKEN=<token>
  3. python integrations/telegram_bot.py
"""

import sys
import os
import logging
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Update, Document
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from config.settings import TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_CHAT_IDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _check_auth(update: Update) -> bool:
    if not ALLOWED_TELEGRAM_CHAT_IDS:
        return True
    return update.effective_chat.id in ALLOWED_TELEGRAM_CHAT_IDS


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return
    await update.message.reply_text(
        "👋 *[YOUR COMPANY] BOM Bot*\n\n"
        "Send me a GTP PDF file and I'll calculate the BOM and pricing.\n\n"
        "Commands:\n"
        "/status — last processed GTP\n"
        "/prices — current raw material prices\n"
        "/help — usage guide",
        parse_mode="Markdown"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
    jsons = sorted([f for f in os.listdir(output_dir) if f.startswith("costing_")], reverse=True)
    if jsons:
        await update.message.reply_text(f"Last processed: `{jsons[0]}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("No GTPs processed yet.")


async def cmd_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return
    try:
        from integrations.sheets_client import SheetsClient
        sc = SheetsClient()
        prices = sc.get_rm_prices()
        lines = ["*Current RM Prices (₹/kg)*\n"]
        for mat, price in prices.items():
            lines.append(f"• {mat.replace('_', ' ').title()}: ₹{price:,.2f}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Could not load prices: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming PDF files — run full BOM + costing pipeline."""
    if not _check_auth(update):
        await update.message.reply_text("Unauthorized.")
        return

    doc: Document = update.message.document
    if not doc or not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Please send a GTP PDF file.")
        return

    await update.message.reply_text(f"📄 Received: `{doc.file_name}`\nProcessing BOM... this may take a minute.", parse_mode="Markdown")

    # Download PDF
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, doc.file_name)
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(pdf_path)

        try:
            # Step 3: BOM
            from agents.bom_agent import run_bom_agent
            output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
            bom_result = run_bom_agent(pdf_path, output_dir=output_dir)

            # Step 4: Costing
            from agents.costing_agent import run_costing_agent
            cost_result = run_costing_agent(bom_result["json_path"], output_dir=output_dir)

            # Send pricing summary text
            await update.message.reply_text(cost_result["telegram_summary"], parse_mode="Markdown")

            # Send pricing PDF
            if os.path.exists(cost_result["pdf_path"]):
                with open(cost_result["pdf_path"], "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(cost_result["pdf_path"]),
                        caption=f"Pricing Report — {cost_result['gtp_ref']} Type {cost_result['gtp_type']}"
                    )

            # Send production BOM PDF (always send it — user can decide to keep or ignore)
            if os.path.exists(bom_result["pdf_path"]):
                with open(bom_result["pdf_path"], "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(bom_result["pdf_path"]),
                        caption=f"Production BOM — {bom_result['gtp_ref']} Type {bom_result['gtp_type']}"
                    )

        except Exception as e:
            logger.exception("Pipeline failed")
            await update.message.reply_text(f"❌ Error processing GTP:\n`{e}`", parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return
    await update.message.reply_text(
        "*How to use this bot*\n\n"
        "1. Send any GTP PDF file (e.g. `2-23077-GTP-A.pdf`)\n"
        "   • GTP type A/B/C is auto-detected from the filename suffix\n"
        "2. The bot will:\n"
        "   • Parse all cable specifications from the PDF\n"
        "   • Calculate Costing BOM and Production BOM for each cable\n"
        "   • Calculate floor price and selling price\n"
        "   • Reply with a pricing summary\n"
        "   • Send the Pricing PDF report\n"
        "   • Send the Production BOM PDF\n\n"
        "*Commands:*\n"
        "/status — last processed GTP\n"
        "/prices — current RM prices\n"
        "/help — this message",
        parse_mode="Markdown"
    )


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set. export TELEGRAM_BOT_TOKEN=<your-token>")
        sys.exit(1)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("prices", cmd_prices))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))

    print("Bot running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
