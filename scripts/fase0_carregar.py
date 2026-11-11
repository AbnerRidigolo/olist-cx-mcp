"""Carrega os CSVs da Olist em um DuckDB local (data/olist.duckdb).

Tabelas: pedidos, itens, vendedores, clientes, produtos, avaliacoes (+ categorias, tradução pt->en).
Os tipos são declarados explicitamente para não depender da inferência do leitor de CSV.
"""
import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
RAW = RAIZ / "data" / "raw"

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


def caminho_banco() -> Path:
    load_dotenv(RAIZ / ".env")
    return RAIZ / os.getenv("OLIST_DUCKDB_PATH", "data/olist.duckdb")


def main() -> None:
    banco = caminho_banco()
    banco.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(banco)) as con:
        for tabela, (arquivo, colunas) in TABELAS.items():
            csv = RAW / arquivo
            if not csv.exists():
                raise SystemExit(f"falta {csv}; rode antes: python scripts\fase0_baixar.py")
            # nomes de tabela/arquivo vêm deste módulo (constantes), não de entrada do usuário
            con.execute(
                f"CREATE OR REPLACE TABLE {tabela} AS SELECT * FROM read_csv(?, header=true, columns=?)",
                [str(csv), colunas],
            )
            n = con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]
            print(f"{tabela:12s} {n:>8,d} linhas")


if __name__ == "__main__":
    main()
