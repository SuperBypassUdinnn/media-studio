# Agent Skills Catalog — media-studio

This directory contains project-local **agent skills** for media-studio. Each skill provides instructions and workflows for AI agents.

## Directory Structure

```text
.agents/skills/
├── README.md                      ← Catalog and execution guide
└── feature-dev-workflow/
    └── SKILL.md                   ← Contract-first feature development pipeline
```

## Agent Execution Steps
1. Read this `README.md` to identify the skill matching your current task.
2. Read `<skill>/SKILL.md` to understand the step-by-step workflow.
3. Execute Step -3 (Backlog Overview) before starting feature implementations.

## Skill Catalog

| Skill | Trigger Keywords | Directory | Description |
| :--- | :--- | :--- | :--- |
| **feature-dev-workflow** | `feature`, `new feature`, `endpoint`, `migration`, `screen`, `API` | [`feature-dev-workflow/`](feature-dev-workflow/SKILL.md) | Contract-first feature development pipeline from backlog to PR |
