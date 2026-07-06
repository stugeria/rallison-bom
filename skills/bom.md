---
name: bom
description: Calculate Bill of Materials from a GTP PDF for cable manufacturing
---

Calculate the Bill of Materials (BOM) for all cable types in a GTP PDF.

Usage: /bom <path-to-gtp.pdf> [A|B|C]

Steps:
1. Run: `cd /Users/ekanshbabbar/Documents/Agents/BOM && python agents/bom_agent.py "$ARGUMENTS"`
2. Show the user a summary table of materials per cable type (kg/km, both costing and production values)
3. Report the path to the generated Production BOM PDF
4. If the run fails, show the error and suggest checking ANTHROPIC_API_KEY is set

The GTP type (A/B/C) is auto-detected from the filename suffix. Override by passing it as second argument.

Required environment variables:
- ANTHROPIC_API_KEY
- GOOGLE_CREDENTIALS_FILE (optional — falls back to local defaults if not set)
- SPREADSHEET_ID (optional)
