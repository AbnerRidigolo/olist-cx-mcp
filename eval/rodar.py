"""Avaliação: um modelo responde às 10 perguntas usando o servidor olist-cx-mcp como cliente MCP.

O servidor roda como subprocesso (stdio), como no Claude Desktop. O loop de ferramentas é manual: cada
chamada do modelo vira uma chamada MCP, e o resultado (resumo + JSON estruturado) volta ao modelo. A
resposta final é um JSON na última linha (`RESPOSTA: {...}`), comparado com o gabarito calculado por SQL
(eval/perguntas.py). Registra acerto, número de chamadas, tokens e custo.

Dois provedores, escolhidos pelo nome do modelo:
- OpenAI (gpt-*): Responses API, store=False com o raciocínio criptografado devolvido a cada turno;
- Anthropic (claude-*): Messages API com cache de prompt e fallback do servidor.

Custo: calculado do `usage` de cada resposta e da tabela de preços abaixo (US$/milhão de tokens).
Para se o custo acumulado passar de --orcamento-usd.

Uso (PowerShell):
    python eval\\rodar.py --modelo gpt-6.2-sol --esforco medium --repeticoes 3
    python eval\\rodar.py --modelo claude-opus-5-5 --perguntas q01,q05
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from mcp import Client, StdioServerParameters

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "eval"))
from perguntas import PERGUNTAS, calcular_gabarito  # noqa: E402

# US$ por milhão de tokens: entrada, saída, escrita de cache, leitura de cache (preços públicos, nov/2026)
PRECOS = {
    "gpt-6.2-sol": (2.00, 10.00, 2.00, 0.10),
    "gpt-6.2-astra": (8.00, 40.00, 8.00, 0.40),
    "gpt-6.1-sol": (2.00, 10.00, 2.00, 0.10),
    "gpt-6.1-terra": (1.00, 5.00, 1.00, 0.05),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
USO_VAZIO = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

SISTEMA = """Você é analista de experiência do cliente (CX) de um marketplace e responde perguntas usando \
apenas as ferramentas do servidor olist-cx-mcp (dados públicos da Olist, 2016 a 2018).

