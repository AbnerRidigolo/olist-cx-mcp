import pytest

from .conftest import SA, SB
from .util import erro, ok

pytestmark = pytest.mark.anyio
F = "cx_reason_breakdown"


def pct(lista, motivo):
    return next(m["pct"] for m in lista if m["motivo"] == motivo)


async def test_distribuicao_geral(servidor):
    r = await ok(servidor, F)
    assert r["total_classificadas"] == 5
    assert r["modelo"] == "falso_palavras_chave"
    assert {m["motivo"]: m["n"] for m in r["distribuicao"] if m["n"]} == {
        "elogio": 2, "atraso_ou_nao_entrega": 2, "defeito_ou_avaria": 1}
    assert pct(r["distribuicao"], "defeito_ou_avaria") == 20.0
    assert len(r["distribuicao"]) == 7 and r["grupos"] == [] and r["pagina_grupos"] is None


async def test_por_estado_ordenado_por_motivo(servidor):
    r = await ok(servidor, F, agrupar_por="estado_cliente", ordenar_por="atraso_ou_nao_entrega")
    assert [(g["grupo"], g["total_classificadas"]) for g in r["grupos"]] == [("MG", 2), ("SP", 2), ("RJ", 1)]
    assert pct(r["grupos"][0]["motivos"], "atraso_ou_nao_entrega") == 50.0


async def test_por_vendedor_conta_pedido_multivendedor_para_ambos(servidor):
    r = await ok(servidor, F, agrupar_por="vendedor")
    assert {g["grupo"]: g["total_classificadas"] for g in r["grupos"]} == {SA: 3, SB: 3}


async def test_por_categoria_e_min_avaliacoes(servidor):
    r = await ok(servidor, F, agrupar_por="categoria")
    assert {g["grupo"]: g["total_classificadas"] for g in r["grupos"]} == {
        "beleza_saude": 3, "cama_mesa_banho": 2, "sem_categoria": 1}
    r = await ok(servidor, F, agrupar_por="categoria", min_avaliacoes=2)
    assert [g["grupo"] for g in r["grupos"]] == ["beleza_saude", "cama_mesa_banho"]
    assert r["grupos"][0]["motivo_principal"] == "atraso_ou_nao_entrega"


async def test_por_mes_ordem_cronologica_e_paginacao(servidor):
    r = await ok(servidor, F, agrupar_por="mes", ordenar_por="grupo", limit=1)
    assert r["grupos"][0]["grupo"] == "2018-01"
    assert r["pagina_grupos"] == {"total": 2, "limit": 1, "offset": 0, "proximo_offset": 1}


async def test_filtros(servidor):
    r = await ok(servidor, F, estado_vendedor="RJ", nota_max=2)
    assert r["total_classificadas"] == 2  # r2 (o2 tem SB) e r3


@pytest.mark.parametrize("args, trecho", [
    ({"agrupar_por": "cidade"}, "use um de:"),
    ({"ordenar_por": "nota"}, "use um de:"),
    ({"min_avaliacoes": 0}, "deve ser >= 1"),
    ({"estado_vendedor": "XX"}, "estado inválido"),
    ({"vendedor_id": "123"}, "32 caracteres hexadecimais"),
])
async def test_erros(servidor, args, trecho):
    assert trecho in await erro(servidor, F, **args)
