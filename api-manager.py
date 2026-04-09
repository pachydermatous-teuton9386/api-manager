#!/usr/bin/env python3
"""api-manager — local API key vault and editor for every .env file on your machine.

Run web UI:    python3 api-manager.py
Run as MCP:    python3 api-manager.py --mcp
Open:          http://127.0.0.1:8765 (auto-opens in browser)
Stop:          ctrl-c

────────────────────────────────────────────────────────────────────────────────
What it does
────────────────────────────────────────────────────────────────────────────────

  • Scan every .env file on your machine. ~30 known to live on a typical dev
    laptop. Click any one to load it.
  • View / add / update / delete keys via a clean local UI. Atomic writes
    that preserve comments and blank lines. Automatic backups before any
    write to ~/.api-manager/backups/.
  • Auto-detect what service each key belongs to (Anthropic, OpenAI, Stripe,
    Supabase, Resend, Firecrawl, GitHub, PostHog, Notion, Linear, Slack,
    Sentry, Cloudflare, Vercel, Replicate, HuggingFace, OpenRouter, Tavily,
    and more). Colored service badges per key.
  • Live key validation. Click "validate" on a key and the tool calls the
    actual service API to check if the key still works. Per-service support
    for Anthropic, OpenAI, Stripe, Resend, Firecrawl, GitHub, OpenRouter,
    Replicate, HuggingFace, Cloudflare, Vercel, Sentry, Linear, Notion, Slack.
    All free metadata endpoints, no cost per check.
  • Rotation workflow. Click "rotate" on any key, jump to the service's
    dashboard, paste the new value, optionally update every file where the
    same key appears in one click, and the rotation is logged.
  • Cross-file global search. Type a key name and see every place it lives
    across all .env files at once. Drift highlighted automatically.
  • Master file generator. Build .env.master — a consolidated namespaced
    inventory of every key from every .env, gitignored, chmod 600.
  • Soft reveal password. Values are masked (last 4 chars) by default. Click
    "reveal" in the top right, type the password (default: Ey5000!!@@,
    override via API_MANAGER_PASSWORD env var), and values render in
    plaintext. Click any value to copy. Auto-locks after 5 minutes idle.
  • Audit log. Every read, write, validate, rotate, and unlock event is
    logged to a local SQLite at ~/.api-manager/audit.db. "When did I last
    change the Anthropic key?" becomes answerable.
  • MCP server mode. Run with --mcp flag to expose the tool as a Model
    Context Protocol stdio server. Claude Code can then manage keys via
    natural language: "rotate the OpenAI key in pipelines/.env to this
    value" or "find every project that uses my old Anthropic key."

────────────────────────────────────────────────────────────────────────────────
What it doesn't do
────────────────────────────────────────────────────────────────────────────────

  • Not a vault. Anyone with shell access can `cat` the .env files directly.
    The password is a screen guard against shoulder-surfing, not encryption.
  • Doesn't sync between files automatically. The rotation workflow lets you
    update many files in one click but it's an explicit action, not magic.
  • Doesn't gate writes by default. Add/update/delete work without unlocking.
    The password only controls *visibility* of existing values.

────────────────────────────────────────────────────────────────────────────────
Stack
────────────────────────────────────────────────────────────────────────────────

  Python 3 stdlib only. No pip install. Single file. Bound to 127.0.0.1.
  HTML + CSS + vanilla JS embedded in the HTML constant. No build step.

  All persistent state lives in ~/.api-manager/:
    metadata.json   per-key sidecar (service, first_seen, last_validated)
    audit.db        SQLite event log
    backups/        atomic file snapshots before every write

────────────────────────────────────────────────────────────────────────────────
"""

import hmac
import http.server
import json
import os
import re
import socketserver
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ─── Config ──────────────────────────────────────────────────────────────────

PORT = 8765
HOST = "127.0.0.1"

# Soft reveal password — gates seeing unmasked values, not gates writes.
# Override via API_MANAGER_PASSWORD (or legacy ENV_MANAGER_PASSWORD).
UNLOCK_PASSWORD = os.environ.get(
    "API_MANAGER_PASSWORD",
    os.environ.get("ENV_MANAGER_PASSWORD", "Ey5000!!@@"),
)

# Auto-lock after this many seconds of inactivity (server-side enforcement)
UNLOCK_IDLE_TIMEOUT = 5 * 60  # 5 minutes

# Per-user data lives in ~/.api-manager/
DATA_DIR = Path.home() / ".api-manager"
METADATA_PATH = DATA_DIR / "metadata.json"
AUDIT_DB_PATH = DATA_DIR / "audit.db"
BACKUPS_DIR = DATA_DIR / "backups"

DEFAULT_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

SCAN_ROOTS = [os.path.expanduser("~")]

ENV_FILENAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    ".env.staging", ".env.master",
}

SCAN_SKIP_DIRS = {
    "node_modules", ".git", ".next", "build", "dist", ".venv", "venv",
    "__pycache__", "Library", ".Trash", ".cache", ".npm", ".cargo",
    ".rustup", ".local", ".vscode-server", ".pyenv", ".rbenv", ".nvm",
    "DerivedData", "Pods", ".gradle", ".m2", ".docker", ".oh-my-zsh",
    "site-packages", ".ruff_cache", ".pytest_cache", ".mypy_cache",
    ".turbo", ".parcel-cache", ".svelte-kit", ".nuxt", ".expo",
    ".api-manager",
}


# ─── Service catalog ─────────────────────────────────────────────────────────
# Each entry has: name, color, prefixes (for value-based detection),
# name_hints (for env-var-name-based detection fallback), dashboard URL,
# and a validator key pointing into VALIDATORS below (or None if unsupported).

