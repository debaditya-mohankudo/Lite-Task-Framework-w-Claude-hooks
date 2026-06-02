"""MCP server for claude-hooks — memory and session tools."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mcp.server.fastmcp import FastMCP
from tools.memory import (
    handle_add, handle_search, handle_list, handle_list_domains,
    handle_get, handle_delete, handle_tool_hints, handle_read_compact,
)
from tools.session import (
    handle_list as session_list,
    handle_list_all as session_list_all,
    handle_get as session_get,
    handle_keywords,
    handle_tasks,
    handle_persist,
    handle_delete as session_delete,
    handle_save_summary,
    handle_get_summaries,
    handle_delete_summary,
    handle_search as session_search,
)

mcp = FastMCP("claude-hooks")

# memory tools
mcp.tool(name="memory__add")(handle_add)
mcp.tool(name="memory__search")(handle_search)
mcp.tool(name="memory__list")(handle_list)
mcp.tool(name="memory__list_domains")(handle_list_domains)
mcp.tool(name="memory__get")(handle_get)
mcp.tool(name="memory__delete")(handle_delete)
mcp.tool(name="memory__tool_hints")(handle_tool_hints)
mcp.tool(name="memory__read_compact")(handle_read_compact)

# session tools
mcp.tool(name="session__list")(session_list)
mcp.tool(name="session__list_all")(session_list_all)
mcp.tool(name="session__get")(session_get)
mcp.tool(name="session__keywords")(handle_keywords)
mcp.tool(name="session__tasks")(handle_tasks)
mcp.tool(name="session__persist")(handle_persist)
mcp.tool(name="session__delete")(session_delete)
mcp.tool(name="session__save_summary")(handle_save_summary)
mcp.tool(name="session__get_summaries")(handle_get_summaries)
mcp.tool(name="session__delete_summary")(handle_delete_summary)
mcp.tool(name="session__search")(session_search)

if __name__ == "__main__":
    mcp.run()
