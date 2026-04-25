# Agent Harness CLI Instructions

- Keep the scaffold dependency-free unless a new dependency is clearly justified.
- Prefer deterministic checks before model-based evaluators.
- Every check command must print structured check-result JSON to stdout.
- Check scripts should be narrow: one behavioral concern per file.
- Keep sample tasks and check scripts out of the core project tree unless explicitly requested.
- Add or update `unittest` tests when changing runner behavior.
