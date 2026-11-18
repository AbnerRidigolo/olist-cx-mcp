"""10 perguntas de analista de CX, cada uma exigindo várias chamadas, com gabarito calculado por SQL.

O gabarito lê só as tabelas brutas (pedidos, itens, clientes, produtos, avaliacoes) e `motivos` (saída do
classificador). Não usa as tabelas cx_* nem o código das ferramentas, para conferir o servidor de forma
independente. Definições (as mesmas documentadas no servidor):
- avaliação classificada: linha de `motivos` (avaliação com comentário), chave (review_id, order_id);
- vendedor/categoria de uma avaliação: o pedido tem item daquele vendedor/categoria (pode haver vários);
- pedido entregue: status 'delivered' com data de entrega; atrasado: data de entrega > data estimada (por dia).
Percentuais com 1 casa decimal. Cada gabarito confere que não há empate na precisão pedida.

Uso: python eval/perguntas.py  -> imprime o gabarito e grava eval/gabarito.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

RAIZ = Path(__file__).resolve().parents[1]

AV = """(SELECT m.review_id, m.order_id, m.motivo, a.review_score AS nota,
                CAST(a.review_creation_date AS DATE) AS data_av, cl.customer_state AS uf
         FROM motivos m JOIN avaliacoes a USING (review_id, order_id)
         JOIN pedidos p ON p.order_id = a.order_id JOIN clientes cl ON cl.customer_id = p.customer_id)"""
ENT = """(SELECT p.order_id, cl.customer_state AS uf, CAST(p.order_purchase_timestamp AS DATE) AS data_compra,
                CAST(p.order_delivered_customer_date AS DATE) > CAST(p.order_estimated_delivery_date AS DATE) AS atrasado
         FROM pedidos p JOIN clientes cl USING (customer_id)
         WHERE p.order_status = 'delivered' AND p.order_delivered_customer_date IS NOT NULL)"""
CAT = "coalesce(pr.product_category_name, 'sem_categoria')"


def _linhas(con, sql, params=None):
    return con.execute(sql, params or []).fetchall()


def _sem_empate(linhas, idx_valor=1, casas=1):
    a, b = round(linhas[0][idx_valor], casas), round(linhas[1][idx_valor], casas)
    assert a != b, f"empate na precisão pedida: {linhas[:2]}"


def _pct_atraso(con, where: str, params=None) -> float:
    return round(_linhas(con, f"SELECT 100.0 * avg(atrasado::INT) FROM {ENT} e WHERE {where}", params)[0][0], 1)


# ---------------------------------------------------------------- gabaritos

def g1(con):
    r = _linhas(con, f"""SELECT uf, 100.0 * avg((motivo = 'atraso_ou_nao_entrega')::INT) p, count(*) n
                         FROM {AV} GROUP BY uf HAVING count(*) >= 1000 ORDER BY p DESC""")
    _sem_empate(r)
    uf = r[0][0]
    return {"estado": uf, "pct_reclamacao_atraso": round(r[0][1], 1),
            "pct_pedidos_atrasados": _pct_atraso(con, "e.uf = ?", [uf])}


def g2(con):
    r = _linhas(con, f"""SELECT i.seller_id, count(DISTINCT (x.review_id, x.order_id)) n
                         FROM {AV} x JOIN itens i ON i.order_id = x.order_id
                         WHERE x.nota <= 2 AND x.data_av BETWEEN '2018-01-01' AND '2018-12-31'
                         GROUP BY 1 ORDER BY n DESC""")
    _sem_empate(r, casas=0)
    v = r[0][0]
    nota = _linhas(con, """SELECT avg(review_score) FROM avaliacoes
                           WHERE order_id IN (SELECT order_id FROM itens WHERE seller_id = ?)""", [v])[0][0]
    return {"vendedor_id": v, "nota_media": round(nota, 2)}


def g3(con):
    r = _linhas(con, f"""SELECT {CAT} cat, count(DISTINCT (x.review_id, x.order_id)) n
                         FROM {AV} x JOIN itens i ON i.order_id = x.order_id JOIN produtos pr USING (product_id)
                         WHERE x.motivo = 'defeito_ou_avaria' GROUP BY 1 ORDER BY n DESC""")
    _sem_empate(r, casas=0)
    cat = r[0][0]
    pct = _pct_atraso(con, f"""e.order_id IN (SELECT i.order_id FROM itens i JOIN produtos pr USING (product_id)
                                             WHERE {CAT} = ?)""", [cat])
    return {"categoria": cat, "pct_pedidos_atrasados": pct}


def g4(con):
    r = _linhas(con, f"""SELECT rota, 100.0 * avg(atrasado::INT) p, count(*) n FROM (
                           SELECT DISTINCT e.order_id, e.atrasado, v.seller_state || '->' || e.uf AS rota
                           FROM {ENT} e JOIN itens i USING (order_id) JOIN vendedores v USING (seller_id))
                         GROUP BY rota HAVING count(*) >= 500 ORDER BY p DESC""")
    _sem_empate(r)
    rota = r[0][0]
    destino = rota.split("->")[1]
    m = _linhas(con, f"""SELECT motivo, count(*) n FROM {AV} WHERE uf = ? AND motivo <> 'elogio'
                         GROUP BY 1 ORDER BY n DESC""", [destino])
    _sem_empate(m, casas=0)
    return {"rota": rota, "pct_pedidos_atrasados": round(r[0][1], 1), "motivo_sem_elogio": m[0][0]}


def g5(con):
    r = dict(_linhas(con, f"""SELECT strftime(data_av, '%Y-%m'), count(*) FROM {AV}
                              WHERE motivo = 'produto_errado_ou_incompleto'
                                AND data_av BETWEEN '2018-02-01' AND '2018-03-31' GROUP BY 1"""))
    return {"fevereiro": r["2018-02"], "marco": r["2018-03"]}


def g6(con):
    top = _linhas(con, f"""SELECT i.seller_id, count(DISTINCT (x.review_id, x.order_id)) n
                           FROM {AV} x JOIN itens i ON i.order_id = x.order_id GROUP BY 1 ORDER BY n DESC LIMIT 4""")
    _sem_empate(top[2:], casas=0)  # o 3º e o 4º não empatam
    taxas = sorted(((v, 100.0 * _linhas(con, f"""SELECT avg(atrasado::INT) FROM {ENT} e WHERE e.order_id IN
                       (SELECT order_id FROM itens WHERE seller_id = ?)""", [v])[0][0]) for v, _ in top[:3]),
                   key=lambda t: -t[1])
    _sem_empate(taxas)
    return {"vendedor_id": taxas[0][0], "pct_pedidos_atrasados": round(taxas[0][1], 1)}


def g7(con):
    r = _linhas(con, f"""SELECT strftime(data_compra, '%Y-%m') mes, 100.0 * avg(atrasado::INT) p FROM {ENT}
                         WHERE uf = 'SP' AND data_compra BETWEEN '2018-01-01' AND '2018-08-31'
                         GROUP BY 1 ORDER BY p DESC""")
    _sem_empate(r)
    return {"mes": r[0][0], "pct_pedidos_atrasados": round(r[0][1], 1)}


VENDEDOR_Q8 = "4a3ca9315b744ce9f8e9374361493884"


def g8(con):
    cats = _linhas(con, f"""SELECT {CAT} cat, count(DISTINCT i.order_id) n FROM itens i JOIN produtos pr USING (product_id)
                            WHERE i.seller_id = ? GROUP BY 1 ORDER BY n DESC""", [VENDEDOR_Q8])
    _sem_empate(cats, casas=0)
    cat = cats[0][0]
    p = _linhas(con, f"""SELECT 100.0 * avg((motivo = 'qualidade_abaixo_do_esperado')::INT) FROM {AV} x
                         WHERE x.order_id IN (SELECT order_id FROM itens WHERE seller_id = ?)
                           AND x.order_id IN (SELECT i.order_id FROM itens i JOIN produtos pr USING (product_id)
                                              WHERE {CAT} = ?)""", [VENDEDOR_Q8, cat])[0][0]
    return {"categoria": cat, "pct_qualidade_abaixo": round(p, 1)}


COMENTARIO_Q9 = "O produto chegou todo quebrado e a loja não responde minhas mensagens"


def g9(con):
    sys.path.insert(0, str(RAIZ / "src"))
    from olist_cx_mcp.classificador import ClassificadorSklearn

    motivo = ClassificadorSklearn(RAIZ / "models" / "motivo" / "modelo.joblib").classificar([COMENTARIO_Q9])[0].motivo
    n = _linhas(con, f"SELECT count(*) FROM {AV} WHERE uf = 'RJ' AND nota = 1 AND motivo = ?", [motivo])[0][0]
    return {"motivo": motivo, "n_avaliacoes": n}


def g10(con):
    r = _linhas(con, f"""SELECT strftime(data_av, '%Y-%m') mes, 100.0 * avg((motivo = 'atraso_ou_nao_entrega')::INT) p
                         FROM {AV} WHERE data_av BETWEEN '2018-01-01' AND '2018-12-31' GROUP BY 1 ORDER BY p DESC""")
    _sem_empate(r)
    mes = r[0][0]
    ano, m = map(int, mes.split("-"))
    anterior = f"{ano}-{m - 1:02d}" if m > 1 else f"{ano - 1}-12"
    pct = _pct_atraso(con, "strftime(e.data_compra, '%Y-%m') = ?", [anterior])
    return {"mes": mes, "pct_reclamacao_atraso": round(r[0][1], 1), "pct_atrasados_compras_mes_anterior": pct}


# ---------------------------------------------------------------- perguntas
# chaves: "texto" = comparação exata (sem caixa/espaços); "numero" = tolerância absoluta

PERGUNTAS = [
    {"id": "q01", "gabarito": g1,
     "pergunta": "Entre os estados de clientes com pelo menos 1.000 avaliações classificadas, qual tem o maior "
                 "percentual de reclamações de atraso (motivo atraso_ou_nao_entrega)? Para esse estado, qual é o "
                 "percentual de pedidos entregues com atraso?",
     "chaves": {"estado": "texto", "pct_reclamacao_atraso": 0.15, "pct_pedidos_atrasados": 0.15}},
    {"id": "q02", "gabarito": g2,
     "pergunta": "Qual vendedor recebeu mais avaliações negativas (nota 1 ou 2) com comentário criadas em 2018? "
                 "Qual é a nota média de todas as avaliações desse vendedor, em todo o período?",
     "chaves": {"vendedor_id": "texto", "nota_media": 0.015}},
    {"id": "q03", "gabarito": g3,
     "pergunta": "Qual categoria de produto tem o maior número absoluto de avaliações classificadas como "
                 "defeito_ou_avaria? Qual o percentual de pedidos entregues com atraso nessa categoria?",
     "chaves": {"categoria": "texto", "pct_pedidos_atrasados": 0.15}},
    {"id": "q04", "gabarito": g4,
     "pergunta": "Entre as rotas (UF do vendedor -> UF do cliente) com pelo menos 500 pedidos entregues, qual tem a "
                 "maior taxa de atraso, e qual é essa taxa? Desconsiderando elogio, qual é o motivo mais comum nas "
                 "avaliações classificadas dos clientes do estado de destino dessa rota?",
     "chaves": {"rota": "texto", "pct_pedidos_atrasados": 0.15, "motivo_sem_elogio": "texto"}},
    {"id": "q05", "gabarito": g5,
     "pergunta": "Quantas avaliações foram classificadas como produto_errado_ou_incompleto em fevereiro de 2018 e "
                 "quantas em março de 2018 (pela data da avaliação)?",
     "chaves": {"fevereiro": 0, "marco": 0}},
    {"id": "q06", "gabarito": g6,
     "pergunta": "Considere os 3 vendedores com mais avaliações classificadas em toda a base. Qual deles tem a maior "
                 "taxa de pedidos entregues com atraso, e qual é essa taxa?",
     "chaves": {"vendedor_id": "texto", "pct_pedidos_atrasados": 0.15}},
    {"id": "q07", "gabarito": g7,
     "pergunta": "Para clientes de SP, em qual mês de compra entre janeiro e agosto de 2018 a taxa de pedidos "
                 "entregues com atraso foi mais alta, e qual foi essa taxa?",
     "chaves": {"mes": "texto", "pct_pedidos_atrasados": 0.15}},
    {"id": "q08", "gabarito": g8,
     "pergunta": f"Qual é a categoria em que o vendedor {VENDEDOR_Q8} tem mais pedidos? Entre as avaliações "
                 "classificadas dos pedidos desse vendedor nessa categoria, qual o percentual com motivo "
                 "qualidade_abaixo_do_esperado?",
     "chaves": {"categoria": "texto", "pct_qualidade_abaixo": 0.15}},
    {"id": "q09", "gabarito": g9,
     "pergunta": f"Um cliente escreveu: \"{COMENTARIO_Q9}\". Em qual motivo o classificador coloca esse comentário? "
                 "Quantas avaliações com nota 1 de clientes do RJ foram classificadas com esse mesmo motivo?",
     "chaves": {"motivo": "texto", "n_avaliacoes": 0}},
    {"id": "q10", "gabarito": g10,
     "pergunta": "Em qual mês de 2018 (pela data da avaliação) a participação de reclamações de atraso entre as "
                 "avaliações classificadas foi maior, e qual foi esse percentual? Qual foi a taxa de pedidos "
                 "entregues com atraso entre as compras feitas no mês anterior a esse?",
     "chaves": {"mes": "texto", "pct_reclamacao_atraso": 0.15, "pct_atrasados_compras_mes_anterior": 0.15}},
]


def calcular_gabarito(banco: Path = RAIZ / "data" / "olist.duckdb") -> dict:
    with duckdb.connect(str(banco), read_only=True) as con:
        return {p["id"]: p["gabarito"](con) for p in PERGUNTAS}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    gab = calcular_gabarito()
    for p in PERGUNTAS:
        print(f"{p['id']}: {gab[p['id']]}")
    (RAIZ / "eval" / "gabarito.json").write_text(json.dumps(gab, ensure_ascii=False, indent=2), encoding="utf-8")
