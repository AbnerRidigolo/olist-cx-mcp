"""Servidor MCP olist-cx-mcp (SDK mcp v2, MCPServer).

Ferramentas somente leitura sobre as avaliações da Olist classificadas por motivo. Cada ferramenta
devolve JSON estruturado (structured_content, com outputSchema) e um resumo em texto.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from .classificador import Classificador, ClassificadorSklearn
from .consultas import Banco, ErroConsulta
from .esquemas import (
    Categoria,
    DataFimAvaliacao,
    DataFimCompra,
    DataInicioAvaliacao,
    DataInicioCompra,
    Dimensao,
    DimensaoAtraso,
    EstadoCliente,
    EstadoVendedor,
    Limit,
    Motivo,
    NotaMax,
    NotaMin,
    Offset,
    PerfilVendedor,
    ResultadoAtraso,
    ResultadoBusca,
    ResultadoClassificacao,
    ResultadoMotivos,
    VendedorId,
    VendedorIdObrigatorio,
)

RAIZ = Path(__file__).resolve().parents[2]

INSTRUCOES = """\
Dados públicos da Olist (e-commerce brasileiro), somente leitura. Avaliações de clientes criadas entre
2016-10 e 2018-08; compras entre 2016-09 e 2018-10. Cada avaliação com comentário tem um `motivo` previsto
por um classificador (7 classes; veja `modelo` nas respostas): é uma previsão, não um rótulo humano.
Um pedido pode ter mais de um vendedor e mais de uma categoria: a avaliação conta para cada um.
Atraso = entrega ao cliente depois da data estimada informada na compra (só pedidos entregues).
Use agrupar_por='vendedor' para descobrir ids de vendedores. Pagine com limit/offset (proximo_offset).
"""

SOMENTE_LEITURA = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                  open_world_hint=False)

_MSG_PYDANTIC = {
    "less_than_equal": "deve ser <= {le}",
    "greater_than_equal": "deve ser >= {ge}",
    "literal_error": "valor não permitido; use um de: {expected}",
    "string_too_short": "texto curto demais (mínimo {min_length} caracteres)",
    "string_too_long": "texto longo demais (máximo {max_length} caracteres)",
    "date_from_datetime_parsing": "data inválida; use o formato ISO AAAA-MM-DD, como 2018-03-01",
    "date_parsing": "data inválida; use o formato ISO AAAA-MM-DD, como 2018-03-01",
    "date_from_datetime_inexact": "use só a data (AAAA-MM-DD), sem horário",
    "int_parsing": "deve ser um número inteiro",
    "int_from_float": "deve ser um número inteiro",
    "int_type": "deve ser um número inteiro",
    "bool_parsing": "deve ser true ou false",
    "string_type": "deve ser um texto",
    "missing": "campo obrigatório ausente",
}


def traduzir_validacao(exc: ValidationError) -> str:
    """Erros de validação em português, um por campo, dizendo o que corrigir (sem o link do Pydantic)."""
    partes = []
    for e in exc.errors():
        campo = ".".join(str(p) for p in e["loc"]) or "argumentos"
        if e["type"] == "value_error":
            msg = str(e["ctx"]["error"]) if "ctx" in e else e["msg"]
        else:
            modelo = _MSG_PYDANTIC.get(e["type"])
            ctx = {k: str(v).replace(" or ", " ou ") for k, v in e.get("ctx", {}).items()}
            try:
                msg = modelo.format(**ctx) if modelo else e["msg"]
            except KeyError:
                msg = e["msg"]
        partes.append(f"{campo}: {msg}")
    return "Argumentos inválidos. " + "; ".join(partes) + ". Corrija e chame a ferramenta de novo."


class ServidorCX(MCPServer):
    """MCPServer com erros de validação traduzidos para mensagens acionáveis."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            if isinstance(exc.__cause__, ValidationError):
                raise ToolError(f"{name}: {traduzir_validacao(exc.__cause__)}") from exc.__cause__
            msg = str(exc).removeprefix(f"Error executing tool {name}: ")
            raise ToolError(f"{name}: {msg}") from exc.__cause__


def _resultado(modelo: BaseModel, resumo: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=resumo)],
                          structured_content=modelo.model_dump(mode="json"))


def _reais(x: float) -> str:
    return f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _faixa(p) -> str:
    if p.total == 0:
        return "nenhum resultado"
    fim = p.offset + (min(p.limit, p.total - p.offset))
    mais = f"; próxima página: offset={p.proximo_offset}" if p.proximo_offset is not None else ""
    return f"{p.total} no total, mostrando {p.offset + 1}-{fim}{mais}"


