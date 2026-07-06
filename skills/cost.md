---
name: cost
description: Calculate pricing from an existing BOM JSON for cable manufacturing
---

Calculate floor price and selling price for all cables in a BOM JSON output.

Usage: /cost <path-to-bom.json>

Steps:
1. Run: `cd /Users/ekanshbabbar/Documents/Agents/BOM && python agents/costing_agent.py "$ARGUMENTS"`
2. Show the pricing summary table (material cost, drum cost, margin, floor price, selling price per km and per drum)
3. Report the path to the generated Pricing PDF
4. If RM prices are 0 (not yet configured), warn the user to fill in the Config/Materials sheet

To run BOM + Costing in one step:
  /bom <gtp.pdf>  — then use the output JSON path with /cost

Required environment variables:
- ANTHROPIC_API_KEY
- GOOGLE_CREDENTIALS_FILE (optional)
- SPREADSHEET_ID (optional)
