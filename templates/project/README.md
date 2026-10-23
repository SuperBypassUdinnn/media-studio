# {{title}}

Slug: `{{slug}}`

A media project described by `project.yaml`. Edit the manifest for intent, keep generated
files out of the repository, and let the toolkit produce the deliverables.

## Layout

```
project.yaml     single source of truth: deliverables, timeline, brand, budgets
sources/         raw footage and recordings, never overwritten
assets/          staged, licensed assets with sidecar .license files
scripts/         project-specific generators
out/frames/      scratch frame sequences, purged after encode
out/video/       video deliverables
out/images/      design deliverables
out/reports/     QC reports and contact sheets
docs/            project notes and revision history
```

## Reproduce

```bash
ms doctor                 # toolchain check
ms doctor --deep          # adds GPU, torch virtualenvs, and disk space
```

The video, 3D, design, and asset workflows arrive in phase 1. Until then this project is a
manifest plus directories.

## Rules

- Every asset carries a `.license` sidecar; `CREDITS.md` is generated, never hand-edited.
- Nothing under `out/` is committed; deliverables are published elsewhere.
- `project.yaml` wins over generated files. Re-run the generator instead of editing output.
