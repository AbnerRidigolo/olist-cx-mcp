"""Gera docs/demo.gif a partir de uma execução REAL da avaliação.

Do arquivo de resultados vêm a pergunta, as chamadas que o modelo fez (ferramenta e argumentos) e a resposta
final dele. Os resumos das ferramentas são obtidos reexecutando essas mesmas chamadas no servidor (as
ferramentas são determinísticas e somente leitura), sem chamar o modelo de novo.

Uso: python scripts\\gerar_gif.py eval\\resultados\\<arquivo>.json q12 1
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "eval"))
from perguntas import PERGUNTAS  # noqa: E402

LARGURA, ALTURA, MARGEM, COLUNAS = 1100, 640, 24, 96
FUNDO, TEXTO, CINZA, AZUL, VERDE, AMARELO = "#0f1419", "#e6e1cf", "#7f8b99", "#59c2ff", "#aad94c", "#ffb454"


def _fonte(tamanho: int, negrito: bool = False):
    for nome in (("consolab.ttf" if negrito else "consola.ttf"), "DejaVuSansMono.ttf"):
        for pasta in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (pasta / nome).exists():
                return ImageFont.truetype(str(pasta / nome), tamanho)
    return ImageFont.load_default()


async def _resumos(chamadas: list[dict]) -> list[str]:
    from mcp import Client

    from olist_cx_mcp.servidor import criar_servidor

    saida = []
    async with Client(criar_servidor()) as c:
        for ch in chamadas:
            r = await c.call_tool(ch["ferramenta"], ch["args"])
            saida.append(r.content[0].text.splitlines()[0])
    return saida


def _linhas_do_roteiro(registro: dict, pergunta: str, resumos: list[str]) -> list[tuple[str, str]]:
    """Lista de (cor, texto) já quebrada na largura do terminal."""
    roteiro: list[tuple[str, str]] = []

    def add(cor, texto, recuo=""):
        for i, parte in enumerate(textwrap.wrap(texto, COLUNAS - len(recuo)) or [""]):
            roteiro.append((cor, (recuo if i else "") + parte if i else parte))

    add(AMARELO, "Pergunta: " + pergunta)
    roteiro.append((TEXTO, ""))
    for ch, resumo in zip(registro["chamadas"], resumos):
        args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in ch["args"].items())
        add(AZUL, f"-> {ch['ferramenta']}({args})")
        add(CINZA, f"   <- {resumo}", recuo="      ")
    roteiro.append((TEXTO, ""))
    corpo = registro["texto_final"].split("RESPOSTA:")[0].strip()
    add(VERDE, "Resposta: " + corpo)
    return roteiro


def _quadro(cabecalho: str, linhas: list[tuple[str, str]]) -> Image.Image:
    img = Image.new("RGB", (LARGURA, ALTURA), FUNDO)
    d = ImageDraw.Draw(img)
    fonte, negrito = _fonte(17), _fonte(17, True)
    d.rectangle([0, 0, LARGURA, 34], fill="#1c2430")
    for i, cor in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        d.ellipse([14 + i * 22, 11, 26 + i * 22, 23], fill=cor)
    d.text((90, 8), cabecalho, font=negrito, fill=CINZA)
    y, altura_linha = 50, 22
    visiveis = linhas[-((ALTURA - 60) // altura_linha):]
    for cor, texto in visiveis:
        d.text((MARGEM, y), texto, font=fonte, fill=cor)
        y += altura_linha
    return img


def main(arquivo: Path, id_pergunta: str, repeticao: int) -> None:
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    reg = next(r for r in dados["registros"] if r["id"] == id_pergunta and r["repeticao"] == repeticao)
    pergunta = next(p["pergunta"] for p in PERGUNTAS if p["id"] == id_pergunta)
    resumos = asyncio.run(_resumos(reg["chamadas"]))
    roteiro = _linhas_do_roteiro(reg, pergunta, resumos)
    r = dados["resumo"]
    cabecalho = f"{r['modelo']} + olist-cx-mcp  |  execução real {r['data_utc'][:10]}  |  {id_pergunta}"

    quadros, duracoes = [_quadro(cabecalho, [])], [600]
    for i in range(1, len(roteiro) + 1):
        quadros.append(_quadro(cabecalho, roteiro[:i]))
        duracoes.append(900 if roteiro[i - 1][0] == AZUL else 350)
    duracoes[-1] = 6000
    destino = RAIZ / "docs" / "demo.gif"
    destino.parent.mkdir(exist_ok=True)
    quadros[0].save(destino, save_all=True, append_images=quadros[1:], duration=duracoes, loop=0, optimize=True)
    print(f"{destino.relative_to(RAIZ)}: {len(quadros)} quadros, {destino.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    logging.disable(logging.INFO)
    sys.stdout.reconfigure(encoding="utf-8")
    main(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))
