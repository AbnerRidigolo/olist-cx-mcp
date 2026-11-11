"""Classifica em lote o motivo de toda avaliação com comentário e salva Parquet.

Saída: data/processed/motivos.parquet e a tabela `motivos` no DuckDB. Chave: (review_id, order_id),
pois review_id se repete na Olist. Só entra review_comment_message (o modelo foi treinado só com ela).
Lógica em olist_cx_mcp.pipeline.
"""
import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

from olist_cx_mcp.classificador import ClassificadorSklearn
from olist_cx_mcp.pipeline import classificar_avaliacoes

RAIZ = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    load_dotenv(RAIZ / ".env")
    banco = RAIZ / os.getenv("OLIST_DUCKDB_PATH", "data/olist.duckdb")
    modelo = ClassificadorSklearn(RAIZ / os.getenv("MOTIVO_MODELO_PATH", "models/motivo/modelo.joblib"))
    saida = RAIZ / "data" / "processed" / "motivos.parquet"
    with duckdb.connect(str(banco)) as con:
        r = classificar_avaliacoes(con, modelo, saida)
    print(f"{r['classificadas']:,d} classificadas em {r['segundos']:.1f}s -> {saida.relative_to(RAIZ)}")
