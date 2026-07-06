# Setup Guide — Cable BOM System (Google Apps Script + Telegram)

## How it works
```
You send GTP PDF on Telegram
        ↓
Telegram Webhook → Google Apps Script (runs on Google's servers — no server from you needed)
        ↓
Apps Script: parses PDF (Claude API) → calculates BOM → writes to Google Sheet → replies on Telegram
```

---

## Step 1 — Get your API keys

**Anthropic API Key:**
- Go to console.anthropic.com → API Keys → Create key
- Save as `sk-ant-...`

**Telegram Bot Token:**
- Open Telegram → search @BotFather → `/newbot`
- Follow prompts → get token like `7123456789:AAF...`

---

## Step 2 — Create the Apps Script project

1. Go to [script.google.com](https://script.google.com) → **New Project**
2. Name it `Cable BOM System`
3. Delete the default `Code.gs` content
4. Copy the contents of `Code.gs` → paste into the editor
5. Click **+** (Add File) → Script → name it `Setup`
6. Copy the contents of `Setup.gs` → paste into the `Setup.gs` editor
7. **Save** (Ctrl+S)

---

## Step 3 — Set Script Properties

In the Apps Script editor:
1. Click **Project Settings** (gear icon on left)
2. Scroll to **Script Properties** → **Add script property**
3. Add these one by one:

| Property | Value |
|----------|-------|
| `ANTHROPIC_API_KEY` | `sk-ant-...` (your Anthropic key) |
| `TELEGRAM_BOT_TOKEN` | `7123456789:AAF...` (your bot token) |
| `COMPANY_NAME` | Your company name |

---

## Step 4 — Create the Google Sheet

1. In the Apps Script editor, select function `createSpreadsheet`
2. Click **Run** (▶)
3. Approve permissions when prompted
4. The log will show: `Spreadsheet ID: 1abc...xyz` and the sheet URL
5. Open the URL — you'll see 11 tabs pre-filled with config data

> The `SPREADSHEET_ID` is automatically saved to Script Properties by `createSpreadsheet()`.

---

## Step 5 — Deploy as Web App

1. Click **Deploy** → **New deployment**
2. Click the gear icon next to "Select type" → choose **Web app**
3. Settings:
   - Description: `Cable BOM Bot`
   - Execute as: **Me**
   - Who has access: **Anyone** (Telegram needs to reach it)
4. Click **Deploy** → copy the **Web App URL** (looks like `https://script.google.com/macros/s/AKfy.../exec`)

---

## Step 6 — Register the Telegram Webhook

1. In Script Properties, add: `WEB_APP_URL` → paste the Web App URL from Step 5
2. In the Apps Script editor, select function `registerTelegramWebhook`
3. Click **Run**
4. Log should show: `{"ok":true,"result":true,...}`

To verify: run `checkTelegramWebhook` — should show your URL and `"pending_update_count":0`

---

## Step 7 — Fill in your data

Open the Google Sheet and fill in:

| Tab | What to fill |
|-----|-------------|
| `Config/Materials` | `rm_price_per_kg` for each material (₹/kg) |
| `Config/Drums` | `cost_per_drum` for each drum type and size |
| `Config/Margins` | Review/adjust margin % — pre-filled with estimates |
| `Config/Extrusion_Tolerances` | Review/adjust tolerance factors |
| `Config/GTP_Types` | Fill A/B/C factors once IS 7098 armour formula confirmed |

---

## Step 8 — Test

1. Open Telegram → find your bot → `/start`
2. Send the GTP PDF file (`2-23077-GTP.pdf`)
3. Within 1-2 minutes you'll receive:
   - A pricing summary message
   - Results appear in `BOM_Results` and `Costing_Results` tabs

---

## Updating formulas or factors

All formulas and factors are in the Google Sheet — no code changes needed:
- Change lay factors → `Config/Lay_Factors`
- Change extrusion tolerances → `Config/Extrusion_Tolerances`
- Change margins → `Config/Margins`
- Change GTP A/B/C factors → `Config/GTP_Types`
- See all formulas documented → `Config/Formulas`

To update the calculation code: edit `Code.gs` in Apps Script editor → re-deploy.

---

## Re-deploying after code changes

When you edit `Code.gs`:
1. **Deploy** → **Manage deployments**
2. Click the pencil (edit) on your deployment
3. Change version to **New version**
4. Click **Deploy**

The webhook URL stays the same — no need to re-register with Telegram.
