#!/usr/bin/env python3
"""
Freepik / Magnific image upscaler (asynchronous → polled, no webhook).

Submits a local image to the Freepik image-upscaler, polls the task status until
COMPLETED, then downloads the result. No public webhook URL needed — we poll.

Usage:
  python3 upscale.py --image in.png [--out DIR] [--scale 2x] [--timeout 300]

Reads FREEPIK_API_KEY from ~/.claude/freepik.env or the environment. Never prints it.
"""
import argparse, base64, json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.freepik.com/v1/ai/image-upscaler"
ENV_FILE = os.path.expanduser("~/.claude/freepik.env")


def load_key() -> str:
    key = os.environ.get("FREEPIK_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if line.strip().startswith("FREEPIK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass  # no env file — fall through to the error below
    sys.exit(f"FREEPIK_API_KEY not set (env or {ENV_FILE})")


def api(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"x-freepik-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Freepik API {method} {url} -> {e.code}: {e.read().decode()[:300]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", default=".")
    ap.add_argument("--scale", default="2x")        # 2x / 4x / 8x / 16x (engine-dependent)
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    key = load_key()

    with open(a.image, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    submit = api("POST", BASE, key, {"image": img_b64, "scale_factor": a.scale})
    task = (submit.get("data") or {})
    task_id = task.get("task_id") or task.get("id")
    if not task_id:
        sys.exit(f"No task_id in response: {json.dumps(submit)[:300]}")
    print(f"task {task_id} submitted; polling…", file=sys.stderr)

    deadline = time.time() + a.timeout
    while time.time() < deadline:
        time.sleep(5)
        st = api("GET", f"{BASE}/{task_id}", key)
        d = st.get("data") or {}
        status = (d.get("status") or "").upper()
        if status in ("COMPLETED", "SUCCESS", "DONE"):
            urls = d.get("generated") or d.get("images") or []
            if isinstance(urls, str):
                urls = [urls]
            if not urls:
                sys.exit(f"Completed but no output URL: {json.dumps(d)[:300]}")
            stamp = int(time.time())
            for i, u in enumerate(urls):
                out = os.path.join(a.out, f"upscaled-{stamp}-{i}.png")
                urllib.request.urlretrieve(u, out)
                print(out)
            return
        if status in ("FAILED", "ERROR"):
            sys.exit(f"Upscale failed: {json.dumps(d)[:300]}")
        print(f"  status={status or '?'}…", file=sys.stderr)
    sys.exit("Timed out waiting for upscale")


if __name__ == "__main__":
    main()
