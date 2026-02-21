#!/usr/bin/env bash
#
# run-stories.sh — BMAD-aligned autonomous story implementation loop
#
# Runs the full BMAD story cycle per story with fresh Claude sessions:
#   Create Story (CS) → Dev Story (DS) → Code Review (CR) → [fix loop] → commit
#
# Each step uses the real BMAD workflow engine (workflow.xml) in YOLO mode.
#
# Usage:
#   ./run-stories.sh                        # Run all remaining stories
#   ./run-stories.sh --max-stories 3        # Stop after 3 stories
#   ./run-stories.sh --review-model sonnet  # Use different model for code review
#   ./run-stories.sh --dry-run              # Show what would run without executing
#

set -euo pipefail

# --- Configuration ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SPRINT_STATUS="$PROJECT_DIR/_bmad-output/implementation-artifacts/sprint-status.yaml"
IMPL_DIR="$PROJECT_DIR/_bmad-output/implementation-artifacts"
LOG_DIR="$IMPL_DIR/logs"

PROMPT_CS="$PROJECT_DIR/PROMPT-create-story.md"
PROMPT_DS="$PROJECT_DIR/PROMPT-dev-story.md"
PROMPT_CR="$PROJECT_DIR/PROMPT-code-review.md"

MAX_STORIES=999
MAX_TURNS_CS=100    # Create story needs fewer turns
MAX_TURNS_DS=200    # Dev story needs the most
MAX_TURNS_CR=150    # Code review + auto-fix
MAX_REVIEW_ROUNDS=3 # Max CS→DS→CR→DS→CR... rounds before giving up
DRY_RUN=false
DEV_MODEL=""        # empty = use default
REVIEW_MODEL=""     # empty = use default (recommend different model)


# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --max-stories)
      MAX_STORIES="$2"
      shift 2
      ;;
    --max-turns-ds)
      MAX_TURNS_DS="$2"
      shift 2
      ;;
    --max-review-rounds)
      MAX_REVIEW_ROUNDS="$2"
      shift 2
      ;;
    --dev-model)
      DEV_MODEL="$2"
      shift 2
      ;;
    --review-model)
      REVIEW_MODEL="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Runs the full BMAD story cycle: Create Story → Dev Story → Code Review"
      echo "Each step runs in a fresh Claude Code session using real BMAD workflows."
      echo ""
      echo "Options:"
      echo "  --max-stories N        Stop after N stories (default: unlimited)"
      echo "  --max-turns-ds N       Max turns for dev-story sessions (default: 200)"
      echo "  --max-review-rounds N  Max dev→review rounds per story (default: 3)"
      echo "  --dev-model MODEL      Model for create-story and dev-story (default: system default)"
      echo "  --review-model MODEL   Model for code review — use a DIFFERENT model (default: system default)"
      echo "  --dry-run              Show what would run without executing"
      echo "  -h, --help             Show this help"
      echo ""
      echo "Examples:"
      echo "  $0                                    # Run all stories with defaults"
      echo "  $0 --max-stories 1                    # Run just the next story"
      echo "  $0 --dev-model opus --review-model sonnet  # Different models for dev vs review"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# --- Preflight checks ---
if ! command -v claude &> /dev/null; then
  echo "ERROR: 'claude' command not found. Install Claude Code first."
  exit 1
fi

for f in "$PROMPT_CS" "$PROMPT_DS" "$PROMPT_CR" "$SPRINT_STATUS"; do
  if [[ ! -f "$f" ]]; then
    echo "ERROR: Required file not found: $f"
    exit 1
  fi
done

mkdir -p "$LOG_DIR"

# --- Helper: get story status from sprint-status.yaml ---
get_story_status() {
  local key="$1"
  grep -E "^\s+${key}:" "$SPRINT_STATUS" | sed 's/.*: *//' | tr -d ' ' || echo "unknown"
}

# --- Helper: find next backlog story key ---
next_backlog_story() {
  grep -E '^\s+[0-9]+-[0-9]+-.*:\s*backlog' "$SPRINT_STATUS" | head -1 | sed 's/:.*//' | tr -d ' ' || echo ""
}

