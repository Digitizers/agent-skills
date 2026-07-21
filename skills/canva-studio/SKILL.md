---
name: canva-studio
description: Operate the Canva MCP for branded design work — social posts, presentations, marketing collateral, one-pagers. Use when the user wants to create or edit a Canva design, fill a brand template, resize for another format, or export a design ("Canva", "פוסט לרשתות", "מצגת", "brand template", "עצב לי", "make a deck in Canva"). Not for AI image generation or upscaling (magnific-studio), not for UI/product mockups or design-to-code (figma-studio), and not for building slide files locally (the pptx skill).
compatibility: Requires the Canva MCP connector connected to the session (mcp__Canva__* tools).
---

# Canva Studio — branded design via the Canva MCP

Route branded design work through the Canva MCP. Brand consistency comes from
**brand templates and brand kits, not freehand generation** — always look for
an existing template before generating from scratch.

## Core flows

**From a brand template (default path):**
1. `search-brand-templates` — find the right template for the format.
2. `get-brand-template-dataset` — see its autofill fields.
3. `create-design-from-brand-template` with autofill data (headline, body,
   image slots) → review → iterate.

**Freeform (no suitable template):**
- `generate-design` / `generate-design-structured` from a written brief:
  format, copy, palette, imagery direction. Check `list-brand-kits` first and
  reference the brand kit in the brief.

**Refine an existing design:**
- Find it with `search-designs` / `list-folder-items`; read with `get-design`
  + `get-design-content`; preview with `get-design-thumbnail`.
- Edit inside a transaction: `start-editing-transaction` →
  `perform-editing-operations` → `commit-editing-transaction`. On a wrong
  turn, `cancel-editing-transaction` — never leave a transaction hanging.
- `resize-design` to fan one approved design out to other formats
  (post → story → banner).

**Export / deliver:**
- `get-export-formats` → `export-design`. PNG for web/social, PDF for print.

## Guardrails

- **Ask before anything leaves Canva**: exporting, sharing links, publishing a
  brand template (`publish-brand-template`), or commenting on a client-visible
  design all get an explicit human OK first.
- **Don't overwrite an existing design in place** — `copy-design` and work on
  the copy unless the user explicitly says to edit the original.
- Keep client work in its client folder (`create-folder` /
  `move-item-to-folder`); don't scatter drafts at root.
- AI imagery inside a design: generate it with `magnific-studio`, then
  `upload-asset-from-url` / `upload-assets` — Canva composes, Magnific paints.

## Division of labor

| Need | Skill |
|---|---|
| Branded post / deck / flyer / one-pager | **canva-studio** (this) |
| AI image or upscale for use anywhere | `magnific-studio` |
| UI mockup, design-to-code, FigJam | `figma-studio` |
| Local .pptx file as the deliverable | `pptx` skill |
