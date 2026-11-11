"""Classifica em lote o motivo de toda avaliação com comentário e salva Parquet.

Saída: data/processed/motivos.parquet (review_id, order_id, motivo, confianca, p_<classe>..., modelo)
e a tabela `motivos` no DuckDB, lida desse Parquet. Chave: (review_id, order_id), pois review_id
se repete na Olist. Só entra review_comment_message (o modelo foi treinado só com ela).
"""
import os
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
from olist_cx_mcp.classificador import CLASSES, ClassificadorSklearn  # noqa: E402

SAIDA = RAIZ / "data" / "processed" / "motivos.parquet"
LOTE = 5_000


def main() -> None:
    load_dotenv(RAIZ / ".env")
    banco = RAIZ / os.getenv("OLIST_DUCKDB_PATH", "data/olist.duckdb")
    modelo = ClassificadorSklearn(RAIZ / os.getenv("MOTIVO_MODELO_PATH", "models/motivo/modelo.joblib"))

    with duckdb.connect(str(banco)) as con:
        df = con.execute("""
            SELECT review_id, order_id, review_comment_message AS texto
            FROM avaliacoes
            WHERE trim(coalesce(review_comment_message, '')) <> ''
            ORDER BY review_id, order_id
        """).df()
        print(f"{len(df):,d} avaliações com comentário")

        t0 = time.perf_counter()
        prev = []
        for i in range(0, len(df), LOTE):
            prev += modelo.classificar(df.texto.iloc[i:i + LOTE].tolist())
            print(f"  {min(i + LOTE, len(df)):>6,d}/{len(df):,d}")
        seg = time.perf_counter() - t0

        out = df[["review_id", "order_id"]].copy()
        out["motivo"] = [p.motivo for p in prev]
        out["confianca"] = [round(p.confianca, 4) for p in prev]
        for c in CLASSES:
            out[f"p_{c}"] = [round(p.probabilidades[c], 4) for p in prev]
        out["modelo"] = modelo.nome
        SAIDA.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(SAIDA, index=False)

        con.execute("CREATE OR REPLACE TABLE motivos AS SELECT * FROM read_parquet(?)", [str(SAIDA)])
        print(f"classificadas em {seg:.1f}s ({1000 * seg / len(df):.2f} ms/avaliação) -> {SAIDA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
