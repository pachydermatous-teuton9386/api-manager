# api-manager

> A local API key vault and editor for every `.env` file on your machine.
> One Python file. Stdlib only. No `pip install`. No build step.
> Web UI at `http://127.0.0.1:8765` *and* an MCP server in the same file.

```bash
python3 api-manager.py        # web UI
python3 api-manager.py --mcp  # stdio MCP server
```

## I built this after finding 31 .env files on my machine

I scanned my home directory looking for `.env` files. I found **31 of them**, holding **85 unique API key names** across **19 different projects**. Most of them I'd forgotten existed.

Worse: 5 of those files were iCloud archive backups of an old project, all holding a stale OpenAI key I assumed I'd revoked months earlier. One project had literal placeholder values (`STRIPE_SECRET_KEY=your-stripe-key`) that would silently 500 at the first real checkout attempt. Two projects had the same Resend API key in different files — but I'd rotated one and not the other.

I built `api-manager` because nothing existed that would let me:

1. **See every .env file on my machine in one place**
2. **Know which service each key belongs to** without parsing the prefix manually
3. **Verify a key still works** without writing a curl command
4. **Rotate a key in 6 files at once** instead of editing each one by hand
5. **Find every place I'd accidentally pasted the same key**
6. **Manage all of this from Claude Code via natural language**

If you have more than ~3 projects with API keys, you probably have the same problem.

## What it does

- **Scans your home directory** and lists every `.env`, `.env.local`, `.env.production`, `.env.development`, `.env.staging`, and `.env.master`. Skips `node_modules`, `.git`, `Library`, and other noise dirs.
- **Auto-detects 26+ services** from key prefixes (`sk-ant-` → Anthropic, `sk-proj-` → OpenAI, `re_` → Resend, `fc-` → Firecrawl, `eyJ` → Supabase JWT, `ghp_` → GitHub PAT, `whsec_` → Stripe webhook, etc.) with colored badges per key.
- **Validates keys live** by calling the actual service API. Click the ✓ button on any key — Anthropic, OpenAI, Stripe, Resend, Firecrawl, GitHub, OpenRouter, Replicate, HuggingFace, Cloudflare, Vercel, Sentry, Linear, Notion, Slack are all supported. All free metadata endpoints, no cost per check.
- **Rotation workflow.** Click ↻ on any key. Opens the service's dashboard, you create a new key, paste it, pick which files to update (the tool finds every file the key lives in across your machine), and the rotation runs in one click. Optionally re-validates after.
- **Cross-file global search.** Type `OPENAI` and see every file where it appears, with drift highlighted automatically (multiple distinct values for the same key name).
- **Soft reveal password.** Values are masked by default (last 4 chars visible). Type the password once to reveal everything in plaintext. Click any value to copy. Auto-locks after 5 minutes idle.
- **Audit log.** Every read, write, validate, rotate, and unlock event is logged to a local SQLite at `~/.api-manager/audit.db`. *"When did I last change the Anthropic key?"* becomes answerable.
- **Atomic backups.** Every write snapshots the file to `~/.api-manager/backups/<timestamp>_<path>` first. If you fat-finger a value, you can recover.
- **MCP server mode.** Run with `--mcp` and the same tool exposes itself as a Model Context Protocol stdio server. Claude Code can then manage your keys via natural language: *"rotate the OpenAI key in pipelines/.env to this value"* or *"find every project that uses my old Anthropic key."*

## Install

```bash
# Single-file install — no dependencies
curl -O https://raw.githubusercontent.com/eyerke/api-manager/main/api-manager.py
chmod +x api-manager.py
python3 api-manager.py
```

That's it. No `pip install`. No `package.json`. No build step. Python 3.9+ is the only requirement.

## Quick start

```bash
python3 api-manager.py
```

The web UI opens at `http://127.0.0.1:8765`. Click **scan** to discover every `.env` file on your machine. Click any one to load it. Click **reveal**, type the password, and values become plaintext.

Default unlock password is `Ey5000!!@@`. Override with the `API_MANAGER_PASSWORD` environment variable:

```bash
API_MANAGER_PASSWORD='your-pw' python3 api-manager.py
```

## Using it from Claude Code (MCP mode)

Add to your `.mcp.json` or user MCP config:

```json
{
  "mcpServers": {
    "api-manager": {
      "command": "python3",
      "args": ["/absolute/path/to/api-manager.py", "--mcp"],
      "env": {
        "API_MANAGER_PASSWORD": "your-pw"
      }
    }
  }
}
```

Then in any Claude Code session:

> *"Find every .env file that has an OpenAI key"*
>
> *"Validate the Anthropic key in ~/Desktop/Websites/Plans/pipelines/.env"*
>
> *"Rotate the Stripe live key in my-project/.env to sk_live_..."*

The MCP server exposes 10 tools: `list_files`, `list_keys`, `get_key`, `set_key`, `delete_key`, `find`, `validate`, `rotate`, `list_services`, `audit_log`. Send a `tools/list` JSON-RPC request for the full schema.

## Supported services