# --- Helper: run a Claude session ---
run_claude() {
  local prompt_file="$1"
  local log_file="$2"
  local max_turns="$3"
  local model="$4"
  local extra_prompt="${5:-}"

  local prompt_content
  prompt_content="$(cat "$prompt_file")"

  # Append extra context (e.g., STORY_PATH) if provided
  if [[ -n "$extra_prompt" ]]; then
    prompt_content="${prompt_content}

${extra_prompt}"
  fi

  local cmd=(
    claude
    -p "$prompt_content"
    --max-turns "$max_turns"
    --output-format stream-json
    --dangerously-skip-permissions
  )

  if [[ -n "$model" ]]; then
    cmd+=(--model "$model")
  fi

  set +e
  "${cmd[@]}" > "$log_file" 2>&1
  local exit_code=${PIPESTATUS[0]}
  set -e

  # Print the final assistant text to stdout for terminal visibility
  if command -v jq &> /dev/null && [[ -f "$log_file" ]]; then
    jq -r 'select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text' "$log_file" 2>/dev/null | tail -1
  fi

  return $exit_code
}

# --- Helper: extract story ID (X.Y) from key like 1-2-some-title ---
story_id_from_key() {
  local key="$1"
  local epic_num story_num
  epic_num=$(echo "$key" | cut -d'-' -f1)
  story_num=$(echo "$key" | cut -d'-' -f2)
  echo "${epic_num}.${story_num}"
}

# ============================================================
#  MAIN LOOP
# ============================================================

echo "========================================================"
echo "  BMAD Story Runner"
echo "========================================================"
echo "Project:           $PROJECT_DIR"
echo "Max stories:       $MAX_STORIES"
echo "Max review rounds: $MAX_REVIEW_ROUNDS"
echo "Dev model:         ${DEV_MODEL:-default}"
echo "Review model:      ${REVIEW_MODEL:-default}"
echo "Dry run:           $DRY_RUN"
echo ""

STORY_COUNT=0

