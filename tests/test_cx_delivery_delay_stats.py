import pytest

from .conftest import SA
from .util import chamar, erro, ok

pytestmark = pytest.mark.anyio
F = "cx_delivery_delay_stats"


async def test_geral(servidor):
    r = await ok(servidor, F)
    g = r["geral"]
    # o2 atrasou 5 dias, o3 10; o4 entregou no dia estimado (não é atraso)
    assert (g["entregues"], g["atrasados"], g["pct_atrasados"]) == (5, 2, 40.0)
    assert g["dias_atraso_medio_quando_atrasa"] == 7.5
    assert g["dias_ate_entrega_medio"] == 13.6
    assert (g["nota_media_atrasados"], g["nota_media_no_prazo"]) == (1.5, 4.33)
    assert g["pct_reclamacao_atraso"] == 40.0
    assert r["pedidos_nao_entregues"] == 1


async def test_por_estado_desempate_alfabetico(servidor):
    r = await ok(servidor, F, agrupar_por="estado_cliente")
    assert [(x["grupo"], x["pct_atrasados"]) for x in r["grupos"]] == [("RJ", 50.0), ("SP", 50.0), ("MG", 0.0)]


async def test_rota_e_min_pedidos(servidor):
    r = await ok(servidor, F, agrupar_por="rota", ordenar_por="grupo")
    assert [x["grupo"] for x in r["grupos"]] == ["RJ->MG", "RJ->RJ", "RJ->SP", "SP->RJ", "SP->SP"]
    r = await ok(servidor, F, agrupar_por="rota", min_pedidos=2)
    assert [x["grupo"] for x in r["grupos"]] == ["SP->SP"]


async def test_filtros_por_vendedor_e_periodo_de_compra(servidor):
    r = await ok(servidor, F, vendedor_id=SA, data_fim="2018-01-31")
    assert (r["geral"]["entregues"], r["geral"]["atrasados"]) == (3, 1)


async def test_recorte_vazio(servidor):
    r = await chamar(servidor, F, data_inicio="2030-01-01")
    assert not r.is_error
    assert r.structured_content["geral"]["entregues"] == 0
    assert "fora da cobertura" in r.content[0].text


@pytest.mark.parametrize("args, trecho", [
    ({"agrupar_por": "nota"}, "use um de:"),
    ({"limit": 101}, "deve ser <= 100"),
    ({"estado_cliente": "sp1"}, "estado inválido"),
    ({"categoria": "eletronicos"}, "categoria desconhecida"),
])
async def test_erros(servidor, args, trecho):
    assert trecho in await erro(servidor, F, **args)
