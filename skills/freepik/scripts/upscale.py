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


def save_unique(out_dir: str, base: str, blob: bytes) -> str:
    """Write blob under a collision-proof name.

    O_EXCL ("xb") makes creation atomic, so two invocations landing in the
    same second in the same directory can never overwrite each other —
    the loser bumps the suffix and retries.
    """
    n = 0
    while True:
        suffix = "" if n == 0 else f"-{n}"
        path = os.path.join(out_dir, f"{base}{suffix}.png")
        try:
            with open(path, "xb") as f:
                f.write(blob)
            return path
        except FileExistsError:
            n += 1


def api(method, url, key, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"x-freepik-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
    while True:
        # Never sleep or block past the deadline: cap the sleep to the time
        # remaining, re-check after waking, and bound the GET the same way.
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(5, remaining))
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        st = api("GET", f"{BASE}/{task_id}", key, timeout=min(60, max(1, remaining)))
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
                with urllib.request.urlopen(u, timeout=120) as r:
                    blob = r.read()
                print(save_unique(a.out, f"upscaled-{stamp}-{i}", blob))
            return
        if status in ("FAILED", "ERROR"):
            sys.exit(f"Upscale failed: {json.dumps(d)[:300]}")
        print(f"  status={status or '?'}…", file=sys.stderr)
    sys.exit("Timed out waiting for upscale")


if __name__ == "__main__":
    main()
