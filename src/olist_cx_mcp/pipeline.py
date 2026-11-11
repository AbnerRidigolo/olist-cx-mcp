"""Pipeline de dados: CSVs da Olist -> DuckDB -> motivos (Parquet) -> tabelas cx_*.

Usado pelos scripts da Fase 0/1, pela fixture dos testes e pelo Dockerfile:
    python -m olist_cx_mcp.pipeline --raw data/raw --banco data/olist.duckdb
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from .classificador import CLASSES, Classificador, ClassificadorSklearn
from .construir import construir

TABELAS = {
    "pedidos": ("olist_orders_dataset.csv", {
        "order_id": "VARCHAR", "customer_id": "VARCHAR", "order_status": "VARCHAR",
        "order_purchase_timestamp": "TIMESTAMP", "order_approved_at": "TIMESTAMP",
        "order_delivered_carrier_date": "TIMESTAMP", "order_delivered_customer_date": "TIMESTAMP",
        "order_estimated_delivery_date": "TIMESTAMP"}),
    "itens": ("olist_order_items_dataset.csv", {
        "order_id": "VARCHAR", "order_item_id": "INTEGER", "product_id": "VARCHAR",
        "seller_id": "VARCHAR", "shipping_limit_date": "TIMESTAMP", "price": "DECIMAL(10,2)",
        "freight_value": "DECIMAL(10,2)"}),
    "vendedores": ("olist_sellers_dataset.csv", {
        "seller_id": "VARCHAR", "seller_zip_code_prefix": "VARCHAR", "seller_city": "VARCHAR",
        "seller_state": "VARCHAR"}),
    "clientes": ("olist_customers_dataset.csv", {
        "customer_id": "VARCHAR", "customer_unique_id": "VARCHAR",
        "customer_zip_code_prefix": "VARCHAR", "customer_city": "VARCHAR", "customer_state": "VARCHAR"}),
    "produtos": ("olist_products_dataset.csv", {
        "product_id": "VARCHAR", "product_category_name": "VARCHAR", "product_name_lenght": "INTEGER",
        "product_description_lenght": "INTEGER", "product_photos_qty": "INTEGER",
        "product_weight_g": "INTEGER", "product_length_cm": "INTEGER", "product_height_cm": "INTEGER",
        "product_width_cm": "INTEGER"}),
    "avaliacoes": ("olist_order_reviews_dataset.csv", {
        "review_id": "VARCHAR", "order_id": "VARCHAR", "review_score": "INTEGER",
        "review_comment_title": "VARCHAR", "review_comment_message": "VARCHAR",
        "review_creation_date": "TIMESTAMP", "review_answer_timestamp": "TIMESTAMP"}),
    "categorias": ("product_category_name_translation.csv", {
        "product_category_name": "VARCHAR", "product_category_name_english": "VARCHAR"}),
}
LOTE = 5_000


def carregar_csvs(con: duckdb.DuckDBPyConnection, pasta_raw: Path) -> dict[str, int]:
    contagens = {}
    for tabela, (arquivo, colunas) in TABELAS.items():
        csv = Path(pasta_raw) / arquivo
        if not csv.exists():
            raise FileNotFoundError(f"falta {csv}; rode antes: python scripts\fase0_baixar.py")
        # nome da tabela vem das constantes acima, nunca de entrada externa
        con.execute(f"CREATE OR REPLACE TABLE {tabela} AS SELECT * FROM read_csv(?, header=true, columns=?)",
                    [str(csv), colunas])
        contagens[tabela] = con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]
    return contagens


def classificar_avaliacoes(con: duckdb.DuckDBPyConnection, modelo: Classificador, parquet: Path) -> dict:
    """Classifica toda avaliação com review_comment_message, grava Parquet e a tabela `motivos`."""
    import pandas as pd

    df = con.execute("""
        SELECT review_id, order_id, review_comment_message AS texto FROM avaliacoes
        WHERE trim(coalesce(review_comment_message, '')) <> '' ORDER BY review_id, order_id
    """).df()
    t0 = time.perf_counter()
    prev = []
    for i in range(0, len(df), LOTE):
        prev += modelo.classificar(df.texto.iloc[i:i + LOTE].tolist())
    seg = time.perf_counter() - t0
    out = pd.DataFrame({"review_id": df.review_id, "order_id": df.order_id,
                        "motivo": [p.motivo for p in prev], "confianca": [round(p.confianca, 4) for p in prev]})
    for c in CLASSES:
        out[f"p_{c}"] = [round(p.probabilidades[c], 4) for p in prev]
    out["modelo"] = modelo.nome
    parquet = Path(parquet)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(parquet, index=False)
    con.execute("CREATE OR REPLACE TABLE motivos AS SELECT * FROM read_parquet(?)", [str(parquet)])
    return {"classificadas": len(out), "segundos": seg}


def construir_banco(pasta_raw: Path, banco: Path, modelo: Classificador, parquet: Path) -> None:
    banco = Path(banco)
    banco.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(banco)) as con:
        carregar_csvs(con, pasta_raw)
        classificar_avaliacoes(con, modelo, parquet)
        construir(con)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--banco", default="data/olist.duckdb")
    ap.add_argument("--modelo", default="models/motivo/modelo.joblib")
    ap.add_argument("--parquet", default="data/processed/motivos.parquet")
    a = ap.parse_args()
    construir_banco(Path(a.raw), Path(a.banco), ClassificadorSklearn(a.modelo), Path(a.parquet))
    print(f"banco pronto em {a.banco}")


if __name__ == "__main__":
    main()
