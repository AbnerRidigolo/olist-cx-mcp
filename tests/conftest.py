"""Banco sintético com números conhecidos (calculados à mão; ver tests/README.md).

Vendedores: SA (SP) e SB (RJ). Pedidos o1..o6; o2 tem dois vendedores e duas categorias; o6 não foi
entregue. A avaliação r1 aparece em dois pedidos (review_id repetido, como na Olist).
O classificador falso decide o motivo por palavra-chave, para os testes não dependerem do modelo.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from olist_cx_mcp.classificador import CLASSES, Previsao
from olist_cx_mcp.pipeline import TABELAS, construir_banco

SA, SB = "a" * 32, "b" * 32
RAIZ = Path(__file__).resolve().parents[1]

LINHAS = {
    "vendedores": [(SA, "01000", "sao paulo", "SP"), (SB, "20000", "rio de janeiro", "RJ")],
    "clientes": [(f"c{i}", f"u{i}", "00000", cid, uf) for i, cid, uf in [
        (1, "sao paulo", "SP"), (2, "campinas", "SP"), (3, "rio de janeiro", "RJ"),
        (4, "niteroi", "RJ"), (5, "belo horizonte", "MG"), (6, "uberlandia", "MG")]],
    "produtos": [("p1", "cama_mesa_banho", 10, 100, 1, 500, 10, 10, 10),
                 ("p2", "beleza_saude", 10, 100, 1, 500, 10, 10, 10),
                 ("p3", "", 10, 100, 1, 500, 10, 10, 10)],
    "categorias": [("cama_mesa_banho", "bed_bath_table"), ("beleza_saude", "health_beauty")],
    # order_id, customer_id, status, compra, aprovado, transportadora, entrega, estimada
    "pedidos": [
        ("o1", "c1", "delivered", "2018-01-02 10:00:00", "", "", "2018-01-10 12:00:00", "2018-01-15 00:00:00"),
        ("o2", "c2", "delivered", "2018-01-03 10:00:00", "", "", "2018-01-20 12:00:00", "2018-01-15 00:00:00"),
        ("o3", "c3", "delivered", "2018-01-05 10:00:00", "", "", "2018-01-30 12:00:00", "2018-01-20 00:00:00"),
        ("o4", "c4", "delivered", "2018-01-06 10:00:00", "", "", "2018-01-15 18:00:00", "2018-01-15 00:00:00"),
        ("o5", "c5", "delivered", "2018-02-01 10:00:00", "", "", "2018-02-10 12:00:00", "2018-02-20 00:00:00"),
        ("o6", "c6", "shipped", "2018-02-02 10:00:00", "", "", "", "2018-02-25 00:00:00"),
    ],
    "itens": [
        ("o1", 1, "p1", SA, "2018-01-04 00:00:00", "100.00", "10.00"),
        ("o2", 1, "p1", SA, "2018-01-05 00:00:00", "50.00", "10.00"),
        ("o2", 2, "p2", SB, "2018-01-05 00:00:00", "30.00", "10.00"),
        ("o3", 1, "p2", SB, "2018-01-07 00:00:00", "80.00", "10.00"),
        ("o4", 1, "p1", SA, "2018-01-08 00:00:00", "20.00", "10.00"),
        ("o5", 1, "p3", SB, "2018-02-03 00:00:00", "40.00", "10.00"),
        ("o6", 1, "p2", SA, "2018-02-04 00:00:00", "60.00", "10.00"),
    ],
    # review_id, order_id, nota, título, mensagem, criação, resposta
    "avaliacoes": [
        ("r1", "o1", 5, "", "Produto ótimo, recomendo", "2018-01-11 00:00:00", "2018-01-12 00:00:00"),
        ("r2", "o2", 1, "", "Não recebi no prazo, atrasou", "2018-01-21 00:00:00", "2018-01-22 00:00:00"),
        ("r3", "o3", 2, "Ruim", "Veio com defeito", "2018-01-31 00:00:00", "2018-02-01 00:00:00"),
        ("r4", "o4", 4, "", "", "2018-01-16 00:00:00", "2018-01-17 00:00:00"),
        ("r5", "o5", 3, "Ok", "", "2018-02-11 00:00:00", "2018-02-12 00:00:00"),
        ("r6", "o6", 1, "", "Ainda não recebi, atrasou muito", "2018-02-26 00:00:00", "2018-02-27 00:00:00"),
        ("r1", "o5", 5, "", "Gostei, recomendo", "2018-02-12 00:00:00", "2018-02-13 00:00:00"),
    ],
}


class ClassificadorFalso:
    nome = "falso_palavras_chave"
    REGRAS = [("defeito", "defeito_ou_avaria"), ("receb", "atraso_ou_nao_entrega"),
              ("atras", "atraso_ou_nao_entrega"), ("recomendo", "elogio")]

    def classificar(self, textos):
        saida = []
        for t in textos:
            motivo = next((m for chave, m in self.REGRAS if chave in t.lower()), "outro")
            conf = 0.9 if motivo != "outro" else 0.4
            resto = (1 - conf) / (len(CLASSES) - 1)
            saida.append(Previsao(motivo, conf, {c: conf if c == motivo else resto for c in CLASSES}))
        return saida


def escrever_csvs(pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    for tabela, (arquivo, colunas) in TABELAS.items():
        with open(pasta / arquivo, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(colunas.keys())
            w.writerows(LINHAS[tabela])


@pytest.fixture(scope="session")
def banco_sintetico(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("olist")
    escrever_csvs(base / "raw")
    banco = base / "olist.duckdb"
    construir_banco(base / "raw", banco, ClassificadorFalso(), base / "motivos.parquet")
    return banco


@pytest.fixture(scope="session")
def servidor(banco_sintetico):
    from olist_cx_mcp.servidor import criar_servidor

    return criar_servidor(banco_sintetico, classificador=ClassificadorFalso())


@pytest.fixture
def anyio_backend():
    return "asyncio"
