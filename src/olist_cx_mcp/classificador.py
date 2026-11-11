"""Classificador de motivo das avaliações.

Interface única para as ferramentas e o lote. Hoje a implementação é o baseline scikit-learn
(TF-IDF + regressão logística) do projeto fine-tuning-hugging-face; o BERTimbau publicado no
Hugging Face entra como outra implementação de `Classificador`, sem mudar quem chama.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

CLASSES = (
    "atraso_ou_nao_entrega",
    "produto_errado_ou_incompleto",
    "defeito_ou_avaria",
    "qualidade_abaixo_do_esperado",
    "atendimento_vendedor",
    "elogio",
    "outro",
)

_ESPACOS = re.compile(r"\s+")


def normalizar_texto(texto: str | None) -> str:
    """Mesma limpeza usada no treino: colapsa espaços e quebras de linha; mantém caixa e acentos."""
    return _ESPACOS.sub(" ", texto or "").strip()


@dataclass(frozen=True)
class Previsao:
    motivo: str
    confianca: float
    probabilidades: dict[str, float]


class Classificador(Protocol):
    nome: str

    def classificar(self, textos: Sequence[str]) -> list[Previsao]: ...


class ClassificadorSklearn:
    """Pipeline scikit-learn salvo com joblib (predict_proba sobre texto normalizado)."""

    def __init__(self, caminho: str | Path, nome: str = "baseline_tfidf_lr"):
        import joblib

        self.caminho = Path(caminho)
        if not self.caminho.exists():
            raise FileNotFoundError(
                f"modelo não encontrado em {self.caminho}; veja models/README.md para copiá-lo"
            )
        self._modelo = joblib.load(self.caminho)
        self.nome = nome
        faltando = set(CLASSES) - set(self._modelo.classes_)
        if faltando:
            raise ValueError(f"o modelo não tem as classes esperadas: {sorted(faltando)}")

    def classificar(self, textos: Sequence[str]) -> list[Previsao]:
        limpos = [normalizar_texto(t) for t in textos]
        probs = self._modelo.predict_proba(limpos)
        classes = list(self._modelo.classes_)
        saida = []
        for linha in probs:
            dist = {c: float(p) for c, p in zip(classes, linha)}
            motivo = max(dist, key=dist.get)
            saida.append(Previsao(motivo=motivo, confianca=dist[motivo], probabilidades=dist))
        return saida
