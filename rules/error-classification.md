# Error classification

`api-manager` uses classified errors (`ApiManagerError` + `ApiManagerErrorKind`) so that user-facing errors can be distinguished from actual bugs. This matters once you wire up error reporting (Sentry, PostHog, etc.) — without classification, the error dashboard drowns in high-volume user mistakes ("invalid path") and the actual bugs get lost.

Pattern adapted from [dyad-sh/dyad's `DyadError`](https://github.com/dyad-sh/dyad/blob/main/rules/dyad-errors.md).

## When to use `ApiManagerError`

Throw `ApiManagerError` when the failure is **expected, classifiable, and not a product bug**:

| Kind | Use for |
|---|---|
| `Validation` | Invalid input — malformed key name, missing required field, bad value format |
| `NotFound` | Requested file, key, or metadata entry doesn't exist |
| `Auth` | Unlock password missing or wrong |
| `Precondition` | Wrong state for the operation — unlock required, file not writable |
| `Conflict` | Duplicate key, concurrent modification |
| `UserCancelled` | User declined a prompt or dismissed a dialog |
| `RateLimited` | Hit a service's rate limit during validation |
| `External` | Upstream service failure — network error, API returned 5xx |
| `Internal` | Bug, invariant violation, unexpected state — *always reported* |

## When to use a plain `Exception`

Almost never. If you find yourself writing `raise Exception(...)`, stop and pick a kind from the table above. The only exception (heh) is third-party library exceptions that you catch and re-raise with context.

## Writing a new throw site

```python
from api_manager_errors import ApiManagerError, ApiManagerErrorKind

# Good — classified
raise ApiManagerError("invalid key name", ApiManagerErrorKind.Validation)

# Bad — unclassified, telemetry can't filter this
raise Exception("invalid key name")
```

## Writing a new catch site

```python
try:
    ...
except ApiManagerError as e:
    if e.kind == ApiManagerErrorKind.Validation:
        return self._send_json(400, {"error": str(e)})
    if e.kind == ApiManagerErrorKind.Auth:
        return self._send_json(401, {"error": str(e)})
    if e.kind == ApiManagerErrorKind.NotFound:
        return self._send_json(404, {"error": str(e)})
    # External, Internal, Unknown → 500 and LOG
    return self._send_json(500, {"error": "internal error"})
```

The `ApiManagerError.to_http_status()` helper does this mapping automatically; use it at the HTTP boundary.

## Why this matters for reporting

When you wire up error reporting later, you want the report to look like this:

```
20 exceptions/day
  - 17 Internal (real bugs)
  - 3 External (upstream flakiness)
```

NOT like this:

```
450 exceptions/day
  - 320 Validation (users typing bad key names)
  - 80 NotFound (users loading files that don't exist)
  - 30 Auth (users mistyping the password)
  - 20 Internal (real bugs, buried)
```

The filter is: **user-shaped errors never get reported.** `Internal` and `External` always do. `Unknown` does until you migrate the call site to a proper kind.

## Migration policy

If you find a `raise Exception(...)` in `api-manager.py`, migrate it. The most common pattern:

```python
# before
if not os.path.exists(path):
    raise Exception(f"file not found: {path}")

# after
if not os.path.exists(path):
    raise ApiManagerError(f"file not found: {path}", ApiManagerErrorKind.NotFound)
```

The rule of thumb: **if a user can cause the error by doing something reasonable, it's not a bug. Classify it.**
