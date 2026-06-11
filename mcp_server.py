"""claude-hooks MCP server — self-contained tool server for task, memory, session, hooks tools."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mcp.server.fastmcp import FastMCP
from dispatcher import build_dispatcher

mcp = FastMCP("claude-hooks")
build_dispatcher(mcp)

if __name__ == "__main__":
    mcp.run()
