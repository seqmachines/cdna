import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from cdna import reverse_transcribe, template_switch
from cdna.tools import call_skill, tool_definitions


def test_host_dispatch_matches_python_api(rna):
    data = rna.model_dump()
    hybrid = call_skill("reverse_transcribe", {"rna_state": data, "primer": "RT primer"})
    assert hybrid == reverse_transcribe(data, "RT primer")
    assert call_skill("template_switch", {"hybrid": hybrid, "tso": "TSO handle"}) == (
        template_switch(hybrid, "TSO handle")
    )
    with pytest.raises(ValueError, match="Unknown"):
        call_skill("missing", {})


@pytest.mark.parametrize("entrypoint", ["console", "module"])
def test_stdio_tools_and_errors(rna, tmp_path, entrypoint):
    if entrypoint == "console":
        executable = "cdna-mcp.exe" if sys.platform == "win32" else "cdna-mcp"
        command = str(Path(sys.executable).with_name(executable))
        args = []
    else:
        command = sys.executable
        args = ["-m", "cdna.mcp_server"]
    parameters = StdioServerParameters(command=command, args=args, cwd=str(tmp_path), env={})

    async def run():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                listed = (await client.list_tools()).tools
                assert {tool.name for tool in listed} == {"reverse_transcribe", "template_switch"}
                expected = {tool["name"]: tool["inputSchema"] for tool in tool_definitions()}
                assert {tool.name: tool.inputSchema for tool in listed} == expected

                async def call(name, arguments, *, error=False):
                    response = await client.call_tool(name, arguments)
                    assert response.isError is error
                    assert len(response.content) == 1
                    assert json.loads(response.content[0].text) == response.structuredContent
                    if error:
                        assert set(response.structuredContent) == {"error"}
                        assert response.structuredContent["error"]
                    else:
                        assert set(response.structuredContent) == {"state"}
                    return response.structuredContent

                substrate = rna.model_dump()
                hybrid = (await call("reverse_transcribe", {
                    "rna_state": substrate, "primer": "RT primer",
                }))["state"]
                assert hybrid == reverse_transcribe(substrate, "RT primer")
                switched = (await call("template_switch", {
                    "hybrid": hybrid, "tso": "TSO handle",
                }))["state"]
                assert switched == template_switch(hybrid, "TSO handle")
                await call("reverse_transcribe", {"rna_state": hybrid, "primer": "RT primer"}, error=True)
                await call("template_switch", {"hybrid": substrate, "tso": "TSO handle"}, error=True)
                await call("template_switch", {"hybrid": hybrid, "tso": "ACGTACGTACGT"}, error=True)
                await call("reverse_transcribe", {"rna_state": {}, "primer": "RT primer"}, error=True)
                await call("reverse_transcribe", {"rna_state": substrate, "primer": ""}, error=True)
                await call("missing", {}, error=True)

    async def bounded_run():
        async with asyncio.timeout(30):
            await run()

    asyncio.run(bounded_run())
