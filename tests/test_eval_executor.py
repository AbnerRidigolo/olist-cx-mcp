"""Executor da avaliação (eval/rodar.py) sem chamar a API: correção, custo e loop com um modelo falso."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import rodar  # noqa: E402

pytestmark = pytest.mark.anyio


def test_extrair_resposta_pega_a_ultima_linha():
    texto = 'Primeiro achei RESPOSTA: {"x": 1}\nmas corrigi.\nRESPOSTA: {"estado": "RJ", "pct": 21.7}'
    assert rodar.extrair_resposta(texto) == {"estado": "RJ", "pct": 21.7}
    assert rodar.extrair_resposta("sem resposta") is None
    assert rodar.extrair_resposta("RESPOSTA: {quebrado}") is None


def test_corrigir_texto_e_tolerancia():
    chaves = {"estado": "texto", "pct": 0.15, "n": 0}
    gab = {"estado": "RJ", "pct": 21.7, "n": 91}
    assert rodar.corrigir({"estado": " rj ", "pct": 21.6, "n": 91}, gab, chaves)["correta"]
    r = rodar.corrigir({"estado": "RJ", "pct": 21.4, "n": 90}, gab, chaves)
    assert not r["correta"] and not r["chaves"]["pct"]["ok"] and not r["chaves"]["n"]["ok"]
    assert not rodar.corrigir(None, gab, chaves)["correta"]
    # acento e caixa não contam (o dataset grava "rolandia"); outra cidade continua errada
    assert rodar.corrigir({"cidade": "Rolândia"}, {"cidade": "rolandia"}, {"cidade": "texto"})["correta"]
    assert not rodar.corrigir({"cidade": "Londrina"}, {"cidade": "rolandia"}, {"cidade": "texto"})["correta"]


def test_custo():
    uso = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_creation_input_tokens": 0,
           "cache_read_input_tokens": 1_000_000}
    assert rodar.custo_usd("claude-opus-5-5", uso) == pytest.approx(4.0 + 2.0 + 0.2)


class ModeloFalso:
    """Pede cx_reason_breakdown, depois responde. Registra o que recebeu."""

    def __init__(self):
        self.pedidos = []
        self.beta = NS(messages=NS(create=self._create))

    async def _create(self, **kw):
        self.pedidos.append(kw)
        uso = NS(input_tokens=100, output_tokens=50, cache_creation_input_tokens=10, cache_read_input_tokens=5,
                 iterations=None)
        if len(self.pedidos) == 1:
            return NS(stop_reason="tool_use", usage=uso, content=[
                NS(type="tool_use", id="t1", name="cx_reason_breakdown", input={}),
                NS(type="tool_use", id="t2", name="cx_search_reviews", input={"estado_cliente": "Rio"})])
        return NS(stop_reason="end_turn", usage=uso,
                  content=[NS(type="text", text='Total 5.\nRESPOSTA: {"total": 5}')])


async def test_loop_com_servidor_real(servidor):
    modelo = ModeloFalso()
    args = NS(modelo="claude-opus-5-5", esforco="medium", max_turnos=5)
    pergunta = {"pergunta": "Quantas avaliações classificadas?", "chaves": {"total": 0}}
    async with Client(servidor) as mcp:
        ferramentas = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                       for t in (await mcp.list_tools()).tools]
        r = await rodar.responder(modelo, mcp, ferramentas, pergunta, args)

    assert r["resposta"] == {"total": 5} and r["stop_reason"] == "end_turn"
    assert (r["turnos"], r["n_chamadas"], r["erros_ferramenta"]) == (2, 2, 1)
    assert r["uso"]["input_tokens"] == 200 and r["custo_usd"] > 0
    # os dois tool_results voltam numa única mensagem, com o erro marcado e a mensagem acionável
    # mensagens: [0] pergunta, [1] assistente com tool_use, [2] resultados, [3] resposta final
    resultados = modelo.pedidos[1]["messages"][2]["content"]
    assert [x["tool_use_id"] for x in resultados] == ["t1", "t2"]
    assert resultados[0]["is_error"] is False and '"total_classificadas": 5' in resultados[0]["content"]
    assert resultados[1]["is_error"] is True and "use a sigla" in resultados[1]["content"]
    # parâmetros da requisição: esforço explícito, cache e fallback
    kw = modelo.pedidos[0]
    assert kw["output_config"] == {"effort": "medium"} and kw["cache_control"] == {"type": "ephemeral"}
    assert kw["fallbacks"] == "default" and kw["betas"] == [rodar.FALLBACK_BETA]


class OpenAIFalso:
    """Responses API falsa: pede duas ferramentas (uma com JSON quebrado), depois responde."""

    def __init__(self):
        self.pedidos = []
        self.responses = NS(create=self._create)

    async def _create(self, **kw):
        self.pedidos.append({**kw, "input": list(kw["input"])})
        uso = NS(input_tokens=100, input_tokens_details=NS(cached_tokens=40), output_tokens=50)
        if len(self.pedidos) == 1:
            return NS(status="completed", usage=uso, output_text="", output=[
                NS(type="reasoning", id="rs1"),
                NS(type="function_call", name="cx_reason_breakdown", arguments="{}", call_id="c1"),
                NS(type="function_call", name="cx_search_reviews", arguments="{quebrado", call_id="c2")])
        return NS(status="completed", usage=uso, output=[NS(type="message")],
                  output_text='Total 5.\nRESPOSTA: {"total": 5}')


async def test_loop_openai_com_servidor_real(servidor):
    modelo = OpenAIFalso()
    args = NS(modelo="gpt-6.2-sol", esforco="medium", max_turnos=5)
    pergunta = {"pergunta": "Quantas avaliações classificadas?", "chaves": {"total": 0}}
    async with Client(servidor) as mcp:
        ferramentas = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                       for t in (await mcp.list_tools()).tools]
        r = await rodar.responder(modelo, mcp, ferramentas, pergunta, args)

    assert r["resposta"] == {"total": 5} and (r["turnos"], r["n_chamadas"], r["erros_ferramenta"]) == (2, 2, 1)
    # cached_tokens sai da entrada cheia e entra como leitura de cache
    assert r["uso"]["input_tokens"] == 120 and r["uso"]["cache_read_input_tokens"] == 80
    entrada = modelo.pedidos[1]["input"]
    assert [getattr(x, "type", None) for x in entrada[1:4]] == ["reasoning", "function_call", "function_call"]
    saidas = [x for x in entrada if isinstance(x, dict) and x.get("type") == "function_call_output"]
    assert [s["call_id"] for s in saidas] == ["c1", "c2"]
    assert '"total_classificadas": 5' in saidas[0]["output"]
    assert saidas[1]["output"].startswith("ERRO DA FERRAMENTA: argumentos não são um JSON válido")
    kw = modelo.pedidos[0]
    assert kw["store"] is False and kw["include"] == ["reasoning.encrypted_content"]
    assert kw["reasoning"] == {"effort": "medium"}
    assert kw["tools"][0]["type"] == "function" and kw["tools"][0]["strict"] is False
