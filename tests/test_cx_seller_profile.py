import pytest

from .conftest import SA
from .util import erro, ok

pytestmark = pytest.mark.anyio
F = "cx_seller_profile"


async def test_perfil_completo(servidor):
    r = await ok(servidor, F, vendedor_id=SA)
    assert (r["cidade"], r["estado"]) == ("sao paulo", "SP")
    assert (r["pedidos"], r["itens_vendidos"], r["receita_itens"]) == (4, 4, 230.0)
    assert (r["primeira_venda"], r["ultima_venda"]) == ("2018-01-02", "2018-02-02")
    assert r["principais_categorias"] == [{"categoria": "cama_mesa_banho", "pedidos": 3},
                                          {"categoria": "beleza_saude", "pedidos": 1}]
    assert r["avaliacoes"] == 4 and r["classificadas"] == 3
    assert r["nota_media"] == {"vendedor": 2.75, "plataforma": 3.0}
    assert r["pct_nota_baixa"] == {"vendedor": 50.0, "plataforma": 42.9}
    assert r["entregues"] == 3
    assert r["pct_atrasados"] == {"vendedor": 33.3, "plataforma": 40.0}
    assert r["dias_atraso_medio_quando_atrasa"] == {"vendedor": 5.0, "plataforma": 7.5}
    assert {m["motivo"]: m["n"] for m in r["motivos"] if m["n"]} == {"atraso_ou_nao_entrega": 2, "elogio": 1}


async def test_reclamacoes_paginadas(servidor):
    r = await ok(servidor, F, vendedor_id=SA, limit=1)
    assert r["pagina_reclamacoes"] == {"total": 2, "limit": 1, "offset": 0, "proximo_offset": 1}
    assert r["reclamacoes_recentes"][0]["review_id"] == "r6"
    r = await ok(servidor, F, vendedor_id=SA, limit=1, offset=1)
    assert r["reclamacoes_recentes"][0]["review_id"] == "r2"


@pytest.mark.parametrize("args, trecho", [
    ({"vendedor_id": "123"}, "32 caracteres hexadecimais"),
    ({"vendedor_id": "c" * 32}, "não encontrado"),
    ({}, "campo obrigatório ausente"),
    ({"vendedor_id": SA, "limit": 0}, "deve ser >= 1"),
])
async def test_erros(servidor, args, trecho):
    assert trecho in await erro(servidor, F, **args)
