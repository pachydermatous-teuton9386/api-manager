# Security model

Read this before adding any feature that touches the password, values, or the HTTP server. The short version is in the main README; this doc is the version for contributors.

## What this tool is

A **management workflow** for API keys scattered across `.env` files — discovery, validation, rotation, audit. The password is a **screen guard** against shoulder-surfing.

## What this tool is NOT

- A secret vault. If you want secret-at-rest protection, use [age](https://github.com/FiloSottile/age), [sops](https://github.com/getsops/sops), 1Password CLI, or a cloud KMS.
- A network service. The HTTP server binds to `127.0.0.1` only.
- A replacement for proper key-rotation discipline. It makes the rotation *easier*, not automatic.

## Threat model

**What we protect against:**

- Someone glancing at your screen while a .env file is loaded → values are masked to last 4 chars by default
- Accidentally copying a plaintext key to the clipboard when you meant to copy the masked form → reveal is an explicit, separate action
- Writing to the wrong file by fat-fingering a path → atomic writes, backups before every mutation, path validation rejects non-.env files
- Losing track of when a key was rotated → audit log captures every write
- Forgetting which files share the same key → cross-file search shows everything at once

**What we DO NOT protect against:**

- Anyone with shell access on your machine (they can `cat` the files directly)
- Malware with file-read permissions (same)
- A keylogger capturing the unlock password (same)
- A malicious browser extension reading the localhost HTTP responses (localhost is not isolated from the browser)
- Someone with your backup drives (the `.env` files are in the backups just like the originals)

If any of those are in your threat model, you need a real secrets vault, not this tool.

## Password rules

1. **Default password exists** for zero-friction first-run, and is documented publicly. Users MUST override it via `API_MANAGER_PASSWORD` for any real use.
2. **Constant-time comparison** via `hmac.compare_digest`. Never use `==` on the password.
3. **Header-based auth** via `X-Unlock-Password`. Never put the password in a query string (shows up in server logs and browser history).
4. **Memory only in the UI.** The JS stores the password in a closure variable. Never `localStorage`, never `sessionStorage`, never cookies. Page refresh = re-lock.
5. **Server-side idle timeout** of 5 minutes. Even if the browser held the password, the server will reject it after idle.
6. **Never log or echo the password.** Not in error messages, not in stderr, not in audit events. Audit logs record *that* an unlock happened, not the password itself.

## Write gating

**Writes (add/update/delete) are NOT gated by the unlock password.** This is a deliberate design choice. If you can access the file path, you can already read and write it directly via the filesystem. Gating the API would be theater.

If you believe writes should be gated, open an issue first to discuss. The likely outcome is a new `STRICT_MODE` env var that opts into gated writes for users whose threat model differs from the default.

## Value reveal rules

1. **Reading any plaintext value requires the unlock password.** This covers `/api/env` (when unlocked), `/api/find-key`, `/api/validate`, the MCP `get_key` tool, and the MCP `find` tool with a password.
2. **The masked form is always safe to return.** It's derived by `mask(value)` and preserves only the last 4 characters (or all characters if length ≤ 8, replaced by dots). Masked values can appear in locked responses, audit logs, and error messages.
3. **Never return a raw value in an error message.** If a validator rejects a key as malformed, return "invalid key format" — not "received 'sk-ant-abc123' which is malformed."

## Atomic writes + backups

Every mutation MUST:

1. Call `backup_file(path)` before the mutation
2. Use `atomic_write(path, content)` for the actual write (writes to `.tmp`, then `os.replace`)
3. Call `audit_log("write" | "delete" | "rotate", file_path=path, key_name=key)` after

These three steps are the recoverability guarantees. Any code path that modifies a .env file without all three is a bug.

## What to do if you find a security issue

Email the author (see GitHub profile) privately before filing a public issue. Give us 30 days to fix before disclosure. If it's a doozy (RCE, unauthenticated secrets dump, etc.), we'll move fast.
