#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Validate prerequisites ---------------------------------------------------

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: 'uv' is required but not found. See https://docs.astral.sh/uv/" >&2
  exit 1
fi

for f in pyproject.toml run_stories/cli.py run_stories/PROMPT-create-story.md; do
  if [[ ! -f "$SCRIPT_DIR/$f" ]]; then
    echo "Error: expected '$f' in $SCRIPT_DIR — is this the bmad-ralph-loop repo?" >&2
    exit 1
  fi
done

# --- Determine target directory -----------------------------------------------

TARGET_DIR="${1:-$(dirname "$SCRIPT_DIR")}"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"   # resolve to absolute

if [[ "$TARGET_DIR" == "$SCRIPT_DIR" ]]; then
  echo "Error: target directory is the same as the bmad-ralph-loop repo." >&2
  echo "Usage: $0 /path/to/your-project" >&2
  exit 1
fi

echo "Installing bmad-ralph-loop into: $TARGET_DIR"
echo "  Source: $SCRIPT_DIR"

# --- Copy Python package and project config into .run-stories/ ----------------

rm -rf "$TARGET_DIR/.run-stories"
mkdir -p "$TARGET_DIR/.run-stories"
cp -r "$SCRIPT_DIR/run_stories" "$TARGET_DIR/.run-stories/run_stories"
cp "$SCRIPT_DIR/pyproject.toml" "$TARGET_DIR/.run-stories/pyproject.toml"
echo "  Copied .run-stories/ (run_stories package + pyproject.toml)"

# --- Copy run-stories wrapper -------------------------------------------------

cp "$SCRIPT_DIR/run-stories" "$TARGET_DIR/run-stories"
chmod +x "$TARGET_DIR/run-stories"
echo "  Copied run-stories"

# --- Done ---------------------------------------------------------------------

echo ""
echo "Done! From your project root you can now run:"
echo "  cd $TARGET_DIR"
echo "  ./run-stories --help"
