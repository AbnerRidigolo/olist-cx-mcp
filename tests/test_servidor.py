"""Contrato do servidor: ferramentas, anotações, esquemas e prompt."""
import pytest
from mcp import Client

pytestmark = pytest.mark.anyio

FERRAMENTAS = {"cx_search_reviews", "cx_reason_breakdown", "cx_seller_profile",
               "cx_delivery_delay_stats", "cx_classify_review"}


async def test_ferramentas_somente_leitura_com_esquemas(servidor):
    async with Client(servidor) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == FERRAMENTAS
    for t in tools.values():
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False
        assert t.output_schema, f"{t.name} sem outputSchema"
        for nome, prop in t.input_schema["properties"].items():
            assert prop.get("description"), f"{t.name}.{nome} sem descrição"
            assert prop.get("examples"), f"{t.name}.{nome} sem exemplo"


async def test_prompt_relatorio_semanal(servidor):
    async with Client(servidor) as c:
        assert [p.name for p in (await c.list_prompts()).prompts] == ["relatorio_semanal_cx"]
        r = await c.get_prompt("relatorio_semanal_cx", {"semana_inicio": "2018-05-07", "estado_cliente": "sp"})
    texto = r.messages[0].content.text
    assert "2018-05-07 a 2018-05-13" in texto
    assert "2018-04-30 a 2018-05-06" in texto
    assert "estado_cliente='SP'" in texto
    for f in FERRAMENTAS - {"cx_classify_review"}:
        assert f in texto


def test_banco_aberto_somente_leitura(servidor):
    import duckdb

    with pytest.raises(duckdb.Error):
        servidor.banco._linhas("CREATE TABLE x (a INT)")
