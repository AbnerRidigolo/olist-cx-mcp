import pytest

from olist_cx_mcp.classificador import CLASSES

from .util import erro, ok

pytestmark = pytest.mark.anyio
F = "cx_classify_review"


async def test_classificador_injetado(servidor):
    r = await ok(servidor, F, texto="Veio com defeito na tampa")
    assert r["motivo"] == "defeito_ou_avaria" and r["confianca"] == 0.9 and not r["baixa_confianca"]
    assert r["probabilidades"][0]["motivo"] == "defeito_ou_avaria"
    assert len(r["probabilidades"]) == 7


async def test_baixa_confianca(servidor):
    r = await ok(servidor, F, texto="sem palavra chave")
    assert r["motivo"] == "outro" and r["baixa_confianca"] is True


async def test_modelo_real_versionado(banco_sintetico):
    """Usa o modelo.joblib versionado no repositório (o mesmo que classificou o dataset)."""
    from olist_cx_mcp.servidor import criar_servidor

    srv = criar_servidor(banco_sintetico)
    r = await ok(srv, F, texto="Comprei há 20 dias e até agora não recebi o produto")
    assert r["motivo"] == "atraso_ou_nao_entrega" and r["modelo"] == "baseline_tfidf_lr"
    assert abs(sum(p["probabilidade"] for p in r["probabilidades"]) - 1) < 0.01
    assert {p["motivo"] for p in r["probabilidades"]} == set(CLASSES)


@pytest.mark.parametrize("args, trecho", [
    ({"texto": "ab"}, "mínimo 3 caracteres"),
    ({"texto": "x" * 2001}, "máximo 2000 caracteres"),
    ({"texto": "     "}, "está vazio"),
    ({}, "campo obrigatório ausente"),
])
async def test_erros(servidor, args, trecho):
    assert trecho in await erro(servidor, F, **args)
