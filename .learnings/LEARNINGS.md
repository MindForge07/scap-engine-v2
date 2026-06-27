# Learnings

## LRN-20260627-001: Ollama API returns 502 via httpx but works with requests
- **Category**: tooling
- **Priority**: high
- **Area**: backend
- **Date**: 2026-06-27
- **Summary**: On Windows (Python 3.14, TRAE environment), `httpx.post()` to Ollama's `/api/generate` returns HTTP 502 with empty body, but `requests.post()` with identical parameters returns 200 with valid data.
- **Details**: Tested both libraries with the same URL, headers, and JSON payload. httpx consistently returned 502 on this system. Root cause unclear — possibly httpx's HTTP/2 negotiation or connection pooling behaves differently with Ollama's embedded HTTP server.
- **Suggested action**: Always use `requests` library for Ollama API calls on Windows. Never use httpx for local model APIs.
- **Source**: SCAP v2 benchmark experiment
- **Tags**: ollama,httpx,requests,windows

## LRN-20260627-002: qwen3 thinking models output NDJSON even with stream:false
- **Category**: tooling
- **Priority**: high
- **Area**: backend
- **Date**: 2026-06-27
- **Summary**: Ollama's qwen3 model (with thinking/reasoning) returns multiple newline-delimited JSON objects even when `stream: false` is set. Each line has `response`, `thinking`, and `done` fields. The `response` text only appears in later lines after the thinking phase.
- **Details**: The `thinking` field contains the chain-of-thought reasoning. The `response` field is empty during thinking and only populated after thinking completes. With small `num_predict` (e.g., 50), the model may exhaust all tokens on thinking and never produce a response. Need `num_predict >= 2048` for reliable output.
- **Suggested action**: When calling Ollama with thinking models: (1) Use `requests`, not httpx. (2) Parse response as NDJSON (iterate lines). (3) Concatenate all `response` fields. (4) Set `num_predict >= 8192` for solving tasks.
- **Source**: SCAP v2 benchmark experiment
- **Tags**: ollama,qwen3,ndjson,thinking

## LRN-20260627-003: FTS5 virtual tables don't support ALTER TABLE ADD COLUMN
- **Category**: correction
- **Priority**: medium
- **Area**: backend
- **Date**: 2026-06-27
- **Summary**: SQLite FTS5 virtual tables cannot be modified with ALTER TABLE ADD COLUMN. If you add a new column to the FTS5 schema definition, existing databases keep the old schema because `CREATE VIRTUAL TABLE IF NOT EXISTS` doesn't alter existing tables.
- **Details**: In `sqlite_store.py`, the FTS5 table was defined with `compression_instruction` but the production database was created before that column was added. The `initialize()` method's `CREATE IF NOT EXISTS` was a no-op for the existing table. All FTS writes that included `compression_instruction` failed with "no column named compression_instruction".
- **Suggested action**: Add a migration check: try `SELECT new_column FROM fts_table LIMIT 1`. If it fails with OperationalError, `DROP TABLE` and recreate, then `rebuild_fts()`. This is safe because FTS5 data is derived from the source table.
- **Source**: SCAP v2 bug fix
- **Tags**: sqlite,fts5,migration,schema

## LRN-20260627-004: Windows rich library + GBK terminal encoding crash
- **Category**: tooling
- **Priority**: medium
- **Area**: cli
- **Date**: 2026-06-27
- **Summary**: On Windows with GBK terminal encoding, `rich` library crashes with `UnicodeEncodeError: 'gbk' codec can't encode character '✓'` when printing Unicode checkmarks or special characters.
- **Details**: The `rich.console.Console` uses the terminal's default encoding on Windows, which is often GBK (Chinese locale). Unicode characters like ✓ (U+2713) are not in the GBK character set.
- **Suggested action**: At the top of CLI entry points on Windows, add: `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` before importing rich.
- **Source**: SCAP v2 CLI development
- **Tags**: windows,encoding,rich,cli
