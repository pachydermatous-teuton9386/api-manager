# Adding a new service

Service entries live in the `SERVICES` list near the top of `api-manager.py`. Each entry drives prefix detection, UI badge color, dashboard auto-open, and (optionally) live validation.

## Shape of a service entry

```python
{
    "name": "Anthropic",            # human-readable label shown in UI badges + MCP
    "color": "#cc785c",             # hex badge color (use the brand color)
    "prefixes": ["sk-ant-"],        # key-value prefix strings for auto-detection
    "name_hints": ["ANTHROPIC"],    # env-var-name hints used when prefix doesn't match
    "dashboard": "https://console.anthropic.com/settings/keys",  # "rotate" button target
    "validate": "anthropic",        # key into VALIDATORS dict, or None if not validatable
},
```

## Rules

1. **`prefixes` must be exact leading substrings.** Prefix matching uses `value.startswith(prefix)` directly. Don't include regex syntax.
2. **Sort longer prefixes earlier within a single service.** The matcher already sorts globally by prefix length (longest wins), but if you have multiple prefixes for the same service, keep the most specific one first for readability. Example: OpenAI has `sk-proj-`, `sk-svcacct-`, `sk-None-`, `sk-` in that order.
3. **`name_hints` are a fallback** for env-var naming like `SUPABASE_URL` or `CLOUDFLARE_API_TOKEN` where the value itself has no stable prefix. They're matched against the uppercased key name via `substring in name.upper()`.
4. **Use `validate: None`** if there's no free metadata endpoint you can hit to verify a key. Don't add a paid-tier validator — validation must be free.
5. **Services without prefixes AND without name_hints cannot be auto-detected.** The badge will say "unknown" for their keys. Only do this if there's truly no reliable signal.

## Adding a validator

If the service has a free auth-check endpoint (`/models`, `/user`, `/balance`, `/auth/test`, etc.), add a validator function in the `Validation` section:

```python
def validate_myservice(value):
    status, _ = _http_request(
        "https://api.example.com/v1/user",
        headers={"Authorization": f"Bearer {value}"},
    )
    return status == 200
```

Then register it in the `VALIDATORS` dict:

```python
VALIDATORS = {
    ...
    "myservice": validate_myservice,
}
```

And reference it in the service entry with `"validate": "myservice"`.

**Rules for validators:**

1. **Free endpoints only.** Never call an endpoint that costs API credits. `/v1/models`, `/account`, `/balance`, `/user`, `/auth.test`, `/me`, `/whoami` are typically free.
2. **10-second timeout.** `_http_request` enforces this by default. Don't increase it.
3. **Return `True` for status 200 OR for any auth-passed response.** Some APIs return 400 when auth is correct but the request is malformed (e.g., Anthropic's `/v1/messages` with an intentionally bad body). The `validate_anthropic` function treats `400 + invalid_request_error` as "key works, we just sent garbage intentionally."
4. **Never log or print the key value** in validator code, including in error messages.

## Testing a new service

After adding an entry:

1. Run the web UI and paste a real key for the service into any `.env` file
2. Reload the file and verify the badge shows the correct name + color
3. If you added a validator, click the ✓ button in the UI and verify it returns `valid`
4. Run the MCP mode test: `echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_services","arguments":{}}}' | python3 api-manager.py --mcp` and verify your service shows up
