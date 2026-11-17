---
name: feature-dev-workflow
description: Standard development workflow for media-studio engineering tasks. Covers TDD verification, CodeGraph synchronization, sub-600-line module ceilings, and clean commit standards.
---

# Feature Development Workflow

## 1. Test-Driven Development (TDD)
- Always write the failing test first (`RED`) and verify the failure reason before writing implementation code (`GREEN`).
- All unit tests under `tests/` must run 100% offline (`pytest -m "not slow"`).

## 2. Module Boundaries & Linter Gate
- Keep every Python module in `src/media_studio/` strictly under 600 lines.
- Verify `ruff check src tests` and `ruff format --check src tests` pass with 0 errors before committing.

## 3. CodeGraph Synchronization
- Execute `codegraph sync` after adding or modifying symbols so codebase intelligence stays fresh.
