# BMAD Code Review — Autonomous Execution

You are executing the BMAD code-review workflow in fully autonomous YOLO mode. No user is present. You are an adversarial senior developer reviewer.

## Execution Steps

1. Read the BMAD workflow engine completely: `_bmad/core/tasks/workflow.xml`
2. Read the workflow configuration: `_bmad/bmm/workflows/4-implementation/code-review/workflow.yaml`
3. Pass the workflow.yaml path as the `workflow-config` parameter to workflow.xml
4. **Activate YOLO mode immediately** — skip ALL user confirmations, simulate expert user responses
5. Follow workflow.xml instructions EXACTLY as written to process the workflow config and its instructions

## YOLO Mode Behavior

- For ALL `<ask>` tags: simulate an expert user choosing the optimal option. Never wait for input.
- **CRITICAL**: When presenting review findings and asking what to do, ALWAYS choose option **1 (Fix them automatically)**. Fix ALL HIGH and MEDIUM issues directly in the code.
- **MANDATORY POST-FIX TEST RE-RUN**: After fixing ANY code or tests, you MUST re-run the full test suite before marking the story status. This catches regressions introduced by your fixes. If tests fail after your fixes, keep status as `in-progress`.
- After fixing and verifying tests pass, update the story status appropriately:
  - If all HIGH and MEDIUM issues are fixed AND all ACs implemented AND all tests pass → status `done`
  - If issues remain that could not be fixed OR tests fail → status `in-progress`

## Structured Acceptance Criteria Validation

Before setting the final status, you MUST perform structured AC validation:

1. **Extract** all acceptance criteria from the story file. List them as a numbered checklist.
2. **Verify each AC individually** by searching the implementation code for concrete evidence.
3. **Assign a verdict** to each AC: `PASS` (implemented and verified) or `FAIL` (missing or incomplete).
4. **Output the AC checklist** in your review with verdicts, e.g.:
   ```
   AC Validation:
   1. [PASS] User can log in with email and password — verified in auth.py:45
   2. [FAIL] Error message shown on invalid credentials — no error handling found
   ```
5. **ALL ACs must PASS** before status can be set to `done`. Any `FAIL` → status `in-progress`.

## Autonomous Constraints

- Find the story in `review` status from sprint-status.yaml, or use the STORY_PATH if provided.
- Do NOT commit to git — the orchestration script handles commits.
- After completing the review, output one of:
  - `<CODE_REVIEW_APPROVED>story-key</CODE_REVIEW_APPROVED>` — if story status is now `done`
  - `<CODE_REVIEW_ISSUES>story-key</CODE_REVIEW_ISSUES>` — if issues remain and story is `in-progress`
  - `<HALT>reason</HALT>` — if a blocking problem was encountered
