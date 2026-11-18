"""Reaplica a correção atual (rodar.corrigir) a um resultado salvo, sem chamar o modelo de novo.

Usado quando a regra de correção muda (ex.: passar a ignorar acentos). Mantém o arquivo original e grava
<nome>_recorrigido.json com o resumo recalculado e a contagem antes/depois.

Uso: python eval\\recorrigir.py eval\\resultados\\<arquivo>.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from perguntas import PERGUNTAS  # noqa: E402
from rodar import corrigir  # noqa: E402

CHAVES = {p["id"]: p["chaves"] for p in PERGUNTAS}


def main(caminho: Path) -> None:
    d = json.loads(caminho.read_text(encoding="utf-8"))
    antes = sum(r["correta"] for r in d["registros"])
    por_pergunta: dict[str, list[bool]] = {}
    for r in d["registros"]:
        # usa as chaves gravadas no registro (o conjunto de perguntas pode ter mudado depois da execução)
        gabarito = {k: v["esperado"] for k, v in r["chaves"].items()}
        tipos = {k: CHAVES[r["id"]][k] for k in gabarito}
        r.update(corrigir(r["resposta"], gabarito, tipos))
        por_pergunta.setdefault(r["id"], []).append(r["correta"])
    depois = sum(r["correta"] for r in d["registros"])
    n = len(d["registros"])
    d["resumo"] |= {"acertos": depois, "taxa_acerto": round(depois / n, 3),
                    "acertos_por_pergunta": {k: f"{sum(v)}/{len(v)}" for k, v in por_pergunta.items()},
                    "recorrecao": {"regra": "textos comparados sem acento e sem caixa",
                                   "acertos_antes": antes, "acertos_depois": depois, "arquivo_original": caminho.name}}
    saida = caminho.with_name(caminho.stem + "_recorrigido.json")
    saida.write_text(json.dumps(d, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"acertos: {antes}/{n} -> {depois}/{n}; gravado em {saida.name}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(Path(sys.argv[1]))
