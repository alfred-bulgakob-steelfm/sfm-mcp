"""End-to-end check: MCP client -> sfm_mcp_server (stdio) -> bridge.

    .venv/Scripts/python.exe tests/e2e_client.py [tool] [json-args]
Without arguments runs a fixed smoke sequence.
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "sfm_mcp_server.py"

SMOKE = [
    ("sfm_status", {}),
    ("sfm_exec", {"code": "x = 21\nprint 'hello from', sys.version.split()[0]\nx * 2"}),
    ("sfm_exec", {"code": "x + 1"}),  # namespace persists
    ("sfm_exec", {"code": "1/0"}),  # error path
    ("sfm_exec", {"code": u"u'привет'"}),  # unicode
    ("sfm_api", {"name": "H"}),
    ("sfm_reload_helpers", {}),
    ("sfm_find_assets", {"query": "heavy", "kind": "models", "limit": 5}),
]


def show(content):
    import base64
    import os

    for c in content:
        if getattr(c, "type", None) == "text":
            print(c.text)
        elif getattr(c, "type", None) == "image":
            out = Path(os.environ.get("SFM_MCP_IMG_OUT", ROOT / "tests" / "last_image.png"))
            out.write_bytes(base64.b64decode(c.data))
            print(f"<image saved to {out}>")
        else:
            print(f"<{c.type} content>")


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", ", ".join(t.name for t in tools.tools))
            calls = SMOKE
            if len(sys.argv) > 1:
                args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
                if "code_file" in args:  # read code from a file (no shell quoting)
                    args["code"] = Path(args.pop("code_file")).read_text(encoding="utf-8")
                calls = [(sys.argv[1], args)]
            for name, args in calls:
                print(f"\n=== {name} {json.dumps(args, ensure_ascii=False)[:80]}")
                res = await s.call_tool(name, args)
                show(res.content)


asyncio.run(main())
