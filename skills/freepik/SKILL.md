---
name: freepik
description: Generate AI images and upscale via the Freepik / Magnific REST API. Use when the user wants to generate an AI image, hero visual, banner background, illustration, or texture from a text prompt, or to upscale/enhance an existing image. Triggers on "Freepik", "Magnific", "generate an image", "AI background", "AI hero visual", "text-to-image", "upscale this image". Reads the API key from ~/.claude/freepik.env (FREEPIK_API_KEY) so it works across all sessions.
---

# Freepik image generation

Wraps the Freepik REST API so any Claude Code session can generate AI imagery
without an MCP server. The API key lives once in `~/.claude/freepik.env` as
`FREEPIK_API_KEY=...` (perms 600) and the script reads it automatically.

## When to use

- Generate a hero visual / banner background / illustration / texture / pattern
  from a text prompt (text-to-image, synchronous — returns the image directly).
- The user mentions Freepik, Magnific, "AI background", "generate an image", or
  "upscale this image".

## Generate (text-to-image, synchronous)

```bash
python3 ~/.claude/skills/freepik/scripts/generate.py \
  --prompt "abstract teal and indigo network mesh on dark navy, cinematic, no text" \
  --size widescreen_16_9 --num 2 \
  --out /path/to/output-dir
```

- `--size` options: `square_1_1`, `widescreen_16_9`, `social_story_9_16`,
  `classic_4_3`, `traditional_3_4`, `standard_3_2`, `portrait_2_3`,
  `social_post_4_5`, `horizontal_2_1`, `vertical_1_2` (default `widescreen_16_9`).
- `--num` 1-4 (default 1). Writes `freepik-<timestamp>-<i>.png` to `--out`.
- Prints the saved file paths. Costs API credits — keep `--num` small while iterating.

**Prompt tips:** describe subject, palette, lighting, mood, composition. For
banner backgrounds add `no text, no letters, no words` (overlay text in
HTML/CSS afterwards), and steer composition (`subject on the right, left side
dark negative space`) so headline text stays legible.

## Compose into a banner

Generate the background → set it as a full-bleed `background-image` in an HTML
banner → add a dark side-gradient overlay for text legibility → screenshot with
headless Chrome at exact dimensions. See the `banner-design` skill for the
HTML→PNG export pattern.

## Notes

- **No webhook needed.** Text-to-image is synchronous. Async endpoints (Mystic,
  Magnific upscale) return a `task_id`; poll the status endpoint rather than
  using webhooks (no public callback URL on a local machine). A
  `FREEPIK_WEBHOOK_SECRET` in the env file is optional and only used if you ever
  run a public webhook receiver.
- Never print the API key. The script reads it from the env file silently.
