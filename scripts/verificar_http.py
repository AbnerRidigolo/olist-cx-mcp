"""Conecta num servidor olist-cx-mcp via Streamable HTTP, lista as ferramentas e chama uma.

Uso: python scripts/verificar_http.py http://127.0.0.1:8000/mcp
"""
import asyncio
import sys
import time

from mcp import Client


async def main(url: str) -> None:
    for tentativa in range(30):
        try:
            async with Client(url) as c:
                nomes = sorted(t.name for t in (await c.list_tools()).tools)
                r = await c.call_tool("cx_reason_breakdown", {})
                break
        except Exception:
            if tentativa == 29:
                raise
            time.sleep(2)
    print("ferramentas:", ", ".join(nomes))
    print(r.content[0].text)
    assert len(nomes) == 5 and not r.is_error


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"))