for i in $(seq 1 "$MAX_STORIES"); do

  # --- Find next story ---
  STORY_KEY=$(next_backlog_story)

  if [[ -z "$STORY_KEY" ]]; then
    echo ""
    echo "No more backlog stories. All stories have been created or completed."
    break
  fi

  STORY_ID=$(story_id_from_key "$STORY_KEY")
  STORY_FILE="$IMPL_DIR/${STORY_KEY}.md"
  TIMESTAMP=$(date '+%Y%m%d-%H%M%S')

  echo ""
  echo "========================================================"
  echo "  Story $i: $STORY_KEY (${STORY_ID})"
  echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "========================================================"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] Would execute:"
    echo "  1. Create Story:  claude -p PROMPT-create-story.md"
    echo "  2. Dev Story:     claude -p PROMPT-dev-story.md"
    echo "  3. Code Review:   claude -p PROMPT-code-review.md"
    echo "  4. Git commit"
    STORY_COUNT=$((STORY_COUNT + 1))
    continue
  fi

  # --------------------------------------------------------
  # STEP 1: CREATE STORY (CS)
  # --------------------------------------------------------
  echo ""
  echo "--- Step 1/3: Create Story ($STORY_KEY) ---"
  LOG_CS="$LOG_DIR/${TIMESTAMP}_${STORY_KEY}_1-create-story.log"

  if ! run_claude "$PROMPT_CS" "$LOG_CS" "$MAX_TURNS_CS" "$DEV_MODEL"; then
    echo "ERROR: Create Story failed (exit code $?). Check log: $LOG_CS"
    break
  fi

  # Verify story was created
  STATUS=$(get_story_status "$STORY_KEY")
  if [[ "$STATUS" != "ready-for-dev" ]]; then
    echo "ERROR: Expected status 'ready-for-dev' after create-story, got '$STATUS'"
    echo "Check log: $LOG_CS"
    break
  fi

  echo "Create Story complete. Status: ready-for-dev"

  # --------------------------------------------------------
  # STEP 2-3: DEV STORY + CODE REVIEW LOOP
  # --------------------------------------------------------
  STORY_DONE=false

  for round in $(seq 1 "$MAX_REVIEW_ROUNDS"); do
    echo ""
    echo "--- Step 2/3: Dev Story ($STORY_KEY) [round $round/$MAX_REVIEW_ROUNDS] ---"
    LOG_DS="$LOG_DIR/${TIMESTAMP}_${STORY_KEY}_2-dev-story-r${round}.log"

    # On first round, dev-story auto-discovers from sprint-status (ready-for-dev).
    # On subsequent rounds, the story is in-progress, so pass the path explicitly.
    EXTRA=""
    if [[ $round -gt 1 ]]; then
      EXTRA="STORY_PATH: ${STORY_FILE}"
    fi

    if ! run_claude "$PROMPT_DS" "$LOG_DS" "$MAX_TURNS_DS" "$DEV_MODEL" "$EXTRA"; then
      echo "ERROR: Dev Story failed (exit code $?). Check log: $LOG_DS"
      STORY_DONE=false
      break 2  # break out of both loops
    fi

    # Check for HALT
    if jq -e 'select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text | test("^HALT:")' "$LOG_DS" &>/dev/null; then
      echo "Dev Story HALTed. Check log: $LOG_DS"
      break 2
    fi

    # Verify status moved to review
    STATUS=$(get_story_status "$STORY_KEY")
    if [[ "$STATUS" != "review" ]]; then
      echo "WARNING: Expected status 'review' after dev-story, got '$STATUS'"
      echo "Check log: $LOG_DS"
      # Continue anyway — code review might still work
    fi

    echo "Dev Story complete. Status: $STATUS"

    # --------------------------------------------------
    # CODE REVIEW (CR) — ideally different model
    # --------------------------------------------------
    echo ""
    echo "--- Step 3/3: Code Review ($STORY_KEY) [round $round/$MAX_REVIEW_ROUNDS] ---"
    LOG_CR="$LOG_DIR/${TIMESTAMP}_${STORY_KEY}_3-code-review-r${round}.log"

    CR_EXTRA="STORY_PATH: ${STORY_FILE}"

    if ! run_claude "$PROMPT_CR" "$LOG_CR" "$MAX_TURNS_CR" "$REVIEW_MODEL" "$CR_EXTRA"; then
      echo "ERROR: Code Review failed (exit code $?). Check log: $LOG_CR"
      break 2
    fi

    # Check review outcome
    STATUS=$(get_story_status "$STORY_KEY")
    echo "Code Review complete. Status: $STATUS"

    if [[ "$STATUS" == "done" ]]; then
      STORY_DONE=true
      break
    fi

    if [[ $round -lt $MAX_REVIEW_ROUNDS ]]; then
      echo "Code review found issues. Running dev-story again (round $((round + 1)))..."
    else
      echo "WARNING: Max review rounds ($MAX_REVIEW_ROUNDS) reached. Story not fully approved."
    fi
  done

  # --------------------------------------------------------
  # STEP 4: COMMIT (if story is done)
  # --------------------------------------------------------
  if [[ "$STORY_DONE" == "true" ]]; then
    echo ""
    echo "--- Committing: Story $STORY_ID ($STORY_KEY) ---"

    cd "$PROJECT_DIR"
    git add -A

    # Generate a meaningful commit message from the actual changes
    COMMIT_MSG=$(claude -p "$(cat <<PROMPT
Generate a git commit message for this story implementation.

Story ID: ${STORY_ID}
Story key: ${STORY_KEY}
Story file: ${STORY_FILE}

Rules:
- First line: feat(story-${STORY_ID}): <concise description of what was built>
- Empty line, then 3-6 bullet points summarizing the key changes
- End with: Co-Authored-By: Claude <noreply@anthropic.com>
- Keep it factual — describe what was implemented, not the process
- Max 20 words for the first line

Read the story file at ${STORY_FILE} and run 'git diff --cached --stat' to understand what changed.
Output ONLY the commit message, nothing else.
PROMPT
    )" --max-turns 5 --dangerously-skip-permissions 2>/dev/null)

    # Fallback to generic message if generation fails
    if [[ -z "$COMMIT_MSG" || $? -ne 0 ]]; then
      COMMIT_MSG="$(cat <<EOF
feat(story-${STORY_ID}): implement ${STORY_KEY}

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
      )"
    fi

    git commit -m "$COMMIT_MSG"

    echo "Committed: story-${STORY_ID}"
    STORY_COUNT=$((STORY_COUNT + 1))
  else
    echo ""
    echo "Story $STORY_KEY is NOT done (status: $(get_story_status "$STORY_KEY"))."
    echo "Stopping. Review logs in: $LOG_DIR"
    break
  fi

  echo ""
  echo "Pausing 5 seconds before next story..."
  sleep 5
done

echo ""
echo "========================================================"
echo "  Session Complete"
echo "========================================================"
echo "Stories completed: $STORY_COUNT"
echo "Logs directory:    $LOG_DIR"
echo ""
echo "Next steps:"
echo "  - git log --oneline    # Review commits"
echo "  - cat $SPRINT_STATUS   # Check sprint status"
echo "  - ./run-stories.sh     # Continue with next stories"
