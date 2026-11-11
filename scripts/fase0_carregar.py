"""Carrega os CSVs da Olist em um DuckDB local (data/olist.duckdb). Lógica em olist_cx_mcp.pipeline."""
import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

from olist_cx_mcp.pipeline import carregar_csvs

RAIZ = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    load_dotenv(RAIZ / ".env")
    banco = RAIZ / os.getenv("OLIST_DUCKDB_PATH", "data/olist.duckdb")
    banco.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(banco)) as con:
        for tabela, n in carregar_csvs(con, RAIZ / "data" / "raw").items():
            print(f"{tabela:12s} {n:>8,d} linhas")
