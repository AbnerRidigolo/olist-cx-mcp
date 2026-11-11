"""Monta as tabelas cx_* no DuckDB (rode depois de fase0_classificar.py)."""
import os

from pathlib import Path

import duckdb
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]

from olist_cx_mcp.construir import construir  # noqa: E402

load_dotenv(RAIZ / ".env")
with duckdb.connect(str(RAIZ / os.getenv("OLIST_DUCKDB_PATH", "data/olist.duckdb"))) as con:
    for tabela, n in construir(con).items():
        print(f"{tabela:22s} {n:>8,d}")
