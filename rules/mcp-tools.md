# MCP tool definitions

`api-manager` exposes a stdio MCP server when invoked with `--mcp`. Tool definitions live in the `MCP_TOOLS` list and the dispatcher is `mcp_call_tool()`. This doc covers how to add, modify, or flag a tool.

## Tool shape

Each entry in `MCP_TOOLS` follows the [Model Context Protocol spec](https://modelcontextprotocol.io/specification):

```python
{
    "name": "get_key",
    "description": "Get a single key's plaintext value from a .env file. Requires the unlock password.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "file": {"type": "string"},
            "key": {"type": "string"},
            "password": {"type": "string", "description": "..."},
        },
        "required": ["file", "key"],
    },
},
```

Then add a dispatch case in `mcp_call_tool()`:

```python
if name == "get_key":
    if not _mcp_check_password(args.get("password")):
        return text_response("error: unlock password required")
    # ... implementation
    return text_response(json.dumps(result, indent=2))
```

## The `modifiesState` convention

Tool behavior must be classified based on whether it mutates on-disk state. This controls whether the tool can be used in read-only contexts and which tools require unlock.

| Category | Tools | Requires unlock? |
|---|---|---|
| **Read-only, no values** | `list_files`, `list_keys`, `list_services`, `audit_log` | No |
| **Read-only, reveals values** | `get_key`, `find` (with values) | **Yes** |
| **State-modifying** | `set_key`, `delete_key`, `rotate`, `validate` | Varies |

Rules:

1. **Reading a key's value is a privileged operation.** `get_key` and `find` (when called with the password) both return plaintext secrets and must check the unlock.
2. **Writing is NOT currently gated by unlock.** This matches the web UI's behavior — anyone who can edit the file directly can also update it via the tool. Don't change this without updating the security model doc.
3. **`validate` is gated by unlock** because it sends the key value to an external service and someone watching network traffic could intercept it. Validation is "reading with side effects."
4. **Every state-modifying tool must call `backup_file(path)` before writing** and `audit_log(...)` after. No exceptions. Both are cheap and both are required.

## Checking the unlock password in MCP mode

Use `_mcp_check_password(args.get("password"))`. It accepts:

- The password passed as a tool argument: `{"password": "..."}`
- The `API_MANAGER_PASSWORD` environment variable set on the server process

The second path is how Claude Code configures the server: users set the env var in their `.mcp.json` and then Claude Code's tool calls don't need to pass the password every time.

## Adding a new tool — checklist

1. Add the tool definition to `MCP_TOOLS` list — name, description, inputSchema
2. Add the dispatch case in `mcp_call_tool()`
3. Decide: does it need unlock? If yes, call `_mcp_check_password` first
4. If state-modifying: call `backup_file` before, `audit_log` after
5. Return `text_response(json.dumps(..., indent=2))` for structured data, or `text_response(f"ok: ...")` for simple success
6. Add a line to the tools list in README.md
7. Test with a stdio JSON-RPC request:
   ```bash
   echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"my_tool","arguments":{}}}' | python3 api-manager.py --mcp
   ```

## What MCP tools should NOT do

- **Print to stdout.** Only JSON-RPC messages go to stdout. Use `sys.stderr.write(...)` for debug output.
- **Exit the process.** Never call `sys.exit()` from a tool handler. Raise and let the dispatcher catch.
- **Block indefinitely.** Validation timeouts exist for a reason. If a tool can hang, give it an explicit timeout.
- **Return secrets in error messages.** If validation fails because the key is malformed, don't echo the malformed value back. Just say "invalid key format."
