"""FastMCP server for {{name}}."""

from mcp.server.fastmcp import FastMCP

# host/port are FastMCP constructor kwargs, not run() arguments. The default
# ``host="127.0.0.1"`` only listens on the loopback interface — unreachable
# from outside the container — so we must override with ``0.0.0.0`` here.
# (Earlier scaffolds passed host/port to ``run()`` which is a TypeError on
# the current FastMCP API; ENG-3901 dry-run F-014.)
mcp = FastMCP("{{name}}", host="0.0.0.0", port=8000)


@mcp.tool()
def hello(name: str = "world") -> str:
    """Say hello — replace this with your tool implementation."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run(transport="sse")