SERVICES = [
    {
        "name": "Anthropic",
        "color": "#cc785c",
        "prefixes": ["sk-ant-"],
        "name_hints": ["ANTHROPIC", "CLAUDE"],
        "dashboard": "https://console.anthropic.com/settings/keys",
        "validate": "anthropic",
    },
    {
        "name": "OpenAI",
        "color": "#10a37f",
        "prefixes": ["sk-proj-", "sk-svcacct-", "sk-None-", "sk-"],
        "name_hints": ["OPENAI"],
        "dashboard": "https://platform.openai.com/api-keys",
        "validate": "openai",
    },
    {
        "name": "Stripe (live)",
        "color": "#635bff",
        "prefixes": ["sk_live_", "rk_live_"],
        "name_hints": [],
        "dashboard": "https://dashboard.stripe.com/apikeys",
        "validate": "stripe",
    },
    {
        "name": "Stripe (test)",
        "color": "#a78bfa",
        "prefixes": ["sk_test_", "rk_test_"],
        "name_hints": [],
        "dashboard": "https://dashboard.stripe.com/test/apikeys",
        "validate": "stripe",
    },
    {
        "name": "Stripe publishable",
        "color": "#a78bfa",
        "prefixes": ["pk_live_", "pk_test_"],
        "name_hints": ["STRIPE_PUBLISHABLE", "STRIPE_PUBLIC"],
        "dashboard": "https://dashboard.stripe.com/apikeys",
        "validate": None,
    },
    {
        "name": "Stripe webhook",
        "color": "#a78bfa",
        "prefixes": ["whsec_"],
        "name_hints": ["STRIPE_WEBHOOK", "WEBHOOK_SECRET"],
        "dashboard": "https://dashboard.stripe.com/webhooks",
        "validate": None,
    },
    {
        "name": "Resend",
        "color": "#000000",
        "prefixes": ["re_"],
        "name_hints": ["RESEND"],
        "dashboard": "https://resend.com/api-keys",
        "validate": "resend",
    },
    {
        "name": "Firecrawl",
        "color": "#ff6b35",
        "prefixes": ["fc-"],
        "name_hints": ["FIRECRAWL"],
        "dashboard": "https://firecrawl.dev/app/api-keys",
        "validate": "firecrawl",
    },
    {
        "name": "PostHog",
        "color": "#1d4aff",
        "prefixes": ["phc_", "phx_"],
        "name_hints": ["POSTHOG"],
        "dashboard": "https://app.posthog.com/project/settings",
        "validate": None,
    },
    {
        "name": "Supabase",
        "color": "#3ecf8e",
        "prefixes": ["eyJ", "sbp_", "sbs_"],
        "name_hints": ["SUPABASE"],
        "dashboard": "https://supabase.com/dashboard",
        "validate": None,
    },
    {
        "name": "GitHub PAT",
        "color": "#1f2328",
        "prefixes": ["ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_"],
        "name_hints": ["GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"],
        "dashboard": "https://github.com/settings/tokens",
        "validate": "github",
    },
    {
        "name": "OpenRouter",
        "color": "#6366f1",
        "prefixes": ["sk-or-"],
        "name_hints": ["OPENROUTER"],
        "dashboard": "https://openrouter.ai/keys",
        "validate": "openrouter",
    },
    {
        "name": "Replicate",
        "color": "#ea4c89",
        "prefixes": ["r8_"],
        "name_hints": ["REPLICATE"],
        "dashboard": "https://replicate.com/account/api-tokens",
        "validate": "replicate",
    },
    {
        "name": "HuggingFace",
        "color": "#ffaa00",
        "prefixes": ["hf_"],
        "name_hints": ["HUGGINGFACE", "HF_TOKEN"],
        "dashboard": "https://huggingface.co/settings/tokens",
        "validate": "huggingface",
    },
    {
        "name": "Cloudflare",
        "color": "#f6821f",
        "prefixes": [],
        "name_hints": ["CLOUDFLARE", "CF_API"],
        "dashboard": "https://dash.cloudflare.com/profile/api-tokens",
        "validate": "cloudflare",
    },
    {
        "name": "Vercel",
        "color": "#000000",
        "prefixes": [],
        "name_hints": ["VERCEL_TOKEN"],
        "dashboard": "https://vercel.com/account/tokens",
        "validate": "vercel",
    },
    {
        "name": "Railway",
        "color": "#13111c",
        "prefixes": [],
        "name_hints": ["RAILWAY"],
        "dashboard": "https://railway.app/account/tokens",
        "validate": None,
    },
    {
        "name": "Sentry",
        "color": "#362d59",
        "prefixes": ["sntrys_"],
        "name_hints": ["SENTRY"],
        "dashboard": "https://sentry.io/settings/account/api/auth-tokens/",
        "validate": "sentry",
    },
    {
        "name": "Linear",
        "color": "#5e6ad2",
        "prefixes": ["lin_api_"],
        "name_hints": ["LINEAR"],
        "dashboard": "https://linear.app/settings/api",
        "validate": "linear",
    },
    {
        "name": "Notion",
        "color": "#000000",
        "prefixes": ["secret_", "ntn_"],
        "name_hints": ["NOTION"],
        "dashboard": "https://www.notion.so/my-integrations",
        "validate": "notion",
    },
    {
        "name": "Slack bot",
        "color": "#4a154b",
        "prefixes": ["xoxb-"],
        "name_hints": ["SLACK_BOT", "SLACK_TOKEN"],
        "dashboard": "https://api.slack.com/apps",
        "validate": "slack",
    },
    {
        "name": "Slack user",
        "color": "#4a154b",
        "prefixes": ["xoxp-", "xoxa-"],
        "name_hints": ["SLACK_USER"],
        "dashboard": "https://api.slack.com/apps",
        "validate": "slack",
    },
    {
        "name": "Tavily",
        "color": "#5b8def",
        "prefixes": ["tvly-"],
        "name_hints": ["TAVILY"],
        "dashboard": "https://tavily.com/account/api",
        "validate": None,
    },
    {
        "name": "Brave Search",
        "color": "#fb542b",
        "prefixes": ["BSA"],
        "name_hints": ["BRAVE"],
        "dashboard": "https://brave.com/search/api/",
        "validate": None,
    },
    {
        "name": "Perplexity",
        "color": "#1fb8cd",
        "prefixes": ["pplx-"],
        "name_hints": ["PERPLEXITY", "PPLX"],
        "dashboard": "https://www.perplexity.ai/settings/api",
        "validate": None,
    },
    {
        "name": "Google AI",
        "color": "#4285f4",
        "prefixes": ["AIza"],
        "name_hints": ["GOOGLE_AI", "GEMINI", "GOOGLE_API"],
        "dashboard": "https://aistudio.google.com/apikey",
        "validate": None,
    },
]


def detect_service(value, key_name=""):
    """Return the SERVICES entry that matches a key, or None.
    First tries value-prefix matching (more reliable), then env-var-name hints."""
    if value:
        v = value.strip().strip('"').strip("'")
        # Sort prefixes longest-first so sk-proj- beats sk-
        candidates = []
        for svc in SERVICES:
            for prefix in svc["prefixes"]:
                if v.startswith(prefix):
                    candidates.append((len(prefix), svc))
        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
    if key_name:
        name_upper = key_name.upper()
        # Sort hints longest-first so STRIPE_PUBLISHABLE beats STRIPE
        candidates = []
        for svc in SERVICES:
            for hint in svc["name_hints"]:
                if hint in name_upper:
                    candidates.append((len(hint), svc))
        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
    return None


def service_summary(svc):
    """Return a small dict safe to send over the wire."""
    if not svc:
        return None
    return {
        "name": svc["name"],
        "color": svc["color"],
        "dashboard": svc["dashboard"],
        "can_validate": svc["validate"] is not None,
    }


# ─── Validation ──────────────────────────────────────────────────────────────

def _http_request(url, method="GET", headers=None, data=None, timeout=10):
    """Stdlib HTTP wrapper. Returns (status, body_text)."""
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e.reason}")


def validate_anthropic(value):
    status, body = _http_request(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        headers={
            "x-api-key": value,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "model": "claude-haiku-4-5",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "."}],
        }).encode(),
    )
    # 200 = key works AND we got a real response
    # 400 with "invalid_request_error" but proper auth = key works (model name issue)
    if status == 200:
        return True
    if status == 400 and "invalid_request_error" in body:
        return True  # auth passed, model arg is the only issue
    return False


