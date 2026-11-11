"""Monta as tabelas analíticas `cx_*` que o servidor consulta (a partir das tabelas brutas e de `motivos`).

- cx_pedidos: um pedido por linha, com estado/cidade do cliente e métricas de entrega.
  Atraso = data de entrega ao cliente posterior à data estimada (comparação por dia), só pedidos entregues.
- cx_pedido_vendedor: pedido x vendedor (um pedido pode ter mais de um vendedor).
- cx_pedido_categoria: pedido x categoria de produto (pt e en).
- cx_avaliacoes: uma avaliação por (review_id, order_id), com motivo quando há comentário.
"""
import duckdb

SQL = [
    """
    CREATE OR REPLACE TABLE cx_pedidos AS
    SELECT p.order_id, c.customer_state AS estado_cliente, c.customer_city AS cidade_cliente,
           p.order_status AS status,
           CAST(p.order_purchase_timestamp AS DATE) AS data_compra,
           CAST(p.order_delivered_customer_date AS DATE) AS data_entrega,
           CAST(p.order_estimated_delivery_date AS DATE) AS data_estimada,
           (p.order_status = 'delivered' AND p.order_delivered_customer_date IS NOT NULL) AS entregue,
           CASE WHEN p.order_status = 'delivered' AND p.order_delivered_customer_date IS NOT NULL
                THEN date_diff('day', CAST(p.order_estimated_delivery_date AS DATE),
                               CAST(p.order_delivered_customer_date AS DATE)) END AS dias_atraso,
           CASE WHEN p.order_status = 'delivered' AND p.order_delivered_customer_date IS NOT NULL
                THEN date_diff('day', CAST(p.order_purchase_timestamp AS DATE),
                               CAST(p.order_delivered_customer_date AS DATE)) END AS dias_ate_entrega
    FROM pedidos p JOIN clientes c USING (customer_id)
    """,
    """
    CREATE OR REPLACE TABLE cx_pedido_vendedor AS
    SELECT i.order_id, i.seller_id AS vendedor_id, v.seller_state AS estado_vendedor,
           v.seller_city AS cidade_vendedor, count(*) AS n_itens, sum(i.price) AS valor_itens
    FROM itens i JOIN vendedores v USING (seller_id)
    GROUP BY ALL
    """,
    """
    CREATE OR REPLACE TABLE cx_pedido_categoria AS
    SELECT DISTINCT i.order_id, coalesce(pr.product_category_name, 'sem_categoria') AS categoria,
           coalesce(ct.product_category_name_english, 'no_category') AS categoria_en
    FROM itens i JOIN produtos pr USING (product_id)
    LEFT JOIN categorias ct USING (product_category_name)
    """,
    """
    CREATE OR REPLACE TABLE cx_avaliacoes AS
    SELECT a.review_id, a.order_id, a.review_score AS nota,
           nullif(trim(a.review_comment_title), '') AS titulo,
           nullif(trim(a.review_comment_message), '') AS comentario,
           CAST(a.review_creation_date AS DATE) AS data_avaliacao,
           m.motivo, m.confianca, m.modelo
    FROM avaliacoes a LEFT JOIN motivos m USING (review_id, order_id)
    """,
]


def construir(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    for sql in SQL:
        con.execute(sql)
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("cx_pedidos", "cx_pedido_vendedor", "cx_pedido_categoria", "cx_avaliacoes")}