| service | prefix detection | live validation | dashboard auto-open |
|---|---|---|---|
| Anthropic | `sk-ant-` | ✓ | ✓ |
| OpenAI | `sk-proj-`, `sk-` | ✓ | ✓ |
| Stripe (live/test) | `sk_live_`, `sk_test_` | ✓ | ✓ |
| Stripe publishable | `pk_live_`, `pk_test_` | — | ✓ |
| Stripe webhook | `whsec_` | — | ✓ |
| Resend | `re_` | ✓ | ✓ |
| Firecrawl | `fc-` | ✓ | ✓ |
| GitHub PAT | `ghp_`, `github_pat_`, `gho_`, etc. | ✓ | ✓ |
| OpenRouter | `sk-or-` | ✓ | ✓ |
| Replicate | `r8_` | ✓ | ✓ |
| HuggingFace | `hf_` | ✓ | ✓ |
| Cloudflare | (name hint) | ✓ | ✓ |
| Vercel | (name hint) | ✓ | ✓ |
| Sentry | `sntrys_` | ✓ | ✓ |
| Linear | `lin_api_` | ✓ | ✓ |
| Notion | `secret_`, `ntn_` | ✓ | ✓ |
| Slack (bot/user) | `xoxb-`, `xoxp-` | ✓ | ✓ |
| Supabase | `eyJ`, `sbp_`, `sbs_` | — | ✓ |
| PostHog | `phc_`, `phx_` | — | ✓ |
| Tavily | `tvly-` | — | ✓ |
| Brave Search | `BSA` | — | ✓ |
| Perplexity | `pplx-` | — | ✓ |
| Google AI | `AIza` | — | ✓ |
| Railway | (name hint) | — | ✓ |

Adding a new service is ~6 lines in the `SERVICES` list inside `api-manager.py`. PRs welcome.

## Architecture

Single Python file. No dependencies beyond stdlib (`http.server`, `sqlite3`, `urllib.request`, `hmac`, `json`, `re`, `webbrowser`). HTML + CSS + vanilla JS embedded in a constant. No build step. ~1,600 lines total including the embedded UI.

Per-user state lives in `~/.api-manager/`:

```
~/.api-manager/
├── metadata.json   per-key sidecar — service, first_seen, last_validated, validation_status
├── audit.db        SQLite event log
└── backups/        atomic file snapshots before every write
```

The web server binds to `127.0.0.1` only.

## Security model (read this)

**This tool is not a vault.** Anyone with shell access on your machine can `cat` your `.env` files directly — the password is a **screen guard against shoulder-surfing**, not encryption. If someone is already on your machine, they don't need the password to read your keys.

If you need real secret-at-rest protection, use [age](https://github.com/FiloSottile/age), [sops](https://github.com/getsops/sops), 1Password CLI, or your cloud provider's KMS. This tool is for the **management workflow** — discovery, validation, rotation — not for the protection layer.

Additional notes:

- The server binds to `127.0.0.1` only. Not reachable from the network.
- The unlock password uses constant-time comparison (`hmac.compare_digest`).
- The password is sent as an `X-Unlock-Password` HTTP header, never in a query string.
- In the web UI, the password is held in JavaScript memory only — never `localStorage`, never cookies. A page refresh re-locks it.
- Auto-lock fires after 5 minutes of inactivity on the server side.
- Writes are gated only in the sense that you have to know the file path. Adding/updating/deleting a key does NOT require the unlock password (same as editing the file directly).

## What it doesn't do

- **Not a vault.** See above.
- **Not a sync engine.** If you update `PIPELINES_RESEND_API_KEY` in one file, no other files change automatically. Use the rotation workflow when you want bulk updates — that's an *explicit* action, not magic.
- **Doesn't gate writes.** Add/update/delete work without unlocking. Only *visibility* of existing values is gated.
- **Doesn't store anything in the cloud.** Everything lives on your machine.

## FAQ

**Q: Why Python? Why not Rust or Go?**

A: Because then it would need a build step, and the whole point of this tool is `curl -O ... && python3 file.py`. Python is on every developer's machine. Stdlib is enough.

**Q: Does it work on Windows?**

A: The web UI should work. The home directory scanning should work. The atomic write logic uses `os.replace` which is cross-platform. I haven't tested on Windows. PRs welcome if you hit issues.

**Q: Why is there a hardcoded default password?**

A: So you can run the binary in one command. Override it for any real use via `API_MANAGER_PASSWORD`.

**Q: Can I use this with [direnv / dotenv-vault / Doppler / Infisical / 1Password CLI]?**

A: Those are great if you've already adopted them as your secret management strategy. This tool is for the messy reality where you have 31 `.env` files because you've been shipping projects for 2 years and never set up a coherent strategy. It's a **cleanup and discovery** tool. Run it once, learn what you have, *then* decide if you want to migrate to one of those.

**Q: The validation endpoints you call — do they cost money?**

A: No. Every validator hits a free metadata endpoint (`/models`, `/balance`, `/user`, `/auth/test`, `/domains`, etc.) that doesn't consume API credits.

**Q: How do I add a new service?**

A: Add an entry to the `SERVICES` list in `api-manager.py`. If the service has a free auth-check endpoint, add a validator function in the "Validation" section and reference it in the service entry. ~6 lines of code total. PR welcome.

## License

MIT. See [LICENSE](LICENSE).

## Contributing

PRs welcome for:

- New service entries in the `SERVICES` list
- New validator functions for existing services without them
- Bug fixes
- Windows compatibility improvements
- Screenshots / demo GIFs

Open an issue first for larger changes so we can discuss the approach.

## Credits

Built by [Eric Yerke](https://agency-os.ai) in April 2026, with substantial assistance from Claude (Opus 4.6 / Sonnet 4.6) over two intense Claude Code sessions. The tool is the kind of thing that would have taken me a week by hand; with Claude Code + [Model Context Protocol](https://modelcontextprotocol.io) it took a few focused hours.

If this saves you time, [say hi on Twitter](https://twitter.com/eyerke) or check out [Agency OS](https://agency-os.ai) — we build AI-powered tools like this for clients.