def validate_openai(value):
    status, _ = _http_request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_stripe(value):
    status, _ = _http_request(
        "https://api.stripe.com/v1/balance",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_resend(value):
    status, _ = _http_request(
        "https://api.resend.com/domains",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_firecrawl(value):
    status, _ = _http_request(
        "https://api.firecrawl.dev/v1/team/credit-usage",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_github(value):
    status, _ = _http_request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {value}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "api-manager",
        },
    )
    return status == 200


def validate_openrouter(value):
    status, _ = _http_request(
        "https://openrouter.ai/api/v1/auth/key",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_replicate(value):
    status, _ = _http_request(
        "https://api.replicate.com/v1/account",
        headers={"Authorization": f"Token {value}"},
    )
    return status == 200


def validate_huggingface(value):
    status, _ = _http_request(
        "https://huggingface.co/api/whoami-v2",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_cloudflare(value):
    status, _ = _http_request(
        "https://api.cloudflare.com/client/v4/user/tokens/verify",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_vercel(value):
    status, _ = _http_request(
        "https://api.vercel.com/v2/user",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_sentry(value):
    status, _ = _http_request(
        "https://sentry.io/api/0/",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200


def validate_linear(value):
    status, _ = _http_request(
        "https://api.linear.app/graphql",
        method="POST",
        headers={"Authorization": value, "Content-Type": "application/json"},
        data=json.dumps({"query": "{ viewer { id } }"}).encode(),
    )
    return status == 200


def validate_notion(value):
    status, _ = _http_request(
        "https://api.notion.com/v1/users/me",
        headers={
            "Authorization": f"Bearer {value}",
            "Notion-Version": "2022-06-28",
        },
    )
    return status == 200


def validate_slack(value):
    status, body = _http_request(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {value}"},
    )
    if status != 200:
        return False
    try:
        return json.loads(body).get("ok", False)
    except Exception:
        return False


VALIDATORS = {
    "anthropic": validate_anthropic,
    "openai": validate_openai,
    "stripe": validate_stripe,
    "resend": validate_resend,
    "firecrawl": validate_firecrawl,
    "github": validate_github,
    "openrouter": validate_openrouter,
    "replicate": validate_replicate,
    "huggingface": validate_huggingface,
    "cloudflare": validate_cloudflare,
    "vercel": validate_vercel,
    "sentry": validate_sentry,
    "linear": validate_linear,
    "notion": validate_notion,
    "slack": validate_slack,
}


def validate_key(value, service):
    """Returns 'valid' / 'invalid' / 'unsupported' / 'error: <msg>'."""
    if not service or not service.get("validate"):
        return "unsupported"
    fn = VALIDATORS.get(service["validate"])
    if not fn:
        return "unsupported"
    try:
        return "valid" if fn(value) else "invalid"
    except Exception as e:
        return f"error: {str(e)[:80]}"


# ─── Metadata sidecar ────────────────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def metadata_load():
    if not METADATA_PATH.exists():
        return {}
    try:
        return json.loads(METADATA_PATH.read_text())
    except Exception:
        return {}


def metadata_save(meta):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = METADATA_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=2, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, METADATA_PATH)


def _meta_id(file_path, key_name):
    return f"{file_path}::{key_name}"


def metadata_get(file_path, key_name):
    return metadata_load().get(_meta_id(file_path, key_name), {})


def metadata_set(file_path, key_name, **fields):
    meta = metadata_load()
    mid = _meta_id(file_path, key_name)
    if mid not in meta:
        meta[mid] = {"first_seen": now_iso()}
    meta[mid].update(fields)
    meta[mid]["last_modified"] = now_iso()
    metadata_save(meta)


def metadata_drop(file_path, key_name):
    meta = metadata_load()
    meta.pop(_meta_id(file_path, key_name), None)
    metadata_save(meta)


# ─── Audit log ───────────────────────────────────────────────────────────────

def audit_init():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUDIT_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            file_path TEXT,
            key_name TEXT,
            details TEXT
        )
    """)
    conn.commit()
    conn.close()
    try:
        os.chmod(AUDIT_DB_PATH, 0o600)
    except Exception:
        pass


def audit_log(event, file_path=None, key_name=None, details=None):
    try:
        conn = sqlite3.connect(AUDIT_DB_PATH)
        conn.execute(
            "INSERT INTO events (ts, event, file_path, key_name, details) VALUES (?, ?, ?, ?, ?)",
            (now_iso(), event, file_path, key_name,
             json.dumps(details) if details is not None else None),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # never fail an operation just because logging failed


def audit_recent(limit=50):
    try:
        conn = sqlite3.connect(AUDIT_DB_PATH)
        rows = conn.execute(
            "SELECT ts, event, file_path, key_name, details "
            "FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [
            {
                "ts": ts, "event": event, "file_path": fp,
                "key_name": kn,
                "details": json.loads(details) if details else None,
            }
            for ts, event, fp, kn, details in rows
        ]
    except Exception:
        return []


# ─── Backups ─────────────────────────────────────────────────────────────────

def backup_file(path):
    """Snapshot a file before mutation. Returns backup path or None."""
    if not os.path.exists(path):
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_name = path.replace("/", "_").lstrip("_")
    bp = BACKUPS_DIR / f"{ts}_{safe_name}"
    try:
        with open(path, "rb") as src, open(bp, "wb") as dst:
            dst.write(src.read())
        os.chmod(bp, 0o600)
    except Exception:
        return None
    return str(bp)


# ─── .env parsing ────────────────────────────────────────────────────────────

KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def parse_env(path):
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r") as f:
        for raw in f.read().splitlines():
            if not raw.strip():
                entries.append({"kind": "blank", "raw": raw})
                continue
            if raw.lstrip().startswith("#"):
                entries.append({"kind": "comment", "raw": raw})
                continue
            m = KV_RE.match(raw)
            if not m:
                entries.append({"kind": "other", "raw": raw})
                continue
            key = m.group(1)
            rest = m.group(2)
            comment = ""
            value = rest
            cm = re.search(r"\s+#(.*)$", rest)
            if cm:
                comment = "#" + cm.group(1)
                value = rest[: cm.start()]
            value = value.strip()
            entries.append({
                "kind": "kv", "key": key, "value": value,
                "comment": comment, "raw": raw,
            })
    return entries


def serialize_env(entries):
    out_lines = []
    for e in entries:
        if e["kind"] == "kv":
            line = f'{e["key"]}={e["value"]}'
            if e.get("comment"):
                line += " " + e["comment"]
            out_lines.append(line)
        else:
            out_lines.append(e.get("raw", ""))
    return "\n".join(out_lines) + "\n"


def atomic_write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def upsert(entries, key, value, comment=None):
    for e in entries:
        if e["kind"] == "kv" and e["key"] == key:
            e["value"] = value
            if comment is not None:
                e["comment"] = comment
            return entries
    entries.append({
        "kind": "kv", "key": key, "value": value,
        "comment": comment or "", "raw": "",
    })
    return entries


def delete_key(entries, key):
    return [e for e in entries if not (e["kind"] == "kv" and e["key"] == key)]


def mask(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def public_view(entries, file_path=None, unlocked=False):
    """Return only kv entries enriched with service detection + metadata."""
    result = []
    for e in entries:
        if e["kind"] != "kv":
            continue
        svc = detect_service(e["value"], e["key"])
        meta = metadata_get(file_path, e["key"]) if file_path else {}
        item = {
            "key": e["key"],
            "masked": mask(e["value"]),
            "length": len(e["value"]),
            "comment": e.get("comment", ""),
            "service": service_summary(svc),
            "last_validated": meta.get("last_validated"),
            "validation_status": meta.get("validation_status"),
            "first_seen": meta.get("first_seen"),
            "last_modified": meta.get("last_modified"),
        }
        if unlocked:
            item["value"] = e["value"]
        result.append(item)
    return result


# ─── Unlock + idle timeout ───────────────────────────────────────────────────

_LAST_UNLOCK_ACTIVITY = {"ts": 0.0}


def is_unlocked(headers):
    """Constant-time check + idle-timeout enforcement."""
    provided = headers.get("X-Unlock-Password", "") or ""
    if not provided:
        return False
    if not hmac.compare_digest(provided, UNLOCK_PASSWORD):
        return False
    # Server-side idle check: if too much time has passed since last unlocked
    # request, force a re-unlock by rejecting now.
    now = time.time()
    last = _LAST_UNLOCK_ACTIVITY["ts"]
    if last and now - last > UNLOCK_IDLE_TIMEOUT:
        _LAST_UNLOCK_ACTIVITY["ts"] = 0
        return False
    _LAST_UNLOCK_ACTIVITY["ts"] = now
    return True


# ─── Scan + cross-file search ────────────────────────────────────────────────

def scan_env_files():
    found = []
    for root in SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in SCAN_SKIP_DIRS and not d.startswith(".vscode")
            ]
            for fn in filenames:
                if fn in ENV_FILENAMES:
                    found.append(os.path.join(dirpath, fn))
    return sorted(set(found))


def global_search(query, unlocked=False):
    """Cross-file search. Searches key names always; values only when unlocked."""
    q = (query or "").lower().strip()
    if not q:
        return []
    files = scan_env_files()
    results = []
    for fp in files:
        try:
            entries = parse_env(fp)
            for e in entries:
                if e["kind"] != "kv":
                    continue
                key_match = q in e["key"].lower()
                value_match = unlocked and e["value"] and q in e["value"].lower()
                if key_match or value_match:
                    svc = detect_service(e["value"], e["key"])
                    item = {
                        "file": fp,
                        "key": e["key"],
                        "masked": mask(e["value"]),
                        "service": service_summary(svc),
                    }
                    if unlocked:
                        item["value"] = e["value"]
                    results.append(item)
        except Exception:
            continue
    return results


def find_key_everywhere(key_name):
    """Return every (file, value) where this key appears across all .env files."""
    files = scan_env_files()
    hits = []
    for fp in files:
        try:
            entries = parse_env(fp)
            for e in entries:
                if e["kind"] == "kv" and e["key"] == key_name:
                    hits.append({"file": fp, "value": e["value"]})
                    break
        except Exception:
            continue
    return hits


# ─── HTML ────────────────────────────────────────────────────────────────────

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>api manager</title>
<style>
  :root {
    --bg: #fafaf9;
    --panel: #ffffff;
    --ink: #1c1917;
    --muted: #78716c;
    --line: #e7e5e4;
    --accent: #1c1917;
    --danger: #b91c1c;
    --ok: #15803d;
    --warn: #b45309;
    --radius: 10px;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    background: var(--bg);
    color: var(--ink);
    -webkit-font-smoothing: antialiased;
    font-size: 15px;
    line-height: 1.5;
  }
  .wrap { max-width: 820px; margin: 48px auto 96px; padding: 0 24px; }
  .topbar {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 16px; margin-bottom: 32px;
  }
  .topbar-left h1 {
    font-size: 24px; font-weight: 600; letter-spacing: -0.01em;
    margin: 0 0 4px;
  }
  .topbar-left .sub {
    color: var(--muted); margin: 0; font-size: 13px;
  }
  .lock {
    display: flex; align-items: center; gap: 8px;
    flex-shrink: 0;
  }
  .lock .badge {
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 999px;
    background: #fee2e2;
    color: #991b1b;
    font-weight: 500;
  }
  .lock.unlocked .badge {
    background: #dcfce7;
    color: #166534;
  }
  .lock input {
    width: 140px;
    font-size: 12px;
    padding: 6px 10px;
    font-family: -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
  }
  .panel {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 20px;
    margin-bottom: 20px;
  }
  .panel h2 {
    margin: 0 0 14px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
  }
  .panel-head h2 { margin: 0; }
  .panel-head input {
    width: 220px;
    font-size: 13px;
    padding: 7px 10px;
  }
  .collapsible h2 {
    cursor: pointer;
    user-select: none;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .collapsible h2::before {
    content: "▸";
    font-size: 10px;
    transition: transform 0.15s ease;
  }
  .collapsible.open h2::before {
    transform: rotate(90deg);
  }
  .collapsible .body { display: none; }
  .collapsible.open .body { display: block; }
  label {
    display: block;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
    margin-bottom: 6px;
  }
  input[type="text"], input[type="password"], select {
    width: 100%;
    font: inherit;
    font-size: 14px;
    color: var(--ink);
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 10px 12px;
    outline: none;
    transition: border-color 0.12s ease;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
  }
  input:focus, select:focus { border-color: var(--ink); }
  .row { display: flex; gap: 10px; align-items: flex-end; }
  .row > * { flex: 1; }
  .row > .btn-col { flex: 0 0 auto; }
  button {
    font: inherit;
    font-size: 13px;
    font-weight: 500;
    color: #fff;
    background: var(--ink);
    border: 1px solid var(--ink);
    border-radius: 8px;
    padding: 10px 16px;
    cursor: pointer;
    transition: opacity 0.12s ease;
  }
  button:hover { opacity: 0.85; }
  button.ghost {
    background: transparent;
    color: var(--ink);
    border-color: var(--line);
  }
  button.danger {
    background: transparent;
    color: var(--danger);
    border-color: transparent;
    padding: 6px 8px;
  }
  button.icon {
    background: transparent;
    color: var(--muted);
    border: none;
    padding: 4px 6px;
    font-size: 11px;
  }
  button.icon:hover { color: var(--ink); }
  ul.keys { list-style: none; padding: 0; margin: 0; }
  ul.keys li {
    padding: 12px 0;
    border-bottom: 1px solid var(--line);
  }
  ul.keys li:last-child { border-bottom: none; }
  .key-row {
    display: flex; align-items: center; gap: 12px;
  }
  .k {
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 13px;
    font-weight: 600;
    color: var(--ink);
    flex: 0 0 auto;
    min-width: 200px;
    word-break: break-all;
  }
  .v {
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 12px;
    color: var(--muted);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .actions { display: flex; gap: 2px; flex: 0 0 auto; }
  .empty { color: var(--muted); font-size: 13px; padding: 8px 0; }
  .key-meta {
    display: flex; align-items: center; gap: 8px;
    margin-top: 4px;
    margin-left: 0;
    font-size: 11px;
    color: var(--muted);
  }
  .svc-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 600;
    text-transform: none;
    letter-spacing: 0;
    color: #fff;
    white-space: nowrap;
  }
  .svc-badge.unknown { background: #d6d3d1; color: #57534e; }
  .v-status {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 10px;
  }
  .v-status.valid { color: var(--ok); }
  .v-status.invalid { color: var(--danger); }
  .v-status.error { color: var(--warn); }
  .toast {
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    background: var(--ink); color: #fff;
    padding: 10px 16px; border-radius: 8px;
    font-size: 13px;
    opacity: 0; transition: opacity 0.2s ease;
    pointer-events: none;
    max-width: 90vw;
  }
  .toast.show { opacity: 1; }
  .toast.err { background: var(--danger); }
  .toast.ok { background: var(--ok); }
  .scan-list {
    margin-top: 8px;
    border: 1px solid var(--line);
    border-radius: 8px;
    max-height: 240px;
    overflow-y: auto;
  }
  .scan-list button {
    display: block;
    width: 100%;
    text-align: left;
    background: transparent;
    color: var(--ink);
    border: none;
    border-bottom: 1px solid var(--line);
    border-radius: 0;
    padding: 8px 12px;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 12px;
  }
  .scan-list button:hover { background: var(--bg); }
  .scan-list button:last-child { border-bottom: none; }
  .meta { font-size: 11px; color: var(--muted); }
  .editing input { font-size: 12px; padding: 6px 8px; }
  .editing { flex: 1; display: flex; gap: 6px; align-items: center; }
  .modal-backdrop {
    position: fixed; inset: 0;
    background: rgba(28, 25, 23, 0.45);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }
  .modal-backdrop.show { display: flex; }
  .modal {
    background: #fff;
    border-radius: 12px;
    padding: 28px;
    max-width: 540px;
    width: 90%;
    max-height: 86vh;
    overflow-y: auto;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
  }
  .modal h3 {
    margin: 0 0 8px;
    font-size: 18px;
    font-weight: 600;
  }
  .modal .modal-sub {
    color: var(--muted);
    font-size: 13px;
    margin: 0 0 20px;
  }
  .modal .step {
    border-left: 2px solid var(--line);
    padding: 4px 0 12px 16px;
    margin-bottom: 12px;
  }
  .modal .step strong {
    display: block;
    margin-bottom: 6px;
    font-size: 13px;
  }
  .modal .step p {
    margin: 4px 0;
    font-size: 13px;
    color: var(--muted);
  }
  .modal .modal-actions {
    display: flex;
    gap: 8px;
    margin-top: 16px;
    justify-content: flex-end;
  }
  .file-pill {
    display: inline-block;
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 6px 10px;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 11px;
    margin: 4px 0;
    word-break: break-all;
  }
  .search-results {
    margin-top: 12px;
  }
  .search-result {
    padding: 10px 0;
    border-bottom: 1px solid var(--line);
  }
  .search-result:last-child { border-bottom: none; }
  .search-result .file-link {
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 11px;
    color: var(--muted);
    cursor: pointer;
    word-break: break-all;
  }
  .search-result .file-link:hover { color: var(--ink); }
  .search-result .key-line {
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 13px;
    margin-top: 2px;
  }
  .audit-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 0;
    border-bottom: 1px solid var(--line);
    font-size: 12px;
  }
  .audit-row:last-child { border-bottom: none; }
  .audit-row .audit-ts { color: var(--muted); flex: 0 0 140px; font-family: ui-monospace, monospace; font-size: 11px; }
  .audit-row .audit-event { flex: 0 0 100px; font-weight: 600; }
  .audit-row .audit-target { flex: 1; color: var(--muted); font-family: ui-monospace, monospace; font-size: 11px; word-break: break-all; }
  .checkbox-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    font-size: 12px;
  }
  .checkbox-row input[type="checkbox"] {
    width: auto;
    margin: 0;
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="topbar-left">
        <h1>api manager</h1>
        <p class="sub">view, validate, and rotate API keys across every .env file on your machine. localhost only.</p>
      </div>
      <div class="lock" id="lock">
        <span class="badge" id="lockBadge">🔒 locked</span>
        <button class="ghost" id="revealBtn">reveal</button>
      </div>
    </div>

    <div class="panel">
      <h2>file</h2>
      <div class="row">
        <div>
          <input type="text" id="path" />
        </div>
        <div class="btn-col">
          <button class="ghost" id="loadBtn">load</button>
        </div>
        <div class="btn-col">
          <button class="ghost" id="scanBtn">scan</button>
        </div>
      </div>
      <div id="scanList"></div>
      <div class="meta" id="fileMeta" style="margin-top: 10px;"></div>
    </div>

    <div class="panel">
      <h2>add or update</h2>
      <div class="row">
        <div>
          <label>key</label>
          <input type="text" id="newKey" placeholder="ANTHROPIC_API_KEY" />
        </div>
        <div>
          <label>value</label>
          <input type="password" id="newVal" placeholder="sk-ant-..." />
        </div>
        <div class="btn-col" style="align-self: flex-end;">
          <button id="saveBtn">save</button>
        </div>
      </div>
      <div class="meta" id="newKeyHint" style="margin-top: 8px; min-height: 14px;"></div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>existing keys</h2>
        <input type="text" id="search" placeholder="filter…" autocomplete="off" />
      </div>
      <ul class="keys" id="keys"></ul>
    </div>

    <div class="panel collapsible" id="globalPanel">
      <h2>🔍 search across all .env files</h2>
      <div class="body">
        <div class="row" style="margin-top: 4px;">
          <div>
            <input type="text" id="globalQuery" placeholder="ANTHROPIC, sk-, etc." autocomplete="off" />
          </div>
          <div class="btn-col">
            <button id="globalGoBtn" class="ghost">search</button>
          </div>
        </div>
        <div class="meta" style="margin-top: 6px;">
          searches every .env file on the machine. key names always; values only when unlocked.
        </div>
        <div class="search-results" id="globalResults"></div>
      </div>
    </div>

    <div class="panel collapsible" id="auditPanel">
      <h2>📋 recent activity</h2>
      <div class="body">
        <div class="meta">last 50 events from <code>~/.api-manager/audit.db</code>.</div>
        <div id="auditList" style="margin-top: 12px;"></div>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="modal">
    <div class="modal" id="modalBody"></div>
  </div>

  <div class="toast" id="toast"></div>

<script>
const $ = (s) => document.querySelector(s);
const pathInput = $("#path");
const keysEl = $("#keys");
const fileMeta = $("#fileMeta");
const toast = $("#toast");
let lastData = { entries: [], path: "", exists: false, unlocked: false };
let unlockPw = null; // in-memory only — never persisted
let SERVICES_INFO = [];

function authHeaders() {
  return unlockPw ? { "X-Unlock-Password": unlockPw } : {};
}

function showToast(msg, kind) {
  toast.textContent = msg;
  toast.classList.remove("err", "ok");
  if (kind === "err") toast.classList.add("err");
  if (kind === "ok") toast.classList.add("ok");
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2400);
}

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "request failed");
  return data;
}

async function load() {
  const path = pathInput.value.trim();
  try {
    const data = await fetch("/api/env?path=" + encodeURIComponent(path), {
      headers: authHeaders(),
    }).then(r => r.json());
    if (data.error) {
      showToast(data.error, "err");
      return;
    }
    lastData = data;
    renderLockState();
    render();
  } catch (e) {
    showToast(e.message, "err");
  }
}

function renderLockState() {
  const lock = $("#lock");
  const badge = $("#lockBadge");
  if (!lock || !badge) return;
  if (lastData.unlocked) {
    lock.classList.add("unlocked");
    badge.textContent = "🔓 unlocked";
  } else {
    lock.classList.remove("unlocked");
    badge.textContent = "🔒 locked";
  }
}

function fmtSince(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, now - then);
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function badgeHtml(svc) {
  if (!svc) return '<span class="svc-badge unknown">unknown</span>';
  return `<span class="svc-badge" style="background:${svc.color}">${escapeHtml(svc.name)}</span>`;
}

function statusHtml(status, lastValidated) {
  if (!status) return "";
  const cls = status === "valid" ? "valid" : (status === "invalid" ? "invalid" : "error");
  const sym = status === "valid" ? "✓" : (status === "invalid" ? "✗" : "⚠");
  const text = status === "valid" ? "valid" : (status === "invalid" ? "invalid" : "error");
  const ago = lastValidated ? ` · ${fmtSince(lastValidated)}` : "";
  return `<span class="v-status ${cls}">${sym} ${text}${ago}</span>`;
}

function render() {
  const data = lastData;
  fileMeta.textContent = data.exists
    ? `${data.entries.length} key${data.entries.length === 1 ? "" : "s"} • ${data.path}`
    : `file does not exist yet — saving will create it at ${data.path}`;
  keysEl.innerHTML = "";
  if (data.entries.length === 0) {
    keysEl.innerHTML = '<li class="empty">no keys yet. add one above.</li>';
    return;
  }
  const q = ($("#search").value || "").trim().toLowerCase();
  const filtered = q
    ? data.entries.filter(e =>
        e.key.toLowerCase().includes(q) ||
        (e.masked || "").toLowerCase().includes(q) ||
        (e.service && e.service.name.toLowerCase().includes(q)))
    : data.entries;
  if (filtered.length === 0) {
    keysEl.innerHTML = `<li class="empty">no keys match "${escapeHtml(q)}"</li>`;
    return;
  }
  for (const e of filtered) {
    const li = document.createElement("li");
    const display = data.unlocked && e.value !== undefined ? e.value : (e.masked || "(empty)");
    const canValidate = e.service && e.service.can_validate;
    li.innerHTML = `
      <div class="key-row">
        <span class="k">${escapeHtml(e.key)}</span>
        <span class="v" title="${data.unlocked ? 'click to copy' : ''}">${escapeHtml(display)}</span>
        <span class="actions">
          ${canValidate ? `<button class="icon" data-validate="${escapeHtml(e.key)}" title="validate against API">✓</button>` : ''}
          ${e.service && e.service.dashboard ? `<button class="icon" data-rotate="${escapeHtml(e.key)}" title="rotate this key">↻</button>` : ''}
          <button class="icon" data-edit="${escapeHtml(e.key)}">edit</button>
          <button class="danger" data-del="${escapeHtml(e.key)}">delete</button>
        </span>
      </div>
      <div class="key-meta">
        ${badgeHtml(e.service)}
        ${statusHtml(e.validation_status, e.last_validated)}
        ${e.last_modified ? `<span>· edited ${fmtSince(e.last_modified)}</span>` : ''}
      </div>
    `;
    if (data.unlocked) {
      const vEl = li.querySelector(".v");
      vEl.style.cursor = "pointer";
      vEl.onclick = () => {
        navigator.clipboard.writeText(e.value || "").then(() => showToast("copied", "ok"));
      };
    }
    keysEl.appendChild(li);
  }
  keysEl.querySelectorAll("[data-del]").forEach(btn => {
    btn.onclick = () => onDelete(btn.dataset.del);
  });
  keysEl.querySelectorAll("[data-edit]").forEach(btn => {
    btn.onclick = () => onEdit(btn.dataset.edit, btn.closest("li"));
  });
  keysEl.querySelectorAll("[data-validate]").forEach(btn => {
    btn.onclick = () => onValidate(btn.dataset.validate);
  });
  keysEl.querySelectorAll("[data-rotate]").forEach(btn => {
    btn.onclick = () => onRotate(btn.dataset.rotate);
  });
}

function onEdit(key, li) {
  const valSpan = li.querySelector(".v");
  const actions = li.querySelector(".actions");
  const original = valSpan.textContent;
  const wrap = document.createElement("div");
  wrap.className = "editing";
  wrap.innerHTML = `
    <input type="${lastData.unlocked ? 'text' : 'password'}" placeholder="new value for ${escapeHtml(key)}" />
    <button>save</button>
    <button class="ghost cancel">cancel</button>
  `;
  valSpan.replaceWith(wrap);
  actions.style.display = "none";
  const input = wrap.querySelector("input");
  input.focus();
  wrap.querySelector("button:not(.cancel)").onclick = async () => {
    if (!input.value) { showToast("value can't be empty", "err"); return; }
    try {
      await api("POST", "/api/env", { path: pathInput.value.trim(), key, value: input.value });
      showToast("saved", "ok");
      load();
    } catch (e) { showToast(e.message, "err"); }
  };
  wrap.querySelector(".cancel").onclick = () => {
    const span = document.createElement("span");
    span.className = "v";
    span.textContent = original;
    wrap.replaceWith(span);
    actions.style.display = "";
  };
}

async function onDelete(key) {
  if (!confirm(`Delete ${key}?`)) return;
  try {
    await api("DELETE", "/api/env", { path: pathInput.value.trim(), key });
    showToast("deleted", "ok");
    load();
  } catch (e) { showToast(e.message, "err"); }
}

async function onValidate(key) {
  if (!unlockPw) {
    showToast("unlock first to validate", "err");
    return;
  }
  showToast(`validating ${key}…`);
  try {
    const data = await api("POST", "/api/validate", { path: pathInput.value.trim(), key });
    if (data.status === "valid") showToast(`${key}: valid ✓`, "ok");
    else if (data.status === "invalid") showToast(`${key}: INVALID — needs rotation`, "err");
    else if (data.status === "unsupported") showToast(`${key}: no validator for this service`);
    else showToast(`${key}: ${data.status}`, "err");
    load();
  } catch (e) { showToast(e.message, "err"); }
}

async function onRotate(key) {
  if (!unlockPw) {
    showToast("unlock first to rotate", "err");
    return;
  }
  // Look up the key's service from current data
  const entry = lastData.entries.find(e => e.key === key);
  if (!entry || !entry.service) {
    showToast("can't rotate: unknown service", "err");
    return;
  }
  // Find every file the key appears in
  let occurrences;
  try {
    occurrences = await api("POST", "/api/find-key", { key });
  } catch (e) {
    showToast(e.message, "err");
    return;
  }
  showRotateModal(key, entry.service, occurrences.hits || []);
}

function showRotateModal(key, service, hits) {
  const modal = $("#modal");
  const body = $("#modalBody");
  const dashUrl = service.dashboard;
  const fileList = hits.map(h => {
    const safe = escapeHtml(h.file);
    const isCurrent = h.file === pathInput.value.trim();
    return `
      <div class="checkbox-row">
        <input type="checkbox" ${isCurrent ? 'checked' : 'checked'} data-rotate-file="${safe}" />
        <code>${safe}</code>
      </div>
    `;
  }).join("");
  body.innerHTML = `
    <h3>Rotate ${escapeHtml(key)}</h3>
    <p class="modal-sub">${escapeHtml(service.name)} · ${hits.length} file${hits.length === 1 ? '' : 's'} contain${hits.length === 1 ? 's' : ''} this key</p>

    <div class="step">
      <strong>1. Open the dashboard</strong>
      <p><a href="${dashUrl}" target="_blank" rel="noopener">${dashUrl}</a></p>
      <button class="ghost" id="openDashBtn">open in new tab</button>
    </div>

    <div class="step">
      <strong>2. Create a new key, copy the value</strong>
      <p>Then disable or delete the old one in the same dashboard. Don't forget to revoke the old key after pasting the new one below.</p>
    </div>

    <div class="step">
      <strong>3. Paste the new value</strong>
      <input type="password" id="rotateVal" placeholder="paste new key value" style="margin-top: 8px;" />
    </div>

    <div class="step">
      <strong>4. Update which files?</strong>
      ${fileList || '<p>(no files found containing this key)</p>'}
    </div>

    <div class="step">
      <strong>5. Validate after rotation?</strong>
      <div class="checkbox-row">
        <input type="checkbox" id="rotateValidate" checked />
        <span>verify the new key works against the API</span>
      </div>
    </div>

    <div class="modal-actions">
      <button class="ghost" id="rotateCancelBtn">cancel</button>
      <button id="rotateSaveBtn">rotate</button>
    </div>
  `;
  modal.classList.add("show");

  $("#openDashBtn").onclick = () => window.open(dashUrl, "_blank");
  $("#rotateCancelBtn").onclick = () => modal.classList.remove("show");
  $("#rotateSaveBtn").onclick = async () => {
    const newVal = $("#rotateVal").value;
    if (!newVal) { showToast("paste a value first", "err"); return; }
    const targets = Array.from(body.querySelectorAll("[data-rotate-file]:checked"))
      .map(cb => cb.dataset.rotateFile);
    if (targets.length === 0) { showToast("pick at least one file", "err"); return; }
    const validate = $("#rotateValidate").checked;
    try {
      const result = await api("POST", "/api/rotate", { key, value: newVal, files: targets, validate });
      modal.classList.remove("show");
      showToast(`rotated in ${result.updated} file${result.updated === 1 ? '' : 's'}${validate ? ' · ' + (result.validation || '') : ''}`, "ok");
      load();
    } catch (e) { showToast(e.message, "err"); }
  };
}

$("#saveBtn").onclick = async () => {
  const key = $("#newKey").value.trim();
  const value = $("#newVal").value;
  if (!key) { showToast("key required", "err"); return; }
  if (!value) { showToast("value required", "err"); return; }
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
    showToast("invalid key name", "err");
    return;
  }
  try {
    await api("POST", "/api/env", { path: pathInput.value.trim(), key, value });
    showToast("saved", "ok");
    $("#newKey").value = "";
    $("#newVal").value = "";
    $("#newKeyHint").innerHTML = "";
    load();
  } catch (e) { showToast(e.message, "err"); }
};

$("#loadBtn").onclick = load;
$("#search").oninput = () => render();

// Live service detection in the new-key form
function updateNewKeyHint() {
  const k = $("#newKey").value.trim();
  const v = $("#newVal").value;
  const hint = $("#newKeyHint");
  if (!k && !v) { hint.innerHTML = ""; return; }
  // Client-side detection lookup against SERVICES_INFO
  let matched = null;
  if (v) {
    for (const svc of SERVICES_INFO) {
      for (const p of svc.prefixes) {
        if (v.startsWith(p)) { matched = svc; break; }
      }
      if (matched) break;
    }
  }
  if (!matched && k) {
    const upper = k.toUpperCase();
    for (const svc of SERVICES_INFO) {
      for (const h of svc.name_hints) {
        if (upper.includes(h)) { matched = svc; break; }
      }
      if (matched) break;
    }
  }
  if (matched) {
    hint.innerHTML = `detected service: <span class="svc-badge" style="background:${matched.color}">${escapeHtml(matched.name)}</span>`;
  } else {
    hint.innerHTML = '<span class="svc-badge unknown">unknown service</span>';
  }
}
$("#newKey").oninput = updateNewKeyHint;
$("#newVal").oninput = updateNewKeyHint;

// ─── Reveal / lock ──────────────────────────────────────────────────────────

function showRevealPrompt() {
  const lock = $("#lock");
  lock.innerHTML = `
    <span class="badge" id="lockBadge">🔒 locked</span>
    <input type="password" id="pwInput" placeholder="password" autocomplete="off" />
    <button id="pwSubmit">unlock</button>
    <button class="ghost" id="pwCancel">cancel</button>
  `;
  const input = $("#pwInput");
  input.focus();
  input.onkeydown = (e) => { if (e.key === "Enter") submitPw(); };
  $("#pwSubmit").onclick = submitPw;
  $("#pwCancel").onclick = resetLockUI;
}

async function submitPw() {
  const pw = $("#pwInput").value;
  if (!pw) return;
  try {
    const data = await fetch("/api/env?path=" + encodeURIComponent(pathInput.value.trim()), {
      headers: { "X-Unlock-Password": pw },
    }).then(r => r.json());
    if (data.unlocked) {
      unlockPw = pw;
      showToast("unlocked", "ok");
      resetLockUI();
      load();
    } else {
      showToast("wrong password", "err");
      $("#pwInput").value = "";
      $("#pwInput").focus();
    }
  } catch (e) {
    showToast(e.message, "err");
  }
}

function resetLockUI() {
  const lock = $("#lock");
  if (unlockPw) {
    lock.innerHTML = `
      <span class="badge" id="lockBadge">🔓 unlocked</span>
      <button class="ghost" id="lockBtn">lock</button>
    `;
    lock.classList.add("unlocked");
    $("#lockBtn").onclick = () => {
      unlockPw = null;
      resetLockUI();
      load();
      showToast("locked", "ok");
    };
  } else {
    lock.innerHTML = `
      <span class="badge" id="lockBadge">🔒 locked</span>
      <button class="ghost" id="revealBtn">reveal</button>
    `;
    lock.classList.remove("unlocked");
    $("#revealBtn").onclick = showRevealPrompt;
  }
}

$("#revealBtn").onclick = showRevealPrompt;

$("#scanBtn").onclick = async () => {
  const list = $("#scanList");
  list.innerHTML = '<div class="meta" style="padding: 8px 0;">scanning…</div>';
  try {
    const data = await fetch("/api/scan").then(r => r.json());
    if (!data.files.length) {
      list.innerHTML = '<div class="meta" style="padding: 8px 0;">no .env files found</div>';
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "scan-list";
    for (const f of data.files) {
      const b = document.createElement("button");
      b.textContent = f;
      b.onclick = () => {
        pathInput.value = f;
        list.innerHTML = "";
        load();
      };
      wrap.appendChild(b);
    }
    list.innerHTML = "";
    list.appendChild(wrap);
  } catch (e) { showToast(e.message, "err"); }
};

// ─── Collapsible sections ───────────────────────────────────────────────────

document.querySelectorAll(".collapsible h2").forEach(h => {
  h.onclick = () => {
    const panel = h.closest(".collapsible");
    panel.classList.toggle("open");
    if (panel.id === "auditPanel" && panel.classList.contains("open")) {
      loadAudit();
    }
  };
});

// ─── Global search ──────────────────────────────────────────────────────────

async function runGlobalSearch() {
  const q = $("#globalQuery").value.trim();
  if (!q) { showToast("type something to search", "err"); return; }
  try {
    const data = await fetch("/api/global-search?q=" + encodeURIComponent(q), {
      headers: authHeaders(),
    }).then(r => r.json());
    renderGlobalResults(data.results || []);
  } catch (e) { showToast(e.message, "err"); }
}

function renderGlobalResults(results) {
  const el = $("#globalResults");
  if (results.length === 0) {
    el.innerHTML = '<div class="empty">no matches</div>';
    return;
  }
  // Group by key, then by value (so drift is visible)
  const byKey = {};
  for (const r of results) {
    if (!byKey[r.key]) byKey[r.key] = {};
    const k = r.value !== undefined ? r.value : r.masked;
    if (!byKey[r.key][k]) byKey[r.key][k] = { display: r.value !== undefined ? r.value : r.masked, files: [], service: r.service };
    byKey[r.key][k].files.push(r.file);
  }
  let html = "";
  for (const key of Object.keys(byKey).sort()) {
    const valueGroups = byKey[key];
    const valueCount = Object.keys(valueGroups).length;
    const sample = Object.values(valueGroups)[0];
    html += `<div class="search-result">`;
    html += `<div class="key-line"><strong>${escapeHtml(key)}</strong> ${badgeHtml(sample.service)} ${valueCount > 1 ? `<span class="v-status invalid">⚠ ${valueCount} distinct values (drift)</span>` : ''}</div>`;
    for (const valKey of Object.keys(valueGroups)) {
      const grp = valueGroups[valKey];
      html += `<div class="meta" style="margin-top: 4px;"><code>${escapeHtml(grp.display)}</code> in ${grp.files.length} file${grp.files.length === 1 ? '' : 's'}:</div>`;
      for (const f of grp.files) {
        html += `<div class="file-link" data-load-file="${escapeHtml(f)}">${escapeHtml(f)}</div>`;
      }
    }
    html += `</div>`;
  }
  el.innerHTML = html;
  el.querySelectorAll("[data-load-file]").forEach(div => {
    div.onclick = () => {
      pathInput.value = div.dataset.loadFile;
      load();
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
  });
}

$("#globalGoBtn").onclick = runGlobalSearch;
$("#globalQuery").onkeydown = (e) => { if (e.key === "Enter") runGlobalSearch(); };

// ─── Audit log ──────────────────────────────────────────────────────────────

async function loadAudit() {
  try {
    const data = await fetch("/api/audit").then(r => r.json());
    const el = $("#auditList");
    if (!data.events || data.events.length === 0) {
      el.innerHTML = '<div class="empty">no events yet</div>';
      return;
    }
    el.innerHTML = data.events.map(ev => `
      <div class="audit-row">
        <div class="audit-ts">${escapeHtml(ev.ts.replace('T', ' ').replace('Z', ''))}</div>
        <div class="audit-event">${escapeHtml(ev.event)}</div>
        <div class="audit-target">${ev.key_name ? escapeHtml(ev.key_name) + ' ' : ''}${ev.file_path ? '<span style="opacity:0.6">@ ' + escapeHtml(ev.file_path) + '</span>' : ''}</div>
      </div>
    `).join("");
  } catch (e) { showToast(e.message, "err"); }
}

(async function init() {
  const cfg = await fetch("/api/config").then(r => r.json());
  pathInput.value = cfg.default_path;
  SERVICES_INFO = cfg.services || [];
  load();
})();
</script>
</body>
</html>
"""


# ─── HTTP server ─────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        if "200" in (args[1] if len(args) > 1 else ""):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _safe_path(self, raw_path):
        if not raw_path:
            return None, "path required"
        path = os.path.abspath(os.path.expanduser(raw_path))
        if not (path.endswith(".env") or ".env." in os.path.basename(path)):
            return None, "path must be a .env file"
        return path, None

    def do_GET(self):
        url = urlparse(self.path)

        if url.path == "/" or url.path == "/index.html":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if url.path == "/api/config":
            services_info = [
                {"name": s["name"], "color": s["color"],
                 "prefixes": s["prefixes"], "name_hints": s["name_hints"]}
                for s in SERVICES
            ]
            return self._send_json(200, {
                "default_path": DEFAULT_ENV_PATH,
                "services": services_info,
            })

        if url.path == "/api/scan":
            return self._send_json(200, {"files": scan_env_files()})

        if url.path == "/api/env":
            qs = parse_qs(url.query)
            raw = qs.get("path", [""])[0]
            path, err = self._safe_path(raw)
            if err:
                return self._send_json(400, {"error": err})
            entries = parse_env(path)
            unlocked = is_unlocked(self.headers)
            audit_log("read", file_path=path)
            return self._send_json(200, {
                "path": path,
                "exists": os.path.exists(path),
                "unlocked": unlocked,
                "entries": public_view(entries, file_path=path, unlocked=unlocked),
            })

        if url.path == "/api/global-search":
            qs = parse_qs(url.query)
            q = qs.get("q", [""])[0]
            unlocked = is_unlocked(self.headers)
            audit_log("global_search", details={"query": q, "unlocked": unlocked})
            return self._send_json(200, {"results": global_search(q, unlocked=unlocked)})

        if url.path == "/api/audit":
            return self._send_json(200, {"events": audit_recent(50)})

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)

        if url.path == "/api/env":
            body = self._read_json()
            path, err = self._safe_path(body.get("path", ""))
            if err:
                return self._send_json(400, {"error": err})
            key = (body.get("key") or "").strip()
            value = body.get("value", "")
            if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                return self._send_json(400, {"error": "invalid key name"})
            if value == "":
                return self._send_json(400, {"error": "value required"})
            backup_file(path)
            entries = parse_env(path)
            entries = upsert(entries, key, value)
            atomic_write(path, serialize_env(entries))
            svc = detect_service(value, key)
            metadata_set(path, key, service=svc["name"] if svc else None)
            audit_log("write", file_path=path, key_name=key)
            return self._send_json(200, {"ok": True})

        if url.path == "/api/validate":
            if not is_unlocked(self.headers):
                return self._send_json(401, {"error": "unlock required to validate"})
            body = self._read_json()
            path, err = self._safe_path(body.get("path", ""))
            if err:
                return self._send_json(400, {"error": err})
            key = (body.get("key") or "").strip()
            entries = parse_env(path)
            entry = next((e for e in entries if e["kind"] == "kv" and e["key"] == key), None)
            if not entry:
                return self._send_json(404, {"error": "key not found"})
            svc = detect_service(entry["value"], key)
            status = validate_key(entry["value"], svc)
            metadata_set(path, key,
                         validation_status=status,
                         last_validated=now_iso(),
                         service=svc["name"] if svc else None)
            audit_log("validate", file_path=path, key_name=key, details={"status": status})
            return self._send_json(200, {"status": status})

        if url.path == "/api/find-key":
            if not is_unlocked(self.headers):
                return self._send_json(401, {"error": "unlock required"})
            body = self._read_json()
            key = (body.get("key") or "").strip()
            if not key:
                return self._send_json(400, {"error": "key required"})
            return self._send_json(200, {"hits": find_key_everywhere(key)})

        if url.path == "/api/rotate":
            if not is_unlocked(self.headers):
                return self._send_json(401, {"error": "unlock required to rotate"})
            body = self._read_json()
            key = (body.get("key") or "").strip()
            value = body.get("value", "")
            files = body.get("files", [])
            do_validate = bool(body.get("validate", True))
            if not key or not value or not files:
                return self._send_json(400, {"error": "key, value, and files required"})
            updated = 0
            for raw_path in files:
                path, err = self._safe_path(raw_path)
                if err:
                    continue
                if not os.path.exists(path):
                    continue
                backup_file(path)
                entries = parse_env(path)
                entries = upsert(entries, key, value)
                atomic_write(path, serialize_env(entries))
                svc = detect_service(value, key)
                metadata_set(path, key,
                             service=svc["name"] if svc else None,
                             rotated_at=now_iso())
                updated += 1
            audit_log("rotate", key_name=key, details={"files": files, "updated": updated})
            validation = None
            if do_validate:
                svc = detect_service(value, key)
                validation = validate_key(value, svc)
                if updated > 0 and files:
                    last_path, _ = self._safe_path(files[0])
                    if last_path:
                        metadata_set(last_path, key,
                                     validation_status=validation,
                                     last_validated=now_iso())
                audit_log("validate", key_name=key, details={"status": validation, "context": "post-rotation"})
            return self._send_json(200, {"updated": updated, "validation": validation})

        self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        url = urlparse(self.path)
        if url.path != "/api/env":
            return self._send_json(404, {"error": "not found"})
        body = self._read_json()
        path, err = self._safe_path(body.get("path", ""))
        if err:
            return self._send_json(400, {"error": err})
        key = (body.get("key") or "").strip()
        if not key:
            return self._send_json(400, {"error": "key required"})
        backup_file(path)
        entries = parse_env(path)
        entries = delete_key(entries, key)
        atomic_write(path, serialize_env(entries))
        metadata_drop(path, key)
        audit_log("delete", file_path=path, key_name=key)
        return self._send_json(200, {"ok": True})


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


# ─── MCP server mode ─────────────────────────────────────────────────────────
# Implements the Model Context Protocol over stdio (line-delimited JSON-RPC 2.0).
# Spec: https://modelcontextprotocol.io/specification
#
# Tools exposed:
#   list_files       — list every .env file on the machine
#   list_keys        — list keys in a file (names only, no values)
#   get_key          — get a single key's value (requires unlock)
#   set_key          — add or update a key
#   delete_key       — remove a key
#   find             — cross-file search
#   validate         — validate a key against its service API
#   rotate           — update a key in one or many files at once
#   list_services    — list known services with dashboard URLs
#   audit_log        — recent activity log

MCP_TOOLS = [
    {
        "name": "list_files",
        "description": "List every .env file on the machine. Returns absolute paths. No authentication required.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_keys",
        "description": "List the key names in a single .env file. Values are NOT returned. No authentication required.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Absolute path to a .env file"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "get_key",
        "description": "Get a single key's plaintext value from a .env file. Requires the unlock password.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "key": {"type": "string"},
                "password": {"type": "string", "description": "Unlock password (or set API_MANAGER_PASSWORD env var)"},
            },
            "required": ["file", "key"],
        },
    },
    {
        "name": "set_key",
        "description": "Add or update a key in a .env file. Atomic write with automatic backup.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["file", "key", "value"],
        },
    },
    {
        "name": "delete_key",
        "description": "Delete a key from a .env file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "key": {"type": "string"},
            },
            "required": ["file", "key"],
        },
    },
    {
        "name": "find",
        "description": "Search every .env file on the machine for a key name. Values returned only when password is provided.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "password": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "validate",
        "description": "Validate a key by calling the actual service API. Requires unlock password.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "key": {"type": "string"},
                "password": {"type": "string"},
            },
            "required": ["file", "key"],
        },
    },
    {
        "name": "rotate",
        "description": "Update a key with a new value across one or many files in a single operation. Use find first to discover where it lives.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "validate": {"type": "boolean", "default": True},
            },
            "required": ["key", "value", "files"],
        },
    },
    {
        "name": "list_services",
        "description": "List all known services (Anthropic, OpenAI, Stripe, etc.) with their dashboard URLs and validation support.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "audit_log",
        "description": "Return the most recent N events from the audit log.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 50}},
            "required": [],
        },
    },
]


