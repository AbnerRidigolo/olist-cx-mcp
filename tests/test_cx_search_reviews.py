import pytest

from .conftest import SA, SB
from .util import chamar, erro, ok

pytestmark = pytest.mark.anyio
F = "cx_search_reviews"


async def test_padrao_so_com_comentario_mais_recentes_primeiro(servidor):
    r = await ok(servidor, F)
    assert r["pagina"] == {"total": 5, "limit": 10, "offset": 0, "proximo_offset": None}
    assert [a["review_id"] for a in r["avaliacoes"]][:2] == ["r6", "r1"]  # 2018-02-26, 2018-02-12


async def test_inclui_sem_comentario(servidor):
    r = await ok(servidor, F, somente_com_comentario=False)
    assert r["pagina"]["total"] == 7


async def test_paginacao(servidor):
    p1 = await ok(servidor, F, limit=2)
    assert p1["pagina"]["proximo_offset"] == 2 and len(p1["avaliacoes"]) == 2
    p3 = await ok(servidor, F, limit=2, offset=4)
    assert p3["pagina"]["proximo_offset"] is None and len(p3["avaliacoes"]) == 1


async def test_texto_ignora_acentos_e_caixa(servidor):
    r = await ok(servidor, F, texto="NAO RECEBI")
    assert {a["review_id"] for a in r["avaliacoes"]} == {"r2", "r6"}


async def test_filtros_combinados(servidor):
    r = await ok(servidor, F, motivo="atraso_ou_nao_entrega", estado_cliente="mg")
    assert [a["review_id"] for a in r["avaliacoes"]] == ["r6"]
    r = await ok(servidor, F, vendedor_id=SB.upper(), nota_max=2)  # id em maiúsculas é normalizado
    assert {a["review_id"] for a in r["avaliacoes"]} == {"r2", "r3"}
    r = await ok(servidor, F, categoria="health_beauty", data_inicio="2018-01-01", data_fim="2018-01-31")
    assert {a["review_id"] for a in r["avaliacoes"]} == {"r2", "r3"}


async def test_pedido_com_dois_vendedores_lista_ambos(servidor):
    r = await ok(servidor, F, texto="no prazo")
    a = r["avaliacoes"][0]
    assert a["vendedores"] == [SA, SB] and a["categorias"] == ["beleza_saude", "cama_mesa_banho"]


async def test_texto_malicioso_e_so_parametro(servidor):
    r = await ok(servidor, F, texto="'; DROP TABLE cx_avaliacoes; --")
    assert r["pagina"]["total"] == 0
    assert (await ok(servidor, F))["pagina"]["total"] == 5


async def test_periodo_fora_da_cobertura_avisa(servidor):
    r = await chamar(servidor, F, data_inicio="2030-01-01")
    assert "fora da cobertura" in r.content[0].text


@pytest.mark.parametrize("args, trecho", [
    ({"estado_cliente": "São Paulo"}, "use a sigla de 2 letras, como SP"),
    ({"limit": 500}, "limit: deve ser <= 50"),
    ({"data_inicio": "01/03/2018"}, "formato ISO AAAA-MM-DD"),
    ({"motivo": "frete"}, "use um de:"),
    ({"nota_min": 4, "nota_max": 2}, "nota_min (4) é maior que nota_max (2)"),
    ({"data_inicio": "2018-05-01", "data_fim": "2018-01-01"}, "inverta as datas"),
    ({"categoria": "cama_mesa"}, "parecidas: cama_mesa_banho"),
])
async def test_erros_dizem_o_que_corrigir(servidor, args, trecho):
    msg = await erro(servidor, F, **args)
    assert trecho in msg
    assert msg.startswith(f"{F}: ")
    assert "errors.pydantic.dev" not in msg
