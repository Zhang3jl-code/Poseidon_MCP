"""Real stdio MCP handshake and tool invocation; never contacts Poseidon."""
import os
from datetime import timedelta
from pathlib import Path
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_handshake_tools_and_validation(self):
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "POSEIDON_USERNAME": "", "POSEIDON_PASSWORD": "",
               "POSEIDON_PROJECT": "", "POSEIDON_PRODUCT": "", "POSEIDON_ENV": "",
               "POSEIDON_BASE_URL": "https://poseidon.test", "PYTHONUTF8": "1"}
        params = StdioServerParameters(command=sys.executable, args=[str(root / "server.py")], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                await session.initialize()
                response = await session.list_tools()
                names = {tool.name for tool in response.tools}
                self.assertEqual(names, {"check_connection", "list_apps", "list_instances", "search_logs", "get_log_context"})
                for tool in response.tools:
                    self.assertNotIn("password", tool.inputSchema.get("properties", {}))
                    self.assertTrue(tool.annotations.readOnlyHint)
                result = await session.call_tool("search_logs", {
                    "app": "demo", "start": "2026-11-26T15:00:00", "end": "2026-11-26T15:30:00"})
                self.assertTrue(result.isError)
                self.assertIn("project", result.content[0].text)
                auth = await session.call_tool("check_connection", {})
                self.assertTrue(auth.isError)
                self.assertIn("POSEIDON_USERNAME", auth.content[0].text)


if __name__ == "__main__":
    unittest.main()
