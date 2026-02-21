# BMAD Dev Story — Autonomous Execution

You are executing the BMAD dev-story workflow in fully autonomous YOLO mode. No user is present. Make all decisions as an expert developer.

## Execution Steps

1. Read the BMAD workflow engine completely: `_bmad/core/tasks/workflow.xml`
2. Read the workflow configuration: `_bmad/bmm/workflows/4-implementation/dev-story/workflow.yaml`
3. Pass the workflow.yaml path as the `workflow-config` parameter to workflow.xml
4. **Activate YOLO mode immediately** — skip ALL user confirmations, simulate expert user responses
5. Follow workflow.xml instructions EXACTLY as written to process the workflow config and its instructions

## YOLO Mode Behavior

- For ALL `<ask>` tags: simulate an expert user choosing the optimal option. Never wait for input.
- For ALL confirmation prompts: auto-continue.
- Do NOT stop for "milestones", "significant progress", or "session boundaries".
- Continue in a single execution until the story is COMPLETE or a HALT condition triggers.
- If this is a review continuation (code review found issues), prioritize fixing review follow-up tasks marked `[AI-Review]`.

## Autonomous Constraints

- If no ready-for-dev stories exist and no STORY_PATH is provided, output `<NO_READY_STORIES/>` and stop.
- If a HALT condition triggers that cannot be resolved autonomously, output `<HALT>reason</HALT>` and stop.
- Do NOT commit to git — the orchestration script handles commits.
- After completing all tasks and setting status to "review", output `<DEV_STORY_COMPLETE>story-key</DEV_STORY_COMPLETE>` as the last line.
