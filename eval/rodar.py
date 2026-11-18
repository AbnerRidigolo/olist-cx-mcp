"""Avaliação: um modelo Claude responde às 10 perguntas usando o servidor olist-cx-mcp como cliente MCP.

O servidor roda como subprocesso (stdio), como no Claude Desktop. O loop de ferramentas é manual
(Messages API): cada chamada do modelo vira uma chamada MCP, e o resultado (resumo + JSON estruturado)
volta como tool_result. A resposta final é um JSON na última linha (`RESPOSTA: {...}`), comparado com o
gabarito calculado por SQL (eval/perguntas.py). Registra acerto, número de chamadas, tokens e custo.

Custo: calculado a partir do `usage` de cada resposta e da tabela de preços abaixo (US$/milhão de tokens).
Para com erro se o custo acumulado passar de --orcamento-usd.

Uso (PowerShell):
    python eval\\rodar.py --modelo claude-opus-5-5 --esforco medium --repeticoes 1
    python eval\\rodar.py --perguntas q01,q05      # só algumas
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

import anthropic
from dotenv import load_dotenv
from mcp import Client, StdioServerParameters

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "eval"))
from perguntas import PERGUNTAS, calcular_gabarito  # noqa: E402

# US$ por milhão de tokens: entrada, saída, escrita de cache (5 min), leitura de cache
PRECOS = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

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


def _formato_chaves(chaves: dict) -> str:
    partes = []
    for k, tipo in chaves.items():
        partes.append(f'"{k}": {"texto" if tipo == "texto" else "número"}')
    return "{" + ", ".join(partes) + "}"


def extrair_resposta(texto: str) -> dict | None:
    achados = re.findall(r"RESPOSTA:\s*(\{.*\})", texto)
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


def _conteudo_resultado(r) -> str:
    texto = "\n".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    if r.structured_content is not None:
        texto += "\n\n" + json.dumps(r.structured_content, ensure_ascii=False)
    return texto


async def responder(client: anthropic.AsyncAnthropic, mcp: Client, ferramentas: list[dict], pergunta: dict,
                    args) -> dict:
    mensagens = [{"role": "user", "content": f"{pergunta['pergunta']}\n\nChaves da RESPOSTA: "
                                             f"{_formato_chaves(pergunta['chaves'])}"}]
    uso = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    chamadas, erros_ferramenta, turnos, fallback = [], 0, 0, False
    stop, texto_final, t0 = None, "", time.perf_counter()

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
            texto_final = "\n".join(b.text for b in resp.content if b.type == "text")
            break
        resultados = []
        for bloco in resp.content:
            if bloco.type != "tool_use":
                continue
            r = await mcp.call_tool(bloco.name, bloco.input)
            chamadas.append({"ferramenta": bloco.name, "args": bloco.input, "erro": bool(r.is_error)})
            erros_ferramenta += bool(r.is_error)
            resultados.append({"type": "tool_result", "tool_use_id": bloco.id,
                               "content": _conteudo_resultado(r), "is_error": bool(r.is_error)})
        mensagens.append({"role": "user", "content": resultados})

    resposta = extrair_resposta(texto_final)
    return {"resposta": resposta, "texto_final": texto_final[-2000:], "stop_reason": stop, "turnos": turnos,
            "n_chamadas": len(chamadas), "erros_ferramenta": erros_ferramenta, "chamadas": chamadas,
            "fallback": fallback, "uso": uso, "custo_usd": round(custo_usd(args.modelo, uso), 5),
            "segundos": round(time.perf_counter() - t0, 1)}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modelo", default="claude-opus-5-5", choices=sorted(PRECOS))
    ap.add_argument("--esforco", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--repeticoes", type=int, default=1)
    ap.add_argument("--perguntas", default="", help="ids separados por vírgula (padrão: todas)")
    ap.add_argument("--max-turnos", type=int, default=15)
    ap.add_argument("--orcamento-usd", type=float, default=10.0, help="para se o custo acumulado passar disso")
    args = ap.parse_args()

    load_dotenv(RAIZ / ".env")
    if os.getenv("ANTHROPIC_API_KEY") == "":
        del os.environ["ANTHROPIC_API_KEY"]  # vazio no .env não deve esconder um login do `ant auth login`
    ids = set(filter(None, args.perguntas.split(",")))
    selecionadas = [p for p in PERGUNTAS if not ids or p["id"] in ids]
    gabarito = calcular_gabarito()

    params = StdioServerParameters(command=sys.executable, args=["-m", "olist_cx_mcp"],
                                   env={**os.environ, "PYTHONUTF8": "1"})
    client = anthropic.AsyncAnthropic()
    registros, total = [], 0.0
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
                    break
            else:
                continue
            break

    n = len(registros)
    resumo = {
        "modelo": args.modelo, "esforco": args.esforco, "repeticoes": args.repeticoes,
        "data_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sdk_anthropic": anthropic.__version__, "respondidas": n,
        "acertos": sum(r["correta"] for r in registros),
        "taxa_acerto": round(sum(r["correta"] for r in registros) / n, 3) if n else None,
        "chamadas_media": round(sum(r["n_chamadas"] for r in registros) / n, 2) if n else None,
        "erros_ferramenta": sum(r["erros_ferramenta"] for r in registros),
        "custo_total_usd": round(total, 4), "custo_medio_usd": round(total / n, 4) if n else None,
        "com_fallback": sum(r["fallback"] for r in registros),
        "tokens": {k: sum(r["uso"][k] for r in registros) for k in registros[0]["uso"]} if n else {},
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
    except anthropic.AuthenticationError:
        sys.exit("sem credencial válida: defina ANTHROPIC_API_KEY no .env ou rode `ant auth login`")
