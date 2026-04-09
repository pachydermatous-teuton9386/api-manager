# Repository Agent Guide

This file is the single source of truth for contributors (human and AI) working on `api-manager`. `CLAUDE.md` is a symlink to this file so Claude Code, Cursor, and any other agent harness all read the same guide.

## Core principles

`api-manager` is a **single-file Python tool** with **no dependencies beyond stdlib**. Every design decision flows from that constraint:

- **One file.** All the code lives in `api-manager.py`. HTML, CSS, and JavaScript are embedded. No separate templates, no asset pipeline, no build step. If you find yourself wanting to split the file, stop and re-read this principle.
- **Stdlib only.** No `pip install` dependencies in the runtime path. You can use `http.server`, `sqlite3`, `urllib.request`, `hmac`, `json`, `re`, `webbrowser`, etc. You cannot use `requests`, `flask`, `fastapi`, `pydantic`, or anything else that needs a wheel.
- **Localhost only.** The web server binds to `127.0.0.1`. Never add network-reachable functionality.
- **Local only.** Nothing in this tool ever leaves the user's machine. No telemetry, no error reporting pings, no "phone home" updates. The audit log is a local SQLite file and stays that way.
- **Stable wire formats.** The `/api/*` endpoints and the MCP tool schemas are part of the public contract. Changing a response shape breaks users downstream. Add new fields; don't rename or remove existing ones without a major version bump.

If any proposed change violates these, it's the wrong change — not the principles.

## Rules index

> **BEFORE writing any code or making changes, you MUST read the relevant rule files from the table below.** Identify which areas your task touches and read those rule files first. Skipping this step leads to avoidable mistakes and rework.

| File | Read when... |
|---|---|
| [rules/adding-services.md](rules/adding-services.md) | Adding a new service to the `SERVICES` list, adding a validator function, or debugging service detection |
| [rules/error-classification.md](rules/error-classification.md) | Adding a new throw site, debugging an error path, or wiring up error reporting |
| [rules/mcp-tools.md](rules/mcp-tools.md) | Adding/modifying MCP tools, changing the JSON-RPC dispatcher, or handling unlock in MCP mode |
| [rules/security-model.md](rules/security-model.md) | Touching the unlock password, the reveal logic, write gating, or anything value-handling |
| [rules/ui-conventions.md](rules/ui-conventions.md) | Modifying the embedded HTML/CSS/JS, adding a new panel, or adding a new API endpoint |

## Code layout

`api-manager.py` is organized top-to-bottom in this order:

1. **Module docstring** — user-facing documentation of what the tool does
2. **Imports** — stdlib only
3. **Config** — constants, paths, ports, unlock password
4. **Service catalog** — the `SERVICES` list + `detect_service()` + `service_summary()`
5. **Validation** — `_http_request()` + per-service validators + `VALIDATORS` dict + `validate_key()`
6. **Metadata sidecar** — `metadata_load/save/get/set/drop` on `~/.api-manager/metadata.json`
7. **Audit log** — SQLite init + `audit_log()` + `audit_recent()`
8. **Backups** — `backup_file()`
9. **`.env` parsing** — `parse_env`, `serialize_env`, `atomic_write`, `upsert`, `delete_key`, `mask`, `public_view`
10. **Unlock + idle timeout** — `is_unlocked()`, `_LAST_UNLOCK_ACTIVITY`
11. **Scan + cross-file search** — `scan_env_files`, `global_search`, `find_key_everywhere`
12. **HTML** — the `HTML` string constant (UI)
13. **HTTP server** — `Handler` class + `ReusableTCPServer`
14. **MCP server mode** — `MCP_TOOLS`, `mcp_call_tool()`, `run_mcp_server()`
15. **Main** — `main()` + `if __name__ == "__main__"`

When adding a new feature, put the new code in the matching section. Don't scatter related code across sections.

## Development

```bash
# Run the web UI
python3 api-manager.py

# Run as MCP stdio server
python3 api-manager.py --mcp

# Quick MCP test
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 api-manager.py --mcp
```

Per-user state lives in `~/.api-manager/`:

```
~/.api-manager/
├── metadata.json   per-key sidecar (service, first_seen, last_validated)
├── audit.db        SQLite event log
└── backups/        atomic file snapshots before every write
```

Delete that directory if you want a clean slate during development.

## Testing

There is currently no automated test suite. The tool is small enough that smoke-testing manually is fast:

1. Run the web UI, verify page loads at `http://127.0.0.1:8765`
2. Click scan, verify files are discovered
3. Load a file, verify service badges render
4. Click reveal, enter the password, verify plaintext values appear
5. Click validate on a known-good key, verify it returns `valid`
6. Run `python3 api-manager.py --mcp` with stdin JSON-RPC and verify `tools/list` + `list_files` respond cleanly

Contributions adding a real test suite are welcome, but the test suite itself must also respect the "stdlib only" constraint. No `pytest`. Use `unittest`.

## Git workflow

- Commits: clear, imperative, lowercase ("add supabase validator", not "Added Supabase Validator")
- Branches: `feat/<short-name>` for features, `fix/<short-name>` for fixes
- Pre-push: run the manual smoke test above
- PRs: describe what changed and why, link to any relevant rule file the change touches

## Project context

- **Author:** [Eric Yerke](https://agency-os.ai)
- **Built with:** Python 3.9+ stdlib + substantial Claude Code assistance
- **License:** MIT
- **Distribution goal:** one-command install (`curl -O ... && python3 api-manager.py`), minimum friction from "found on HN" to "using the tool"
