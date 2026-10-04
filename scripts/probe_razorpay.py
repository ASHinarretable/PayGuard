"""Connects to the real Razorpay MCP server (Docker) and saves its tool list and sample responses.

    uv run python scripts/probe_razorpay.py [--image razorpay/mcp]

The tool list needs no valid keys. Sample responses are saved only when .env holds real
rzp_test_ keys. Outputs go to tests/adapters/fixtures/razorpay/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path("tests/adapters/fixtures/razorpay")


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def has_real_keys(env: dict[str, str]) -> bool:
    key = env.get("RAZORPAY_KEY_ID", "")
    return key.startswith("rzp_test_") and not key.endswith("xxx")


async def probe(image: str) -> None:
    env = load_env()
    server_env = {
        **os.environ,
        "RAZORPAY_KEY_ID": env.get("RAZORPAY_KEY_ID", "rzp_test_placeholder"),
        "RAZORPAY_KEY_SECRET": env.get("RAZORPAY_KEY_SECRET", "placeholder"),
    }
    params = StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", "-e", "RAZORPAY_KEY_ID", "-e", "RAZORPAY_KEY_SECRET", image],
        env=server_env,
    )
    FIXTURES.mkdir(parents=True, exist_ok=True)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        tools = (await session.list_tools()).tools
        dump = {
            "image": image,
            "server": init.server_info.model_dump(by_alias=True, exclude_none=True),
            "tools": [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools],
        }
        (FIXTURES / "tools.json").write_text(json.dumps(dump, indent=2), encoding="utf-8")
        print(f"{init.server_info.name} {init.server_info.version}: {len(tools)} tools")
        print(" ".join(sorted(t.name for t in tools)))

        if not has_real_keys(env):
            print("No real rzp_test_ keys in .env; skipped sample responses.")
            return
        samples = {}
        listing = await session.call_tool("fetch_all_payments", {"count": 5})
        samples["fetch_all_payments"] = json.loads(listing.content[0].text)
        items = samples["fetch_all_payments"].get("items", [])
        if items:
            one = await session.call_tool("fetch_payment", {"payment_id": items[0]["id"]})
            samples["fetch_payment"] = json.loads(one.content[0].text)
        (FIXTURES / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
        print(f"Saved samples: {len(items)} payments")


def main() -> None:
    parser = argparse.ArgumentParser()
    # The Docker Hub image lags the source; build the current one with
    #   docker build -t razorpay-mcp:main https://github.com/razorpay/razorpay-mcp-server.git
    parser.add_argument("--image", default="razorpay-mcp:main")
    asyncio.run(probe(parser.parse_args().image))


if __name__ == "__main__":
    main()
