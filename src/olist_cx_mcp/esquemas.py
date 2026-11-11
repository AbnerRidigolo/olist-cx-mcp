"""Tipos de entrada (validados com Pydantic, com descrição e exemplo) e modelos de saída das ferramentas.

As mensagens de erro dizem ao agente o que corrigir.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from .classificador import CLASSES

UFS = frozenset(
    "AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO".split()
)
_HEX32 = re.compile(r"^[0-9a-f]{32}$")

Motivo = Literal[
    "atraso_ou_nao_entrega",
    "produto_errado_ou_incompleto",
    "defeito_ou_avaria",
    "qualidade_abaixo_do_esperado",
    "atendimento_vendedor",
    "elogio",
    "outro",
]
assert set(Motivo.__args__) == set(CLASSES)


def _uf(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip().upper()
    if s not in UFS:
        raise ValueError(f"estado inválido ({v!r}); use a sigla de 2 letras, como SP, RJ ou MG")
    return s


def _vendedor(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip().lower()
    if not _HEX32.match(s):
        raise ValueError(
            "vendedor_id deve ter 32 caracteres hexadecimais, como 4a3ca9315b744ce9f8e9374361493884; "
            "obtenha ids com cx_reason_breakdown ou cx_delivery_delay_stats usando agrupar_por='vendedor'"
        )
    return s


EstadoCliente = Annotated[
    str | None,
    AfterValidator(_uf),
    Field(description="Sigla da UF do cliente (comprador).", examples=["SP"]),
]
EstadoVendedor = Annotated[
    str | None,
    AfterValidator(_uf),
    Field(description="Sigla da UF do vendedor.", examples=["PR"]),
]
VendedorId = Annotated[
    str | None,
    AfterValidator(_vendedor),
    Field(description="Id do vendedor (32 caracteres hexadecimais).", examples=["4a3ca9315b744ce9f8e9374361493884"]),
]
VendedorIdObrigatorio = Annotated[
    str,
    AfterValidator(_vendedor),
    Field(description="Id do vendedor (32 caracteres hexadecimais).", examples=["4a3ca9315b744ce9f8e9374361493884"]),
]
Categoria = Annotated[
    str | None,
    Field(
        description="Categoria do produto, no nome original em português do dataset ou em inglês. "
        "Pedidos com itens de várias categorias contam em cada uma.",
        examples=["cama_mesa_banho"],
        min_length=2,
        max_length=60,
    ),
]
DataInicioAvaliacao = Annotated[
    date | None,
    Field(description="Início do período (inclusive), pela data de criação da avaliação. ISO AAAA-MM-DD.",
          examples=["2018-01-01"]),
]
DataFimAvaliacao = Annotated[
    date | None,
    Field(description="Fim do período (inclusive), pela data de criação da avaliação. ISO AAAA-MM-DD.",
          examples=["2018-03-31"]),
]
DataInicioCompra = Annotated[
    date | None,
    Field(description="Início do período (inclusive), pela data da compra. ISO AAAA-MM-DD.", examples=["2018-01-01"]),
]
DataFimCompra = Annotated[
    date | None,
    Field(description="Fim do período (inclusive), pela data da compra. ISO AAAA-MM-DD.", examples=["2018-03-31"]),
]
NotaMin = Annotated[int | None, Field(ge=1, le=5, description="Nota mínima (1 a 5).", examples=[1])]
NotaMax = Annotated[int | None, Field(ge=1, le=5, description="Nota máxima (1 a 5).", examples=[2])]
Offset = Annotated[int, Field(ge=0, le=100_000, description="Quantos itens pular (paginação).", examples=[0])]


def Limit(padrao: int, maximo: int):  # noqa: N802 - fábrica de tipo
    return Annotated[
        int, Field(ge=1, le=maximo, description=f"Máximo de itens por página (1 a {maximo}).", examples=[padrao])
    ]


Dimensao = Literal["nenhum", "estado_cliente", "estado_vendedor", "categoria", "vendedor", "mes", "nota"]
DimensaoAtraso = Literal["nenhum", "estado_cliente", "estado_vendedor", "rota", "categoria", "vendedor", "mes"]


# ---------- saídas ----------

class Pagina(BaseModel):
    total: int = Field(description="Total de itens que satisfazem os filtros.")
    limit: int
    offset: int
    proximo_offset: int | None = Field(description="offset da próxima página; null se esta for a última.")


class Avaliacao(BaseModel):
    review_id: str
    order_id: str
    data_avaliacao: date
    nota: int
    motivo: Motivo | None = Field(description="Motivo previsto pelo classificador; null se não há comentário.")
    confianca: float | None
    titulo: str | None
    comentario: str | None
    estado_cliente: str
    vendedores: list[str]
    categorias: list[str]


class ResultadoBusca(BaseModel):
    pagina: Pagina
    avaliacoes: list[Avaliacao]


class ContagemMotivo(BaseModel):
    motivo: Motivo
    n: int
    pct: float = Field(description="Percentual sobre as avaliações classificadas do recorte.")


class GrupoMotivos(BaseModel):
    grupo: str
    total_classificadas: int
    motivo_principal: Motivo
    motivos: list[ContagemMotivo]


class ResultadoMotivos(BaseModel):
    total_classificadas: int = Field(description="Avaliações com comentário (classificadas) no recorte.")
    distribuicao: list[ContagemMotivo]
    agrupar_por: Dimensao
    pagina_grupos: Pagina | None
    grupos: list[GrupoMotivos]
    modelo: str = Field(description="Classificador que gerou os motivos.")


class Comparativo(BaseModel):
    vendedor: float | None
    plataforma: float | None


class PerfilVendedor(BaseModel):
    vendedor_id: str
    cidade: str
    estado: str
    pedidos: int
    itens_vendidos: int
    receita_itens: float = Field(description="Soma do preço dos itens (R$), sem frete.")
    primeira_venda: date
    ultima_venda: date
    principais_categorias: list[dict]
    avaliacoes: int
    nota_media: Comparativo
    pct_nota_baixa: Comparativo = Field(description="% de avaliações com nota 1 ou 2.")
    classificadas: int
    motivos: list[ContagemMotivo]
    pct_motivos_plataforma: dict[str, float]
    entregues: int
    pct_atrasados: Comparativo
    dias_atraso_medio_quando_atrasa: Comparativo
    pagina_reclamacoes: Pagina
    reclamacoes_recentes: list[Avaliacao] = Field(description="Avaliações com nota <= 2 e comentário, mais recentes primeiro.")


class GrupoAtraso(BaseModel):
    grupo: str
    entregues: int
    atrasados: int
    pct_atrasados: float
    dias_atraso_medio_quando_atrasa: float | None
    dias_ate_entrega_medio: float | None
    nota_media_atrasados: float | None
    nota_media_no_prazo: float | None
    pct_reclamacao_atraso: float | None = Field(
        description="% das avaliações classificadas do grupo com motivo atraso_ou_nao_entrega.")


class ResultadoAtraso(BaseModel):
    geral: GrupoAtraso
    pedidos_nao_entregues: int = Field(description="Pedidos do recorte sem entrega registrada (status != delivered).")
    agrupar_por: DimensaoAtraso
    pagina_grupos: Pagina | None
    grupos: list[GrupoAtraso]


class Probabilidade(BaseModel):
    motivo: Motivo
    probabilidade: float


class ResultadoClassificacao(BaseModel):
    motivo: Motivo
    confianca: float
    probabilidades: list[Probabilidade]
    baixa_confianca: bool = Field(description="True quando a confiança é menor que 0,5: trate o motivo com cautela.")
    modelo: str