def _mcp_check_password(provided):
    """Allow either explicit password arg or the env var."""
    if provided and hmac.compare_digest(provided, UNLOCK_PASSWORD):
        return True
    # Also accept if API_MANAGER_PASSWORD is set in the env (auto-unlock)
    if os.environ.get("API_MANAGER_PASSWORD") == UNLOCK_PASSWORD:
        return True
    return False


def mcp_call_tool(name, args):
    """Dispatch a tool call. Returns a content list per MCP spec."""
    args = args or {}

    def text_response(s):
        return [{"type": "text", "text": s}]

    if name == "list_files":
        files = scan_env_files()
        return text_response(json.dumps({"files": files, "count": len(files)}, indent=2))

    if name == "list_keys":
        path = os.path.abspath(os.path.expanduser(args.get("file", "")))
        entries = parse_env(path)
        keys = [
            {
                "key": e["key"],
                "service": (detect_service(e["value"], e["key"]) or {}).get("name"),
                "value_length": len(e["value"]),
            }
            for e in entries if e["kind"] == "kv"
        ]
        return text_response(json.dumps({"file": path, "keys": keys}, indent=2))

    if name == "get_key":
        if not _mcp_check_password(args.get("password")):
            return text_response("error: unlock password required (pass `password` arg or set API_MANAGER_PASSWORD env var)")
        path = os.path.abspath(os.path.expanduser(args.get("file", "")))
        key = args.get("key", "")
        entries = parse_env(path)
        entry = next((e for e in entries if e["kind"] == "kv" and e["key"] == key), None)
        if not entry:
            return text_response(f"error: key {key!r} not found in {path}")
        audit_log("mcp_get", file_path=path, key_name=key)
        svc = detect_service(entry["value"], key)
        return text_response(json.dumps({
            "file": path, "key": key, "value": entry["value"],
            "service": svc["name"] if svc else None,
        }, indent=2))

    if name == "set_key":
        path = os.path.abspath(os.path.expanduser(args.get("file", "")))
        key = args.get("key", "")
        value = args.get("value", "")
        if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            return text_response("error: invalid key name")
        if not value:
            return text_response("error: value required")
        backup_file(path)
        entries = parse_env(path)
        entries = upsert(entries, key, value)
        atomic_write(path, serialize_env(entries))
        svc = detect_service(value, key)
        metadata_set(path, key, service=svc["name"] if svc else None)
        audit_log("mcp_set", file_path=path, key_name=key)
        return text_response(f"ok: set {key} in {path}" + (f" (detected: {svc['name']})" if svc else ""))

    if name == "delete_key":
        path = os.path.abspath(os.path.expanduser(args.get("file", "")))
        key = args.get("key", "")
        backup_file(path)
        entries = parse_env(path)
        entries = delete_key(entries, key)
        atomic_write(path, serialize_env(entries))
        metadata_drop(path, key)
        audit_log("mcp_delete", file_path=path, key_name=key)
        return text_response(f"ok: deleted {key} from {path}")

    if name == "find":
        unlocked = _mcp_check_password(args.get("password"))
        results = global_search(args.get("query", ""), unlocked=unlocked)
        audit_log("mcp_find", details={"query": args.get("query"), "unlocked": unlocked, "count": len(results)})
        return text_response(json.dumps({"results": results, "unlocked": unlocked}, indent=2))

    if name == "validate":
        if not _mcp_check_password(args.get("password")):
            return text_response("error: unlock password required")
        path = os.path.abspath(os.path.expanduser(args.get("file", "")))
        key = args.get("key", "")
        entries = parse_env(path)
        entry = next((e for e in entries if e["kind"] == "kv" and e["key"] == key), None)
        if not entry:
            return text_response(f"error: key {key!r} not found")
        svc = detect_service(entry["value"], key)
        status = validate_key(entry["value"], svc)
        metadata_set(path, key, validation_status=status, last_validated=now_iso(),
                     service=svc["name"] if svc else None)
        audit_log("mcp_validate", file_path=path, key_name=key, details={"status": status})
        return text_response(json.dumps({"key": key, "status": status, "service": svc["name"] if svc else None}, indent=2))

    if name == "rotate":
        key = args.get("key", "")
        value = args.get("value", "")
        files = args.get("files", [])
        if not key or not value or not files:
            return text_response("error: key, value, and files required")
        updated = []
        for raw in files:
            path = os.path.abspath(os.path.expanduser(raw))
            if not os.path.exists(path):
                continue
            backup_file(path)
            entries = parse_env(path)
            entries = upsert(entries, key, value)
            atomic_write(path, serialize_env(entries))
            svc = detect_service(value, key)
            metadata_set(path, key, service=svc["name"] if svc else None, rotated_at=now_iso())
            updated.append(path)
        audit_log("mcp_rotate", key_name=key, details={"files": updated})
        validation = None
        if args.get("validate", True):
            svc = detect_service(value, key)
            validation = validate_key(value, svc)
        return text_response(json.dumps({"updated": updated, "count": len(updated), "validation": validation}, indent=2))

    if name == "list_services":
        out = [
            {
                "name": s["name"],
                "dashboard": s["dashboard"],
                "can_validate": s["validate"] is not None,
                "prefixes": s["prefixes"],
            }
            for s in SERVICES
        ]
        return text_response(json.dumps({"services": out}, indent=2))

    if name == "audit_log":
        limit = int(args.get("limit", 50))
        return text_response(json.dumps({"events": audit_recent(limit)}, indent=2))

    return text_response(f"error: unknown tool {name!r}")