Regras:
- Use as ferramentas para obter cada número; não estime nem invente valores.
- Siga exatamente as definições das ferramentas (por exemplo, o que conta como atraso e como avaliação classificada).
- Quando uma ferramenta devolver erro, leia a mensagem, corrija os argumentos e tente de novo.
- Percentuais com 1 casa decimal, como as ferramentas devolvem; médias de nota com 2 casas.
- Termine a resposta com uma única linha no formato:
RESPOSTA: {"chave": valor, ...}
usando exatamente as chaves pedidas, com números como números JSON e textos entre aspas."""


def provedor(modelo: str) -> str:
    return "anthropic" if modelo.startswith("claude-") else "openai"


def _formato_chaves(chaves: dict) -> str:
    return "{" + ", ".join(f'"{k}": {"texto" if t == "texto" else "número"}' for k, t in chaves.items()) + "}"


def enunciado(pergunta: dict) -> str:
    return f"{pergunta['pergunta']}\n\nChaves da RESPOSTA: {_formato_chaves(pergunta['chaves'])}"


def extrair_resposta(texto: str) -> dict | None:
    achados = re.findall(r"RESPOSTA:\s*(\{.*\})", texto or "")
    if not achados:
        return None
    try:
        return json.loads(achados[-1])
    except json.JSONDecodeError:
        return None


def corrigir(resposta: dict | None, gabarito: dict, chaves: dict) -> dict:
    detalhes = {}
    for k, tipo in chaves.items():
        esperado, obtido = gabarito[k], (resposta or {}).get(k)
        if tipo == "texto":
            ok = isinstance(obtido, str) and obtido.strip().lower() == str(esperado).strip().lower()
        else:
            try:
                ok = abs(float(obtido) - float(esperado)) <= tipo + 1e-9
            except (TypeError, ValueError):
                ok = False
        detalhes[k] = {"esperado": esperado, "obtido": obtido, "ok": ok}
    return {"correta": all(d["ok"] for d in detalhes.values()), "chaves": detalhes}


def custo_usd(modelo: str, uso: dict) -> float:
    pin, pout, pcw, pcr = PRECOS[modelo]
    return (uso["input_tokens"] * pin + uso["output_tokens"] * pout
            + uso["cache_creation_input_tokens"] * pcw + uso["cache_read_input_tokens"] * pcr) / 1e6


async def executar_ferramenta(mcp: Client, nome: str, args: dict | None, chamadas: list) -> tuple[str, bool]:
    """Chama a ferramenta MCP e devolve (texto para o modelo, houve_erro)."""
    if args is None:
        texto, erro = "argumentos não são um JSON válido; envie um objeto JSON", True
    else:
        r = await mcp.call_tool(nome, args)
        texto = "\n".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        if r.structured_content is not None:
            texto += "\n\n" + json.dumps(r.structured_content, ensure_ascii=False)
        erro = bool(r.is_error)
    chamadas.append({"ferramenta": nome, "args": args, "erro": erro})
    return texto, erro


def _resultado(texto_final, stop, turnos, chamadas, fallback, uso, modelo, t0) -> dict:
    return {"resposta": extrair_resposta(texto_final), "texto_final": (texto_final or "")[-2000:],
            "stop_reason": stop, "turnos": turnos, "n_chamadas": len(chamadas),
            "erros_ferramenta": sum(c["erro"] for c in chamadas), "chamadas": chamadas, "fallback": fallback,
            "uso": uso, "custo_usd": round(custo_usd(modelo, uso), 5), "segundos": round(time.perf_counter() - t0, 1)}


# ---------------------------------------------------------------- Anthropic (Messages API)

async def responder_anthropic(client, mcp: Client, ferramentas: list[dict], pergunta: dict, args) -> dict:
    mensagens = [{"role": "user", "content": enunciado(pergunta)}]
    uso, chamadas, turnos, fallback, stop, texto, t0 = dict(USO_VAZIO), [], 0, False, None, "", time.perf_counter()
    while turnos < args.max_turnos:
        turnos += 1
        resp = await client.beta.messages.create(
            model=args.modelo, max_tokens=16000, system=SISTEMA, tools=ferramentas, messages=mensagens,
            output_config={"effort": args.esforco}, cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA], fallbacks="default",
        )
        for k in uso:
            uso[k] += getattr(resp.usage, k) or 0
        fallback |= any(getattr(it, "type", "") == "fallback_message" for it in (resp.usage.iterations or []))
        mensagens.append({"role": "assistant", "content": resp.content})
        stop = resp.stop_reason
        if stop != "tool_use":
            texto = "\n".join(b.text for b in resp.content if b.type == "text")
            break
        resultados = []
        for bloco in resp.content:
            if bloco.type == "tool_use":
                saida, erro = await executar_ferramenta(mcp, bloco.name, bloco.input, chamadas)
                resultados.append({"type": "tool_result", "tool_use_id": bloco.id, "content": saida, "is_error": erro})
        mensagens.append({"role": "user", "content": resultados})
    return _resultado(texto, stop, turnos, chamadas, fallback, uso, args.modelo, t0)


# ---------------------------------------------------------------- OpenAI (Responses API)

def ferramentas_openai(ferramentas: list[dict]) -> list[dict]:
    # strict=False: os esquemas têm campos opcionais com padrão, que o modo estrito não aceita
    return [{"type": "function", "name": f["name"], "description": f["description"],
             "parameters": f["input_schema"], "strict": False} for f in ferramentas]


async def responder_openai(client, mcp: Client, ferramentas: list[dict], pergunta: dict, args) -> dict:
    entrada: list = [{"role": "user", "content": enunciado(pergunta)}]
    tools = ferramentas_openai(ferramentas)
    uso, chamadas, turnos, stop, texto, t0 = dict(USO_VAZIO), [], 0, None, "", time.perf_counter()
    while turnos < args.max_turnos:
        turnos += 1
        resp = await client.responses.create(
            model=args.modelo, instructions=SISTEMA, input=entrada, tools=tools,
            reasoning={"effort": args.esforco}, max_output_tokens=16000,
            store=False, include=["reasoning.encrypted_content"], prompt_cache_key="olist-cx-mcp-eval",
        )
        u = resp.usage
        cache = (u.input_tokens_details.cached_tokens or 0) if u.input_tokens_details else 0
        uso["input_tokens"] += u.input_tokens - cache  # cached_tokens já está dentro de input_tokens
        uso["cache_read_input_tokens"] += cache
        uso["output_tokens"] += u.output_tokens  # inclui os tokens de raciocínio
        entrada += resp.output  # devolve raciocínio e chamadas, como o guia de function calling pede
        chamadas_turno = [it for it in resp.output if it.type == "function_call"]
        stop = resp.status
        if not chamadas_turno:
            texto = resp.output_text
            break
        for it in chamadas_turno:
            try:
                a = json.loads(it.arguments or "{}")
            except json.JSONDecodeError:
                a = None
            saida, erro = await executar_ferramenta(mcp, it.name, a, chamadas)
            entrada.append({"type": "function_call_output", "call_id": it.call_id,
                            "output": ("ERRO DA FERRAMENTA: " if erro else "") + saida})
    return _resultado(texto, stop, turnos, chamadas, False, uso, args.modelo, t0)


async def responder(client, mcp, ferramentas, pergunta, args) -> dict:
    fn = responder_anthropic if provedor(args.modelo) == "anthropic" else responder_openai
    return await fn(client, mcp, ferramentas, pergunta, args)


def criar_cliente(modelo: str):
    if provedor(modelo) == "anthropic":
        import anthropic

        if os.getenv("ANTHROPIC_API_KEY") == "":
            del os.environ["ANTHROPIC_API_KEY"]  # vazio no .env não deve esconder um `ant auth login`
        return anthropic.AsyncAnthropic(), anthropic.__version__
    import openai

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("defina OPENAI_API_KEY no .env (veja .env.example)")
    return openai.AsyncOpenAI(), openai.__version__


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modelo", default="gpt-6.2-sol", choices=sorted(PRECOS))
    ap.add_argument("--esforco", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--repeticoes", type=int, default=1)
    ap.add_argument("--perguntas", default="", help="ids separados por vírgula (padrão: todas)")
    ap.add_argument("--max-turnos", type=int, default=15)
    ap.add_argument("--orcamento-usd", type=float, default=10.0, help="para se o custo acumulado passar disso")
    args = ap.parse_args()

    load_dotenv(RAIZ / ".env")
    client, versao_sdk = criar_cliente(args.modelo)
    ids = set(filter(None, args.perguntas.split(",")))
    selecionadas = [p for p in PERGUNTAS if not ids or p["id"] in ids]
    gabarito = calcular_gabarito()

    params = StdioServerParameters(command=sys.executable, args=["-m", "olist_cx_mcp"],
                                   env={**os.environ, "PYTHONUTF8": "1"})
    registros, total, estourou = [], 0.0, False
    async with Client(params) as mcp:
        ferramentas = sorted(({"name": t.name, "description": t.description, "input_schema": t.input_schema}
                              for t in (await mcp.list_tools()).tools), key=lambda t: t["name"])
        for rep in range(1, args.repeticoes + 1):
            for p in selecionadas:
                r = await responder(client, mcp, ferramentas, p, args)
                r |= {"id": p["id"], "repeticao": rep} | corrigir(r["resposta"], gabarito[p["id"]], p["chaves"])
                registros.append(r)
                total += r["custo_usd"]
                print(f"[{rep}] {p['id']} {'ACERTO' if r['correta'] else 'ERRO  '} chamadas={r['n_chamadas']:2d} "
                      f"turnos={r['turnos']:2d} US$ {r['custo_usd']:.4f} (acumulado {total:.4f})", flush=True)
                if total > args.orcamento_usd:
                    print(f"orçamento de US$ {args.orcamento_usd} ultrapassado; parando", flush=True)
                    estourou = True
                    break
            if estourou:
                break

    n = len(registros)
    por_pergunta = {}
    for r in registros:
        por_pergunta.setdefault(r["id"], []).append(r["correta"])
    resumo = {
        "modelo": args.modelo, "provedor": provedor(args.modelo), "esforco": args.esforco,
        "repeticoes": args.repeticoes, "data_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sdk": versao_sdk, "respondidas": n, "acertos": sum(r["correta"] for r in registros),
        "taxa_acerto": round(sum(r["correta"] for r in registros) / n, 3) if n else None,
        "acertos_por_pergunta": {k: f"{sum(v)}/{len(v)}" for k, v in por_pergunta.items()},
        "chamadas_media": round(sum(r["n_chamadas"] for r in registros) / n, 2) if n else None,
        "erros_ferramenta": sum(r["erros_ferramenta"] for r in registros),
        "custo_total_usd": round(total, 4), "custo_medio_usd": round(total / n, 4) if n else None,
        "com_fallback": sum(r["fallback"] for r in registros), "orcamento_estourado": estourou,
        "tokens": {k: sum(r["uso"][k] for r in registros) for k in USO_VAZIO},
    }
    saida = RAIZ / "eval" / "resultados" / f"{args.modelo}_{args.esforco}_{datetime.now():%Y%m%d_%H%M%S}.json"
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps({"resumo": resumo, "registros": registros}, ensure_ascii=False, indent=2,
                                default=str), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    print(f"detalhes em {saida.relative_to(RAIZ)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except Exception as e:  # erros de autenticação dos dois SDKs, sem vazar detalhes da credencial
        if type(e).__name__ == "AuthenticationError":
            sys.exit("credencial recusada pelo provedor: confira a chave no .env")
        raise
