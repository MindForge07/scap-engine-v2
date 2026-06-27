# Errors

## ERR-20260627-001: save_experience double-lock race condition
- **Name**: save-experience-race
- **Priority**: high
- **Area**: backend
- **Date**: 2026-06-27
- **Summary**: `save_experience()` had two separate `with self._lock:` blocks — one for ID generation and one for the INSERT. Between the two blocks, another thread could acquire the lock and generate the same ID.
- **Error text**: `INSERT OR REPLACE` silently overwrote the first thread's data
- **Context**: 20-thread concurrent write test revealed only 51 unique IDs out of 1000 expected
- **Suggested fix**: Merge into a single `with self._lock:` block covering both ID generation and INSERT
- **Reproducible**: yes
- **Status**: fixed

## ERR-20260627-002: CLI search with empty project returns zero results
- **Name**: empty-project-search
- **Priority**: high
- **Area**: cli
- **Date**: 2026-06-27
- **Summary**: `search()` without `--project` passes `project=""` to FTS5 query with `AND project = ?`. Since no record has `project = ""`, all queries return empty.
- **Error text**: (silent — returns 0 results)
- **Context**: CLI `scap search "test"` without `-p` flag
- **Suggested fix**: Make the project clause conditional: only add `AND project = ?` when project is non-empty
- **Reproducible**: yes
- **Status**: fixed
