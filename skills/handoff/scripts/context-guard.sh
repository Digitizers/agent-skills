#!/usr/bin/env bash
# Thin wrapper so hook configs can call a single .sh path. All logic lives in
# context-guard.py (same directory); stdin — the hook JSON payload — passes
# straight through to Python. No heredoc/process-substitution tricks: both
# mis-parse or silently no-op under macOS bash 3.2.
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/context-guard.py"
