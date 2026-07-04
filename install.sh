#!/usr/bin/env bash
#
# Symlink every skill in this repo into an agent skills directory.
#
# Usage:
#   ./install.sh                       # → ~/.claude/skills   (Claude Code)
#   ./install.sh ~/.agents/skills      # → a custom target    (OpenClaw, etc.)
#
# Idempotent + safe: replaces a stale symlink, skips a real (non-symlink)
# directory of the same name so it never clobbers unrelated skills.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$HOME/.claude/skills}"

mkdir -p "$TARGET"
echo "Installing skills from $REPO → $TARGET"

for dir in "$REPO"/*/; do
  name="$(basename "$dir")"
  [ -f "$dir/SKILL.md" ] || continue          # only real skill folders
  link="$TARGET/$name"

  if [ -L "$link" ]; then
    rm -f "$link"                              # replace a stale/old symlink
  elif [ -e "$link" ]; then
    echo "  skip $name — a real (non-symlink) entry already exists at $link"
    continue
  fi

  ln -s "${dir%/}" "$link"
  echo "  linked $name"
done

echo "Done. On a new machine: git clone this repo, then run ./install.sh"
