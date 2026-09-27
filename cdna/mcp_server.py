"""Standalone stdio MCP server: symbolic skills, no database or model calls."""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import ValidationError

from .tools import call_skill, tool_definitions


async def serve():
    server = Server("cdna")

    @server.list_tools()
    async def list_tools():
        return [Tool(**definition) for definition in tool_definitions()]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            result = {"state": call_skill(name, arguments)}
        except ValueError as exc:
            reason = (json.dumps(exc.errors(include_input=False, include_context=False, include_url=False))
                      if isinstance(exc, ValidationError) else str(exc))
            result = {"error": reason}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))],
                              structuredContent=result, isError="error" in result)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    asyncio.run(serve())


if __name__ == "__main__":
    main()