def run_mcp_server():
    """JSON-RPC 2.0 over stdio. Reads line-delimited JSON, writes line-delimited JSON."""
    audit_init()
    audit_log("mcp_start")

    def reply(id_, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": id_}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    sys.stderr.write("api-manager MCP server started (stdio)\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        method = req.get("method")
        params = req.get("params", {})
        id_ = req.get("id")

        try:
            if method == "initialize":
                reply(id_, {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "api-manager", "version": "0.2.0"},
                    "capabilities": {"tools": {}},
                })
            elif method == "notifications/initialized":
                pass  # no response needed for notifications
            elif method == "tools/list":
                reply(id_, {"tools": MCP_TOOLS})
            elif method == "tools/call":
                name = params.get("name", "")
                args = params.get("arguments", {})
                content = mcp_call_tool(name, args)
                reply(id_, {"content": content})
            elif method == "ping":
                reply(id_, {})
            else:
                if id_ is not None:
                    reply(id_, error={"code": -32601, "message": f"unknown method {method}"})
        except Exception as e:
            if id_ is not None:
                reply(id_, error={"code": -32603, "message": str(e)})


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if "--mcp" in sys.argv:
        run_mcp_server()
        return

    audit_init()
    audit_log("server_start")

    with ReusableTCPServer((HOST, PORT), Handler) as httpd:
        url = f"http://{HOST}:{PORT}"
        print(f"api-manager running at {url}")
        print(f"default file: {DEFAULT_ENV_PATH}")
        print(f"data dir:     {DATA_DIR}")
        print(f"unlock pw:    set via API_MANAGER_PASSWORD env var (default: Ey5000!!@@)")
        print(f"mcp mode:     python3 api-manager.py --mcp")
        print("ctrl-c to stop.")
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye.")


if __name__ == "__main__":
    main()
