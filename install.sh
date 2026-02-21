#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Validate prerequisites ---------------------------------------------------

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: 'uv' is required but not found. See https://docs.astral.sh/uv/" >&2
  exit 1
fi

for f in pyproject.toml run_stories/cli.py; do
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
echo "  Package source: $SCRIPT_DIR"

# --- Compute relative path from target to this package ------------------------

relpath() {
  # Compute relative path from $1 to $2 using python (portable)
  python3 -c "import os.path; print(os.path.relpath('$2', '$1'))"
}

REL_PATH="$(relpath "$TARGET_DIR" "$SCRIPT_DIR")"
echo "  Relative path:  $REL_PATH"

# --- Copy prompt files --------------------------------------------------------

PROMPTS=(PROMPT-create-story.md PROMPT-dev-story.md PROMPT-code-review.md)
for p in "${PROMPTS[@]}"; do
  cp "$SCRIPT_DIR/$p" "$TARGET_DIR/$p"
  echo "  Copied $p"
done

# --- Generate run-stories wrapper ---------------------------------------------

WRAPPER="$TARGET_DIR/run-stories"
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
command -v uv >/dev/null 2>&1 || { echo "Error: 'uv' is required but not found. See https://docs.astral.sh/uv/" >&2; exit 1; }
exec uv run --project "\$(dirname "\$0")/$REL_PATH" run-stories "\$@"
EOF
chmod +x "$WRAPPER"
echo "  Created run-stories wrapper"

# --- Done ---------------------------------------------------------------------

echo ""
echo "Done! From your project root you can now run:"
echo "  cd $TARGET_DIR"
echo "  ./run-stories --help"
