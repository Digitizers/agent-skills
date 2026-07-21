---
name: figma-studio
description: Route design work through the Figma MCP — client-site mockups, UI screens, design-to-code, code-to-design, FigJam diagrams. Use when the user wants to mock up a page or screen, implement a Figma design as code, push a page into Figma, diagram a flow, or shares a figma.com URL ("Figma", "FigJam", "mockup", "מוקאפ", "design-to-code"). Not for branded social posts or presentations (canva-studio) and not for AI image generation or upscaling (magnific-studio).
compatibility: Requires the Figma MCP connector connected to the session (mcp__Figma__* tools).
---

# Figma Studio — mockups and design↔code via the Figma MCP

Thin routing layer over the Figma MCP: Figma ships its **own official
skills** — this skill's job is to make sure they get used correctly and to
carry Digitizer's conventions, not to duplicate them.

## The one hard rule

Before ANY call to `use_figma`, load Figma's official `/figma-use` skill
(fallback if not installed: read `skill://figma/figma-use/SKILL.md` via the
MCP resources). Same for the other official skills when their flow applies:
`/figma-generate-design`, `/figma-generate-library`, `/figma-code-connect`,
`/figma-use-figjam`. Skipping them produces broken node operations.

## Intent → tool routing

| Intent | Route |
|---|---|
| Read a design someone shares (figma.com URL) | `get_design_context`, `get_screenshot`, `get_metadata` |
| Implement a design as code (design-to-code) | `get_design_context` for structure/tokens → write the code → compare against `get_screenshot` |
| Mock up a page/screen from a brief or existing code | `/figma-generate-design` → `generate_figma_design` (new file via `create_new_file` when starting fresh) |
| Flow/architecture diagram | `/figma-use-figjam` → `generate_diagram` |
| Map Figma components to code components | `/figma-code-connect` flow |

## Digitizer uses

- **Proposal mockups**: a homepage/landing concept in Figma sells a website
  deal better than a text quote — generate from the client's existing site
  plus the brief, screenshot, and drop into the proposal.
- **Audit illustrations**: before/after frames for website-audit findings.
- **Pre-build alignment**: mock the screen in Figma, get the client's OK,
  then build — changes cost minutes in Figma and hours in Elementor.

## Guardrails

- **Never edit a client-shared Figma file without an explicit OK** — work in
  a copy or a new file; comment (`comment-on-design` equivalents) only when
  asked.
- Don't paste Figma URLs of client work into public places; the link often
  grants access.
- Export/screenshot before big generate operations so there's a visual
  baseline to compare against.

## Division of labor

| Need | Skill |
|---|---|
| UI mockup, design↔code, FigJam | **figma-studio** (this) |
| Branded post / deck / flyer | `canva-studio` |
| AI imagery to place inside a design | `magnific-studio` |
