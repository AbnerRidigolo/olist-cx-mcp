"""Sobe o servidor de verdade (subprocesso) nos dois transportes e chama uma ferramenta."""
import os
import socket
import subprocess
import sys
import time

import pytest
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.anyio


def _env(banco) -> dict[str, str]:
    return {**os.environ, "OLIST_DUCKDB_PATH": str(banco), "PYTHONUTF8": "1"}


async def _verificar(client: Client) -> None:
    nomes = {t.name for t in (await client.list_tools()).tools}
    assert "cx_reason_breakdown" in nomes and len(nomes) == 5
    r = await client.call_tool("cx_reason_breakdown", {})
    assert not r.is_error and r.structured_content["total_classificadas"] == 5


async def test_stdio(banco_sintetico):
    params = StdioServerParameters(command=sys.executable, args=["-m", "olist_cx_mcp"], env=_env(banco_sintetico))
    async with Client(params) as c:
        await _verificar(c)


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_streamable_http(banco_sintetico):
    porta = _porta_livre()
    proc = subprocess.Popen(
        [sys.executable, "-m", "olist_cx_mcp", "--transport", "streamable-http", "--port", str(porta)],
        env=_env(banco_sintetico), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        prazo = time.monotonic() + 30
        while True:
            try:
                socket.create_connection(("127.0.0.1", porta), timeout=0.5).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > prazo:
                    pytest.fail(f"servidor HTTP não subiu: {proc.stderr.read().decode(errors='replace')[-2000:]}")
                time.sleep(0.2)
        async with Client(f"http://127.0.0.1:{porta}/mcp") as c:
            await _verificar(c)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
