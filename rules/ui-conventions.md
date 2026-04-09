# UI conventions

`api-manager` has an embedded web UI in `api-manager.py`. The HTML, CSS, and JavaScript all live in the `HTML` string constant. This doc is for anyone modifying the UI.

## Core invariant

**Single file. Stdlib only. No build step.**

That means:

- No `pip install` dependencies in the runtime path
- No `npm install`, no webpack, no bundler, no transpiler
- No `fetch(...).then(...).catch(...)` chained to infinity — use `async/await` directly because the modern browsers we target support it
- No TypeScript, no JSX, no Tailwind at build-time (we use plain CSS)
- No framework. Vanilla DOM via `document.querySelector`, aliased as `$`.

If you find yourself wanting a framework or a build step, stop. The whole product thesis is that users should run `curl -O ... && python3 api-manager.py` and have a working tool. Every dependency you add to the runtime path breaks that promise.

## HTML structure

The UI is one page, no routing. Panels stack vertically:

1. **Topbar** — title + lock state (top right)
2. **File panel** — path input + load/scan buttons
3. **Add or update panel** — new-key form with live service detection
4. **Existing keys panel** — filter input + key list with per-row actions
5. **Search all .env files panel** — collapsible, default collapsed
6. **Recent activity panel** — collapsible, default collapsed

Modal overlays (like the rotation dialog) use `#modal` with `.modal-backdrop`.

New features should slot into one of the existing panels or appear as a new collapsible panel at the bottom. **Do not add a navigation bar or route structure.** Don't add tabs. The whole UI fits on one page and that's a feature.

## CSS conventions

- **CSS custom properties for design tokens** at `:root` — `--bg`, `--panel`, `--ink`, `--muted`, `--line`, `--accent`, `--danger`, `--ok`, `--warn`, `--radius`. Reference these everywhere. Don't hardcode colors.
- **System font stack** — `-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif`. Don't load web fonts.
- **Monospace where it matters** — key names, values, file paths all use `ui-monospace, "SF Mono", Menlo, monospace`.
- **Apple-minimalism aesthetic** — lots of white space, small font sizes (12-15px), rounded corners (8-10px), subtle borders (`1px solid var(--line)`), no shadows except on modals.
- **Transitions** — 0.12s ease for hover, 0.15s ease for toggles, 0.2s for toasts. Don't get fancy with spring physics.

## JS conventions

- `const $ = (s) => document.querySelector(s);` — always use this, don't call `document.querySelector` directly
- `escapeHtml(str)` — always wrap user-provided strings when interpolating into `innerHTML`. Never use `innerHTML = \`${userInput}\`` directly.
- `async/await` — no `.then()` chains
- `fetch` — no wrapper library. Use the native API.
- **No global state beyond `lastData`, `unlockPw`, and `SERVICES_INFO`.** These three cover everything. Adding more global variables means you're probably doing state management wrong.
- **Event listeners via `.onclick = ...`** rather than `addEventListener`. Simpler, easier to read, easier to override. We don't need multi-listener semantics.

## Adding a new panel

```html
<div class="panel">
  <h2>panel title</h2>
  <div class="row">
    <!-- ... -->
  </div>
</div>
```

For collapsible panels:

```html
<div class="panel collapsible" id="myPanel">
  <h2>▸ panel title</h2>  <!-- the arrow comes from CSS -->
  <div class="body">
    <!-- content, hidden by default via .collapsible .body { display: none } -->
  </div>
</div>
```

Then wire it up in the collapsible-toggle code near the bottom of the `<script>` block.

## Adding a new API endpoint

Add a case to `do_GET` / `do_POST` / `do_DELETE` in the `Handler` class. Use `self._send_json(status, payload)` to respond. Follow the error-classification pattern (see [error-classification.md](./error-classification.md)).

For the frontend side, add a fetch call using `api(method, path, body)` (for JSON endpoints) or a direct `fetch()` (for GET endpoints with query strings).

## What to keep out of the UI

- **No telemetry.** We're not tracking anything. No analytics, no error reporting, no usage pings. The audit log is local-only.
- **No autocomplete / LLM suggestions in the add-key form.** Service detection is already there; that's enough.
- **No multi-file batch operations from the main panel.** Those go in the rotation modal, which has explicit file selection.
- **No sync-to-cloud features.** Ever. This tool does not leave the machine.
