#!/usr/bin/env python3
"""
Freepik text-to-image generator (synchronous).

Reads FREEPIK_API_KEY from ~/.claude/freepik.env (or the environment), POSTs to
the Freepik text-to-image endpoint, and writes the returned PNG(s) to --out.

Usage:
  python3 generate.py --prompt "..." [--size widescreen_16_9] [--num 1] [--out DIR]

Never prints the API key.
"""
import argparse, base64, json, os, sys, time, urllib.request

API_URL = "https://api.freepik.com/v1/ai/text-to-image"
ENV_FILE = os.path.expanduser("~/.claude/freepik.env")
SIZES = {
    "square_1_1", "widescreen_16_9", "social_story_9_16", "classic_4_3",
    "traditional_3_4", "standard_3_2", "portrait_2_3", "social_post_4_5",
    "horizontal_2_1", "vertical_1_2",
}


def load_key() -> str:
    key = os.environ.get("FREEPIK_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("FREEPIK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass  # no env file — fall through to the error below
    sys.exit(f"FREEPIK_API_KEY not set (env or {ENV_FILE})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--size", default="widescreen_16_9")
    ap.add_argument("--num", type=int, default=1)
    ap.add_argument("--out", default=".")
    a = ap.parse_args()

    if a.size not in SIZES:
        sys.exit(f"--size must be one of: {', '.join(sorted(SIZES))}")
    num = max(1, min(4, a.num))
    os.makedirs(a.out, exist_ok=True)

    body = json.dumps({
        "prompt": a.prompt,
        "num_images": num,
        "image": {"size": a.size},
    }).encode()
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"x-freepik-api-key": load_key(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Freepik API error {e.code}: {e.read().decode()[:300]}")

    data = payload.get("data") or []
    if not data:
        sys.exit(f"No images returned: {json.dumps(payload)[:300]}")

    stamp = int(time.time())
    saved = []
    for i, im in enumerate(data):
        b64 = im.get("base64")
        if not b64:
            continue
        path = os.path.join(a.out, f"freepik-{stamp}-{i}.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        saved.append(path)
    if not saved:
        sys.exit("Response had no base64 image data")
    for p in saved:
        print(p)


if __name__ == "__main__":
    main()