def _cobertura(inicio: date | None, fim: date | None, faixa: tuple[date, date], nome: str) -> str:
    ini_d, fim_d = faixa
    if (inicio and inicio > fim_d) or (fim and fim < ini_d):
        return f" O período pedido está fora da cobertura do dataset ({nome}: {ini_d} a {fim_d})."
    return ""


def criar_servidor(caminho_banco: str | Path | None = None, classificador: Classificador | None = None) -> ServidorCX:
    if caminho_banco is None:
        caminho_banco = RAIZ / os.getenv("OLIST_DUCKDB_PATH", "data/olist.duckdb")
    banco = Banco(Path(caminho_banco))
    _modelo: list[Classificador] = [classificador] if classificador else []

    def obter_classificador() -> Classificador:
        if not _modelo:
            _modelo.append(ClassificadorSklearn(RAIZ / os.getenv("MOTIVO_MODELO_PATH", "models/motivo/modelo.joblib")))
        return _modelo[0]

    mcp = ServidorCX("olist-cx-mcp", title="Olist CX (somente leitura)", instructions=INSTRUCOES, version="0.1.0")
    mcp.banco = banco  # útil em testes

    def erro(e: ErroConsulta) -> ToolError:
        return ToolError(str(e))

    # ------------------------------------------------------------------ cx_search_reviews
    @mcp.tool(annotations=SOMENTE_LEITURA, title="Buscar avaliações")
    def cx_search_reviews(
        motivo: Annotated[Motivo | None, Field(description="Filtra pelo motivo previsto.",
                                               examples=["atraso_ou_nao_entrega"])] = None,
        texto: Annotated[str | None, Field(description="Trecho a procurar no título ou comentário "
                                           "(ignora maiúsculas e acentos).", examples=["nao recebi"],
                                           min_length=2, max_length=100)] = None,
        estado_cliente: EstadoCliente = None,
        estado_vendedor: EstadoVendedor = None,
        vendedor_id: VendedorId = None,
        categoria: Categoria = None,
        nota_min: NotaMin = None,
        nota_max: NotaMax = None,
        data_inicio: DataInicioAvaliacao = None,
        data_fim: DataFimAvaliacao = None,
        somente_com_comentario: Annotated[bool, Field(description="Se true, só avaliações com comentário.",
                                                      examples=[True])] = True,
        ordenar_por: Annotated[Literal["recentes", "antigas", "menor_nota", "maior_confianca"],
                               Field(description="Ordem dos resultados.", examples=["recentes"])] = "recentes",
        limit: Limit(10, 50) = 10,
        offset: Offset = 0,
    ) -> Annotated[CallToolResult, ResultadoBusca]:
        """Busca avaliações de clientes com filtros (motivo, texto, estado, vendedor, categoria, nota, período).

        Use para ler exemplos concretos de comentários. Para contagens e percentuais, prefira
        cx_reason_breakdown. Retorna uma página de avaliações e o total que satisfaz os filtros.
        """
        try:
            banco.checar_periodo(data_inicio, data_fim)
            if nota_min and nota_max and nota_min > nota_max:
                raise ErroConsulta(f"nota_min ({nota_min}) é maior que nota_max ({nota_max})")
            filtros = {"motivo": motivo, "texto": texto, "estado_cliente": estado_cliente,
                       "estado_vendedor": estado_vendedor, "vendedor_id": vendedor_id,
                       "categoria": banco.resolver_categoria(categoria), "nota_min": nota_min,
                       "nota_max": nota_max, "data_inicio": data_inicio, "data_fim": data_fim}
            r = ResultadoBusca.model_validate(banco.buscar_avaliacoes(
                filtros=filtros, somente_com_comentario=somente_com_comentario, ordenar_por=ordenar_por,
                limit=limit, offset=offset))
        except ErroConsulta as e:
            raise erro(e) from e
        linhas = [f"Avaliações: {_faixa(r.pagina)}."
                  + _cobertura(data_inicio, data_fim, banco.cobertura_avaliacoes, "avaliações")]
        for a in r.avaliacoes:
            txt = (a.comentario or a.titulo or "(sem comentário)").replace("\n", " ")
            linhas.append(f"- {a.data_avaliacao} nota {a.nota} {a.estado_cliente} [{a.motivo or '-'}] "
                          f"{txt[:160]}{'...' if len(txt) > 160 else ''}")
        return _resultado(r, "\n".join(linhas))

    # ------------------------------------------------------------------ cx_reason_breakdown
    @mcp.tool(annotations=SOMENTE_LEITURA, title="Distribuição de motivos")
    def cx_reason_breakdown(
        agrupar_por: Annotated[Dimensao, Field(description="Dimensão para quebrar a distribuição; 'nenhum' "
                                               "retorna só o total.", examples=["estado_cliente"])] = "nenhum",
        ordenar_por: Annotated[Literal["total", "grupo", "atraso_ou_nao_entrega", "produto_errado_ou_incompleto",
                                       "defeito_ou_avaria", "qualidade_abaixo_do_esperado", "atendimento_vendedor",
                                       "elogio", "outro"],
                               Field(description="Ordem dos grupos: 'total' (mais avaliações), 'grupo' "
                                     "(alfabética/cronológica) ou o nome de um motivo (maior % desse motivo).",
                                     examples=["atraso_ou_nao_entrega"])] = "total",
        min_avaliacoes: Annotated[int, Field(ge=1, le=10_000, description="Descarta grupos com menos avaliações "
                                             "classificadas que isso (evita % de amostras pequenas).",
                                             examples=[30])] = 1,
        estado_cliente: EstadoCliente = None,
        estado_vendedor: EstadoVendedor = None,
        vendedor_id: VendedorId = None,
        categoria: Categoria = None,
        nota_min: NotaMin = None,
        nota_max: NotaMax = None,
        data_inicio: DataInicioAvaliacao = None,
        data_fim: DataFimAvaliacao = None,
        limit: Limit(20, 100) = 20,
        offset: Offset = 0,
    ) -> Annotated[CallToolResult, ResultadoMotivos]:
        """Conta e percentualiza os motivos das avaliações com comentário, com filtros e quebra opcional
        por estado do cliente, estado do vendedor, categoria, vendedor, mês ou nota.

        Os percentuais são sobre as avaliações classificadas do recorte (ou do grupo).
        """
        try:
            banco.checar_periodo(data_inicio, data_fim)
            filtros = {"estado_cliente": estado_cliente, "estado_vendedor": estado_vendedor,
                       "vendedor_id": vendedor_id, "categoria": banco.resolver_categoria(categoria),
                       "nota_min": nota_min, "nota_max": nota_max, "data_inicio": data_inicio, "data_fim": data_fim}
            r = ResultadoMotivos.model_validate(banco.motivos(
                filtros=filtros, agrupar_por=agrupar_por, ordenar_por=ordenar_por, min_avaliacoes=min_avaliacoes,
                limit=limit, offset=offset))
        except ErroConsulta as e:
            raise erro(e) from e
        top = ", ".join(f"{m.motivo} {m.pct}%" for m in r.distribuicao if m.n)
        linhas = [f"{r.total_classificadas} avaliações classificadas (modelo {r.modelo}). {top or 'sem dados'}."
                  + _cobertura(data_inicio, data_fim, banco.cobertura_avaliacoes, "avaliações")]
        if r.pagina_grupos:
            linhas.append(f"Grupos por {agrupar_por}: {_faixa(r.pagina_grupos)}.")
            for g in r.grupos:
                destaque = (next(m for m in g.motivos if m.motivo == ordenar_por)
                            if ordenar_por not in ("total", "grupo") else g.motivos[0])
                linhas.append(f"- {g.grupo}: {g.total_classificadas} avaliações; "
                              f"{destaque.motivo} {destaque.pct}%")
        return _resultado(r, "\n".join(linhas))

    # ------------------------------------------------------------------ cx_seller_profile
    @mcp.tool(annotations=SOMENTE_LEITURA, title="Perfil do vendedor")
    def cx_seller_profile(
        vendedor_id: VendedorIdObrigatorio,
        limit: Limit(5, 50) = 5,
        offset: Offset = 0,
    ) -> Annotated[CallToolResult, PerfilVendedor]:
        """Perfil de experiência do cliente de um vendedor: volume, receita, categorias, nota média, motivos
        das avaliações e taxa de atraso, sempre comparados com a plataforma, e uma página das reclamações
        recentes (nota <= 2 com comentário; pagine com limit/offset).
        """
        try:
            r = PerfilVendedor.model_validate(banco.perfil_vendedor(vendedor_id, limit, offset))
        except ErroConsulta as e:
            raise erro(e) from e
        principais = ", ".join(f"{m.motivo} {m.pct}% (plataforma {r.pct_motivos_plataforma.get(m.motivo)}%)"
                               for m in r.motivos[:3] if m.n)
        resumo = (
            f"Vendedor {r.vendedor_id} ({r.cidade}/{r.estado}): {r.pedidos} pedidos, {r.itens_vendidos} itens, "
            f"R$ {_reais(r.receita_itens)} em itens, de {r.primeira_venda} a {r.ultima_venda}.\n"
            f"Nota média {r.nota_media.vendedor} (plataforma {r.nota_media.plataforma}); "
            f"{r.pct_nota_baixa.vendedor}% notas 1-2 (plataforma {r.pct_nota_baixa.plataforma}%).\n"
            f"Atraso: {r.pct_atrasados.vendedor}% dos {r.entregues} entregues (plataforma {r.pct_atrasados.plataforma}%).\n"
            f"Motivos ({r.classificadas} classificadas): {principais or 'sem comentários'}.\n"
            f"Reclamações recentes: {_faixa(r.pagina_reclamacoes)}."
        )
        return _resultado(r, resumo)

    # ------------------------------------------------------------------ cx_delivery_delay_stats
    @mcp.tool(annotations=SOMENTE_LEITURA, title="Estatísticas de atraso na entrega")
    def cx_delivery_delay_stats(
        agrupar_por: Annotated[DimensaoAtraso, Field(description="Dimensão para quebrar; 'rota' é UF do "
                                                     "vendedor->UF do cliente.", examples=["estado_cliente"])] = "nenhum",
        ordenar_por: Annotated[Literal["pct_atrasados", "entregues", "dias_atraso_medio", "nota_media_atrasados",
                                       "grupo"],
                               Field(description="Ordem dos grupos (decrescente, exceto nota_media_atrasados "
                                     "e grupo, crescentes).", examples=["pct_atrasados"])] = "pct_atrasados",
        min_pedidos: Annotated[int, Field(ge=1, le=100_000, description="Descarta grupos com menos pedidos "
                                          "entregues que isso.", examples=[100])] = 1,
        estado_cliente: EstadoCliente = None,
        estado_vendedor: EstadoVendedor = None,
        vendedor_id: VendedorId = None,
        categoria: Categoria = None,
        data_inicio: DataInicioCompra = None,
        data_fim: DataFimCompra = None,
        limit: Limit(20, 100) = 20,
        offset: Offset = 0,
    ) -> Annotated[CallToolResult, ResultadoAtraso]:
        """Taxa de atraso na entrega (entrega depois da data estimada), dias de atraso, prazo médio, nota
        média de pedidos atrasados vs. no prazo e % de reclamações de atraso, com filtros e quebra opcional.
        O período filtra pela data da compra.
        """
        try:
            banco.checar_periodo(data_inicio, data_fim)
            filtros = {"estado_cliente": estado_cliente, "estado_vendedor": estado_vendedor,
                       "vendedor_id": vendedor_id, "categoria": banco.resolver_categoria(categoria),
                       "data_inicio": data_inicio, "data_fim": data_fim}
            r = ResultadoAtraso.model_validate(banco.atrasos(
                filtros=filtros, agrupar_por=agrupar_por, ordenar_por=ordenar_por, min_pedidos=min_pedidos,
                limit=limit, offset=offset))
        except ErroConsulta as e:
            raise erro(e) from e
        g = r.geral
        cob = _cobertura(data_inicio, data_fim, banco.cobertura_compras, "compras")
        if g.entregues == 0:
            return _resultado(r, f"Nenhum pedido entregue no recorte; {r.pedidos_nao_entregues} pedidos sem entrega.{cob}")
        linhas = [f"{g.entregues} pedidos entregues, {g.atrasados} atrasados ({g.pct_atrasados}%); atraso médio "
                  f"{g.dias_atraso_medio_quando_atrasa} dias quando atrasa; nota média {g.nota_media_atrasados} "
                  f"atrasados vs {g.nota_media_no_prazo} no prazo; {r.pedidos_nao_entregues} pedidos sem entrega."
                  + _cobertura(data_inicio, data_fim, banco.cobertura_compras, "compras")]
        if r.pagina_grupos:
            linhas.append(f"Grupos por {agrupar_por}: {_faixa(r.pagina_grupos)}.")
            for x in r.grupos:
                linhas.append(f"- {x.grupo}: {x.pct_atrasados}% de {x.entregues} entregues; "
                              f"{x.dias_atraso_medio_quando_atrasa} dias quando atrasa")
        return _resultado(r, "\n".join(linhas))

    # ------------------------------------------------------------------ cx_classify_review
    @mcp.tool(annotations=SOMENTE_LEITURA, title="Classificar comentário")
    def cx_classify_review(
        texto: Annotated[str, Field(description="Comentário de cliente em português.",
                                    examples=["Comprei há 20 dias e até agora não recebi o produto"],
                                    min_length=3, max_length=2000)],
    ) -> Annotated[CallToolResult, ResultadoClassificacao]:
        """Classifica o motivo de um comentário novo com o mesmo modelo usado no dataset.
        Não grava nada. Confiança abaixo de 0,5 indica previsão incerta.
        """
        if not texto.strip():
            raise ToolError("texto: está vazio; envie o comentário do cliente")
        p = obter_classificador().classificar([texto])[0]
        probs = sorted(p.probabilidades.items(), key=lambda kv: -kv[1])
        r = ResultadoClassificacao(
            motivo=p.motivo, confianca=round(p.confianca, 4),
            probabilidades=[{"motivo": m, "probabilidade": round(v, 4)} for m, v in probs],
            baixa_confianca=p.confianca < 0.5, modelo=obter_classificador().nome)
        alt = ", ".join(f"{m} {v:.0%}" for m, v in probs[1:3])
        aviso = " Confiança baixa: trate com cautela." if r.baixa_confianca else ""
        return _resultado(r, f"Motivo: {r.motivo} ({r.confianca:.0%}). Alternativas: {alt}.{aviso}")

    # ------------------------------------------------------------------ prompt
    @mcp.prompt(title="Relatório semanal de CX")
    def relatorio_semanal_cx(
        semana_inicio: Annotated[str, Field(description="Segunda-feira da semana, ISO AAAA-MM-DD "
                                            "(dados entre 2016-10 e 2018-08).", examples=["2018-05-07"])],
        estado_cliente: Annotated[str | None, Field(description="UF opcional para focar o relatório.",
                                                    examples=["SP"])] = None,
    ) -> str:
        """Roteiro para um relatório semanal de experiência do cliente usando as ferramentas cx_*."""
        try:
            ini = date.fromisoformat(semana_inicio)
        except ValueError as e:
            raise ValueError("semana_inicio deve estar no formato AAAA-MM-DD, como 2018-05-07") from e
        from datetime import timedelta

        fim, ant_ini, ant_fim = ini + timedelta(days=6), ini - timedelta(days=7), ini - timedelta(days=1)
        uf = f" com estado_cliente='{estado_cliente.upper()}'" if estado_cliente else ""
        foco = f" no estado {estado_cliente.upper()}" if estado_cliente else ""
        return f"""Prepare o relatório semanal de experiência do cliente da Olist{foco} para a semana de {ini} a {fim}.

Passos (use sempre os filtros de período indicados{uf}):
1. cx_reason_breakdown com data_inicio={ini} e data_fim={fim}; depois o mesmo para a semana anterior ({ant_ini} a {ant_fim}). Compare volume e % de cada motivo.
2. cx_reason_breakdown na semana atual com agrupar_por='categoria', ordenar_por='total', min_avaliacoes=5, para achar as categorias com mais reclamações.
3. cx_reason_breakdown na semana atual com agrupar_por='vendedor', ordenar_por='total', nota_max=2, limit=5, para os vendedores com mais avaliações negativas; abra os 2 primeiros com cx_seller_profile.
4. cx_delivery_delay_stats com as compras das 4 semanas anteriores ao fim da semana ({ini - timedelta(days=21)} a {fim}) e agrupar_por='estado_cliente', min_pedidos=30.
5. cx_search_reviews com nota_max=2 no período, limit=5, para citar 3 comentários representativos.

Formato da resposta (em português, até 1 página):
- Resumo em 3 frases com os números principais.
- Tabela: motivo | semana atual (n, %) | semana anterior (n, %) | variação em p.p.
- Destaques: categorias, vendedores e estados que pioraram, com números.
- 3 comentários citados (com motivo e nota).
- 2 ou 3 ações recomendadas.
Não invente números: use só o que as ferramentas retornarem e diga quando o volume for pequeno demais para concluir.
Os motivos vêm de um classificador automático: mencione isso no rodapé."""

    return mcp
