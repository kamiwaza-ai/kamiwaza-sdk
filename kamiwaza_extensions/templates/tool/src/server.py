"""FastMCP server for {{name}}."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("{{name}}")


@mcp.tool()
def hello(name: str = "world") -> str:
    """Say hello — replace this with your tool implementation."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run(transport="stdio")
