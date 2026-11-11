"""Ponto de entrada: python -m olist_cx_mcp [--transport stdio|streamable-http]."""
import argparse
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    ap = argparse.ArgumentParser(prog="olist-cx-mcp")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    from .servidor import criar_servidor

    mcp = criar_servidor()
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
