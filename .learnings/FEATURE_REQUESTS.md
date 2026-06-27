# Feature Requests

## FR-20260627-001: Claude Code MCP server discovery for .mcp.json
- **Capability**: mcp-server-discovery
- **Priority**: high
- **Area**: integration
- **Date**: 2026-06-27
- **Summary**: Claude Code's MCP server configuration requires `.mcp.json` in the project root or working directory. MCP servers cannot be added via `settings.json` (the `mcpServers` key is not a valid field in settings.json schema). The user needs to approve the server on first discovery.
- **User context**: Configured SCAP v2 as MCP server but it didn't appear in the tool list until proper `.mcp.json` placement.
- **Complexity estimate**: simple
- **Suggested implementation**: Always use `.mcp.json` (not settings.json) for MCP server configuration. Place it in the project root (where `.git` is) or in the current working directory.
- **Frequency**: recurring
