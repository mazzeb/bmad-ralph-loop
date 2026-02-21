# BMAD Create Story — Autonomous Execution

You are executing the BMAD create-story workflow in fully autonomous YOLO mode. No user is present. Make all decisions as an expert Scrum Master.

## Execution Steps

1. Read the BMAD workflow engine completely: `_bmad/core/tasks/workflow.xml`
2. Read the workflow configuration: `_bmad/bmm/workflows/4-implementation/create-story/workflow.yaml`
3. Pass the workflow.yaml path as the `workflow-config` parameter to workflow.xml
4. **Activate YOLO mode immediately** — this means: skip ALL user confirmations, simulate expert user responses for all `<ask>` tags, and auto-generate all `<template-output>` sections without pausing
5. Follow workflow.xml instructions EXACTLY as written to process the workflow config and its instructions
6. Save outputs after EACH section when generating documents from templates

## YOLO Mode Behavior

- For ALL `<ask>` tags: simulate an expert user choosing the optimal option. Never wait for input.
- For ALL `<template-output>` tags: generate content immediately and save to file, then continue to next step without pausing.
- For ALL confirmation prompts: auto-continue.
- Do NOT display menus or wait for choices — choose the best option and proceed.

## Autonomous Constraints

- If no backlog stories exist in sprint-status.yaml, output exactly `<NO_BACKLOG_STORIES/>` and stop.
- If a HALT condition triggers that cannot be resolved autonomously, output `<HALT>reason</HALT>` and stop.
- Do NOT commit to git — the orchestration script handles commits.
- After creating the story file and updating sprint-status.yaml, output `<CREATE_STORY_COMPLETE>story-key</CREATE_STORY_COMPLETE>` as the last line.
