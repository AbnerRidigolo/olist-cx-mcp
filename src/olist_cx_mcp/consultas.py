"""Consultas somente leitura ao DuckDB.

Regra de segurança: todo valor vindo do agente entra como parâmetro `?`. Os pedaços de SQL que variam
(filtros, dimensão de agrupamento, ordenação) são escolhidos de dicionários constantes deste módulo a
partir de valores já validados (Literal), nunca montados com texto do usuário.
"""
from __future__ import annotations

import difflib
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from .classificador import CLASSES

MOTIVO_ATRASO = "atraso_ou_nao_entrega"


class ErroConsulta(ValueError):
    """Erro que o agente consegue corrigir (a mensagem diz como)."""


# ---------- fragmentos constantes ----------

# filtros sobre avaliações: alias a = cx_avaliacoes, p = cx_pedidos
FILTROS_AVALIACAO: dict[str, str] = {
    "estado_cliente": "p.estado_cliente = ?",
    "estado_vendedor": "EXISTS (SELECT 1 FROM cx_pedido_vendedor f WHERE f.order_id = a.order_id AND f.estado_vendedor = ?)",
    "vendedor_id": "EXISTS (SELECT 1 FROM cx_pedido_vendedor f WHERE f.order_id = a.order_id AND f.vendedor_id = ?)",
    "categoria": "EXISTS (SELECT 1 FROM cx_pedido_categoria f WHERE f.order_id = a.order_id AND f.categoria = ?)",
    "data_inicio": "a.data_avaliacao >= ?",
    "data_fim": "a.data_avaliacao <= ?",
    "nota_min": "a.nota >= ?",
    "nota_max": "a.nota <= ?",
    "motivo": "a.motivo = ?",
    "texto": "contains(strip_accents(lower(coalesce(a.titulo, '') || ' ' || coalesce(a.comentario, ''))), "
             "strip_accents(lower(?)))",
}

# filtros sobre pedidos: alias p = cx_pedidos
FILTROS_PEDIDO: dict[str, str] = {
    "estado_cliente": "p.estado_cliente = ?",
    "estado_vendedor": "EXISTS (SELECT 1 FROM cx_pedido_vendedor f WHERE f.order_id = p.order_id AND f.estado_vendedor = ?)",
    "vendedor_id": "EXISTS (SELECT 1 FROM cx_pedido_vendedor f WHERE f.order_id = p.order_id AND f.vendedor_id = ?)",
    "categoria": "EXISTS (SELECT 1 FROM cx_pedido_categoria f WHERE f.order_id = p.order_id AND f.categoria = ?)",
    "data_inicio": "p.data_compra >= ?",
    "data_fim": "p.data_compra <= ?",
}

# (join extra, expressão do grupo)
DIMENSOES_AVALIACAO: dict[str, tuple[str, str]] = {
    "estado_cliente": ("", "p.estado_cliente"),
    "estado_vendedor": ("JOIN cx_pedido_vendedor g ON g.order_id = a.order_id", "g.estado_vendedor"),
    "vendedor": ("JOIN cx_pedido_vendedor g ON g.order_id = a.order_id", "g.vendedor_id"),
    "categoria": ("JOIN cx_pedido_categoria g ON g.order_id = a.order_id", "g.categoria"),
    "mes": ("", "strftime(a.data_avaliacao, '%Y-%m')"),
    "nota": ("", "CAST(a.nota AS VARCHAR)"),
}
DIMENSOES_PEDIDO: dict[str, tuple[str, str]] = {
    "estado_cliente": ("", "p.estado_cliente"),
    "estado_vendedor": ("JOIN cx_pedido_vendedor g ON g.order_id = p.order_id", "g.estado_vendedor"),
    "rota": ("JOIN cx_pedido_vendedor g ON g.order_id = p.order_id", "g.estado_vendedor || '->' || p.estado_cliente"),
    "vendedor": ("JOIN cx_pedido_vendedor g ON g.order_id = p.order_id", "g.vendedor_id"),
    "categoria": ("JOIN cx_pedido_categoria g ON g.order_id = p.order_id", "g.categoria"),
    "mes": ("", "strftime(p.data_compra, '%Y-%m')"),
}
ORDEM_BUSCA: dict[str, str] = {
    "recentes": "a.data_avaliacao DESC, a.review_id",
    "antigas": "a.data_avaliacao ASC, a.review_id",
    "menor_nota": "a.nota ASC, a.data_avaliacao DESC, a.review_id",
    "maior_confianca": "a.confianca DESC NULLS LAST, a.review_id",
}

SQL_ATRASO_AGREGADO = """
    count(*) FILTER (WHERE entregue) AS entregues,
    count(*) FILTER (WHERE dias_atraso > 0) AS atrasados,
    avg(dias_atraso) FILTER (WHERE dias_atraso > 0) AS dias_atraso_medio,
    avg(dias_ate_entrega) FILTER (WHERE entregue) AS dias_ate_entrega_medio,
    avg(nota_pedido) FILTER (WHERE dias_atraso > 0) AS nota_atrasados,
    avg(nota_pedido) FILTER (WHERE entregue AND dias_atraso <= 0) AS nota_no_prazo,
    sum(n_classificadas) AS n_classificadas,
    sum(n_reclamacao_atraso) AS n_reclamacao_atraso,
    count(*) FILTER (WHERE NOT entregue) AS nao_entregues
"""
SQL_AVALIACOES_POR_PEDIDO = f"""
    SELECT order_id, avg(nota) AS nota_pedido, count(motivo) AS n_classificadas,
           count(*) FILTER (WHERE motivo = '{MOTIVO_ATRASO}') AS n_reclamacao_atraso
    FROM cx_avaliacoes GROUP BY order_id
"""


def _onde(filtros: dict[str, Any], catalogo: dict[str, str], extras: list[str] | None = None) -> tuple[str, list]:
    clausulas, params = list(extras or []), []
    for nome, valor in filtros.items():
        if valor is None:
            continue
        clausulas.append(catalogo[nome])  # KeyError = bug de programação, não entrada do usuário
        params.append(valor)
    return (" WHERE " + " AND ".join(clausulas)) if clausulas else "", params


def _pct(n: float, d: float) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def _r(x: float | None, casas: int = 2) -> float | None:
    return None if x is None else round(float(x), casas)


def paginar(total: int, limit: int, offset: int) -> dict:
    prox = offset + limit
    return {"total": total, "limit": limit, "offset": offset, "proximo_offset": prox if prox < total else None}


@dataclass
class Banco:
    caminho: Path
    _con: duckdb.DuckDBPyConnection = field(init=False, repr=False)
    _lock: threading.Lock = field(init=False, repr=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not Path(self.caminho).exists():
            raise FileNotFoundError(f"banco não encontrado em {self.caminho}; rode os scripts da Fase 0 e 1")
        self._con = duckdb.connect(str(self.caminho), read_only=True)
        cats = self._linhas("SELECT DISTINCT categoria, categoria_en FROM cx_pedido_categoria")
        self.categorias: dict[str, str] = {}
        for c in cats:
            self.categorias[c["categoria"].lower()] = c["categoria"]
            self.categorias[c["categoria_en"].lower()] = c["categoria"]
        cob = self._linhas("""
            SELECT (SELECT min(data_avaliacao) FROM cx_avaliacoes) AS av_ini,
                   (SELECT max(data_avaliacao) FROM cx_avaliacoes) AS av_fim,
                   (SELECT min(data_compra) FROM cx_pedidos) AS cp_ini,
                   (SELECT max(data_compra) FROM cx_pedidos) AS cp_fim,
                   (SELECT any_value(modelo) FROM cx_avaliacoes WHERE modelo IS NOT NULL) AS modelo
        """)[0]
        self.cobertura_avaliacoes = (cob["av_ini"], cob["av_fim"])
        self.cobertura_compras = (cob["cp_ini"], cob["cp_fim"])
        self.modelo = cob["modelo"] or "desconhecido"
        self.plataforma = self._calcular_plataforma()

    # ---------- infraestrutura ----------

    def _linhas(self, sql: str, params: list | None = None) -> list[dict]:
        with self._lock:
            cur = self._con.cursor()
            try:
                rel = cur.execute(sql, params or [])
                cols = [d[0] for d in rel.description]
                return [dict(zip(cols, linha)) for linha in rel.fetchall()]
            finally:
                cur.close()

    def fechar(self) -> None:
        self._con.close()

    def resolver_categoria(self, categoria: str | None) -> str | None:
        if categoria is None:
            return None
        chave = categoria.strip().lower().replace(" ", "_")
        if chave in self.categorias:
            return self.categorias[chave]
        parecidas = difflib.get_close_matches(chave, list(self.categorias), n=5, cutoff=0.5)
        dica = f"; parecidas: {', '.join(sorted({self.categorias[p] for p in parecidas}))}" if parecidas else ""
        raise ErroConsulta(
            f"categoria desconhecida ({categoria!r}); use o nome do dataset em snake_case, como "
            f"cama_mesa_banho ou beleza_saude{dica}. Para listar categorias, use cx_reason_breakdown "
            "com agrupar_por='categoria'"
        )

    def checar_periodo(self, inicio: date | None, fim: date | None) -> None:
        if inicio and fim and inicio > fim:
            raise ErroConsulta(f"data_inicio ({inicio}) é posterior a data_fim ({fim}); inverta as datas")

    def _calcular_plataforma(self) -> dict:
        mot = self._linhas("SELECT motivo, count(*) AS n FROM cx_avaliacoes WHERE motivo IS NOT NULL GROUP BY 1")
        tot = sum(m["n"] for m in mot)
        av = self._linhas("""SELECT avg(nota) AS nota_media, 100.0 * avg(CASE WHEN nota <= 2 THEN 1 ELSE 0 END) AS pct_baixa
                             FROM cx_avaliacoes""")[0]
        en = self._linhas("""SELECT 100.0 * count(*) FILTER (WHERE dias_atraso > 0) / count(*) AS pct_atr,
                                    avg(dias_atraso) FILTER (WHERE dias_atraso > 0) AS dias
                             FROM cx_pedidos WHERE entregue""")[0]
        return {
            "pct_motivos": {m["motivo"]: _pct(m["n"], tot) for m in mot},
            "nota_media": _r(av["nota_media"]),
            "pct_nota_baixa": _r(av["pct_baixa"], 1),
            "pct_atrasados": _r(en["pct_atr"], 1),
            "dias_atraso_medio": _r(en["dias"], 1),
        }

    # ---------- cx_search_reviews ----------

    def buscar_avaliacoes(self, *, filtros: dict, somente_com_comentario: bool, ordenar_por: str,
                          limit: int, offset: int) -> dict:
        extras = ["a.comentario IS NOT NULL"] if somente_com_comentario else []
        onde, params = _onde(filtros, FILTROS_AVALIACAO, extras)
        base = f"FROM cx_avaliacoes a JOIN cx_pedidos p USING (order_id){onde}"
        total = self._linhas(f"SELECT count(*) AS n {base}", params)[0]["n"]
        linhas = self._linhas(f"""
            SELECT a.review_id, a.order_id, a.data_avaliacao, a.nota, a.motivo, a.confianca, a.titulo,
                   a.comentario, p.estado_cliente,
                   (SELECT list(DISTINCT v.vendedor_id ORDER BY v.vendedor_id) FROM cx_pedido_vendedor v
                     WHERE v.order_id = a.order_id) AS vendedores,
                   (SELECT list(DISTINCT c.categoria ORDER BY c.categoria) FROM cx_pedido_categoria c
                     WHERE c.order_id = a.order_id) AS categorias
            {base} ORDER BY {ORDEM_BUSCA[ordenar_por]} LIMIT ? OFFSET ?
        """, params + [limit, offset])
        for li in linhas:
            li["vendedores"] = li["vendedores"] or []
            li["categorias"] = li["categorias"] or []
            li["confianca"] = _r(li["confianca"], 3)
        return {"pagina": paginar(total, limit, offset), "avaliacoes": linhas}

    # ---------- cx_reason_breakdown ----------

    def _contagens(self, lista: list[dict], total: int) -> list[dict]:
        por = {m["motivo"]: m["n"] for m in lista}
        return sorted(({"motivo": c, "n": por.get(c, 0), "pct": _pct(por.get(c, 0), total) or 0.0} for c in CLASSES),
                      key=lambda x: (-x["n"], x["motivo"]))

    def motivos(self, *, filtros: dict, agrupar_por: str, ordenar_por: str, min_avaliacoes: int,
                limit: int, offset: int) -> dict:
        onde, params = _onde(filtros, FILTROS_AVALIACAO, ["a.motivo IS NOT NULL"])
        geral = self._linhas(f"""SELECT a.motivo, count(*) AS n FROM cx_avaliacoes a JOIN cx_pedidos p USING (order_id)
                                {onde} GROUP BY 1""", params)
        total = sum(g["n"] for g in geral)
        saida = {"total_classificadas": total, "distribuicao": self._contagens(geral, total),
                 "agrupar_por": agrupar_por, "pagina_grupos": None, "grupos": [], "modelo": self.modelo}
        if agrupar_por == "nenhum":
            return saida

        join, expr = DIMENSOES_AVALIACAO[agrupar_por]
        linhas = self._linhas(f"""
            WITH base AS (
                SELECT DISTINCT a.review_id, a.order_id, a.motivo, {expr} AS grupo
                FROM cx_avaliacoes a JOIN cx_pedidos p USING (order_id) {join} {onde}
            )
            SELECT grupo, motivo, count(*) AS n FROM base GROUP BY ALL
        """, params)
        grupos: dict[str, list[dict]] = {}
        for li in linhas:
            grupos.setdefault(li["grupo"], []).append(li)
        lista = []
        for g, ms in grupos.items():
            n = sum(m["n"] for m in ms)
            if n < min_avaliacoes:
                continue
            cont = self._contagens(ms, n)
            lista.append({"grupo": g, "total_classificadas": n, "motivo_principal": cont[0]["motivo"], "motivos": cont})

        if ordenar_por == "grupo":
            lista.sort(key=lambda x: x["grupo"])
        elif ordenar_por == "total":
            lista.sort(key=lambda x: (-x["total_classificadas"], x["grupo"]))
        else:  # um motivo: maior percentual desse motivo primeiro
            pct = lambda x: next(m["pct"] for m in x["motivos"] if m["motivo"] == ordenar_por)  # noqa: E731
            lista.sort(key=lambda x: (-pct(x), -x["total_classificadas"], x["grupo"]))
        saida["pagina_grupos"] = paginar(len(lista), limit, offset)
        saida["grupos"] = lista[offset:offset + limit]
        return saida

    # ---------- cx_seller_profile ----------

    def vendedor_existe(self, vendedor_id: str) -> bool:
        return bool(self._linhas("SELECT 1 FROM cx_pedido_vendedor WHERE vendedor_id = ? LIMIT 1", [vendedor_id]))

    def perfil_vendedor(self, vendedor_id: str, limit: int, offset: int) -> dict:
        if not self.vendedor_existe(vendedor_id):
            raise ErroConsulta(
                f"vendedor {vendedor_id} não encontrado; confira o id ou obtenha ids válidos com "
                "cx_reason_breakdown usando agrupar_por='vendedor'"
            )
        v = [vendedor_id]
        cad = self._linhas("""
            SELECT any_value(pv.cidade_vendedor) AS cidade, any_value(pv.estado_vendedor) AS estado,
                   count(DISTINCT pv.order_id) AS pedidos, sum(pv.n_itens) AS itens, sum(pv.valor_itens) AS receita,
                   min(p.data_compra) AS primeira, max(p.data_compra) AS ultima
            FROM cx_pedido_vendedor pv JOIN cx_pedidos p USING (order_id) WHERE pv.vendedor_id = ?
        """, v)[0]
        cats = self._linhas("""
            -- só os itens do próprio vendedor (um pedido pode ter itens de outros vendedores)
            SELECT coalesce(pr.product_category_name, 'sem_categoria') AS categoria,
                   count(DISTINCT i.order_id) AS pedidos
            FROM itens i JOIN produtos pr USING (product_id)
            WHERE i.seller_id = ? GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5
        """, v)
        av = self._linhas("""
            SELECT count(*) AS n, avg(nota) AS nota, 100.0 * avg(CASE WHEN nota <= 2 THEN 1 ELSE 0 END) AS pct_baixa,
                   count(motivo) AS classificadas
            FROM cx_avaliacoes a WHERE a.order_id IN (SELECT order_id FROM cx_pedido_vendedor WHERE vendedor_id = ?)
        """, v)[0]
        mot = self._linhas("""
            SELECT motivo, count(*) AS n FROM cx_avaliacoes
            WHERE motivo IS NOT NULL AND order_id IN (SELECT order_id FROM cx_pedido_vendedor WHERE vendedor_id = ?)
            GROUP BY 1
        """, v)
        ent = self._linhas("""
            SELECT count(*) AS entregues, 100.0 * count(*) FILTER (WHERE dias_atraso > 0) / nullif(count(*), 0) AS pct,
                   avg(dias_atraso) FILTER (WHERE dias_atraso > 0) AS dias
            FROM cx_pedidos WHERE entregue AND order_id IN (SELECT order_id FROM cx_pedido_vendedor WHERE vendedor_id = ?)
        """, v)[0]
        recl = self.buscar_avaliacoes(filtros={"vendedor_id": vendedor_id, "nota_max": 2}, somente_com_comentario=True,
                                      ordenar_por="recentes", limit=limit, offset=offset)
        pl = self.plataforma
        return {
            "vendedor_id": vendedor_id, "cidade": cad["cidade"], "estado": cad["estado"],
            "pedidos": cad["pedidos"], "itens_vendidos": int(cad["itens"]), "receita_itens": _r(cad["receita"]),
            "primeira_venda": cad["primeira"], "ultima_venda": cad["ultima"], "principais_categorias": cats,
            "avaliacoes": av["n"],
            "nota_media": {"vendedor": _r(av["nota"]), "plataforma": pl["nota_media"]},
            "pct_nota_baixa": {"vendedor": _r(av["pct_baixa"], 1), "plataforma": pl["pct_nota_baixa"]},
            "classificadas": av["classificadas"], "motivos": self._contagens(mot, av["classificadas"]),
            "pct_motivos_plataforma": pl["pct_motivos"],
            "entregues": ent["entregues"],
            "pct_atrasados": {"vendedor": _r(ent["pct"], 1), "plataforma": pl["pct_atrasados"]},
            "dias_atraso_medio_quando_atrasa": {"vendedor": _r(ent["dias"], 1), "plataforma": pl["dias_atraso_medio"]},
            "pagina_reclamacoes": recl["pagina"], "reclamacoes_recentes": recl["avaliacoes"],
        }

    # ---------- cx_delivery_delay_stats ----------

    @staticmethod
    def _grupo_atraso(li: dict) -> dict:
        return {
            "grupo": li["grupo"], "entregues": li["entregues"], "atrasados": li["atrasados"],
            "pct_atrasados": _pct(li["atrasados"], li["entregues"]) or 0.0,
            "dias_atraso_medio_quando_atrasa": _r(li["dias_atraso_medio"], 1),
            "dias_ate_entrega_medio": _r(li["dias_ate_entrega_medio"], 1),
            "nota_media_atrasados": _r(li["nota_atrasados"]),
            "nota_media_no_prazo": _r(li["nota_no_prazo"]),
            "pct_reclamacao_atraso": _pct(li["n_reclamacao_atraso"] or 0, li["n_classificadas"] or 0),
        }

    def atrasos(self, *, filtros: dict, agrupar_por: str, ordenar_por: str, min_pedidos: int,
                limit: int, offset: int) -> dict:
        onde, params = _onde(filtros, FILTROS_PEDIDO)
        base_pedidos = f"""
            SELECT p.*, r.nota_pedido, coalesce(r.n_classificadas, 0) AS n_classificadas,
                   coalesce(r.n_reclamacao_atraso, 0) AS n_reclamacao_atraso
            FROM cx_pedidos p LEFT JOIN ({SQL_AVALIACOES_POR_PEDIDO}) r USING (order_id)
        """
        geral = self._linhas(f"SELECT 'geral' AS grupo, {SQL_ATRASO_AGREGADO} FROM ({base_pedidos}) p {onde}", params)[0]
        saida = {"geral": self._grupo_atraso(geral), "pedidos_nao_entregues": geral["nao_entregues"],
                 "agrupar_por": agrupar_por, "pagina_grupos": None, "grupos": []}
        if agrupar_por == "nenhum":
            return saida

        join, expr = DIMENSOES_PEDIDO[agrupar_por]
        linhas = self._linhas(f"""
            WITH base AS (SELECT DISTINCT p.*, {expr} AS grupo FROM ({base_pedidos}) p {join} {onde})
            SELECT grupo, {SQL_ATRASO_AGREGADO} FROM base GROUP BY grupo
        """, params)
        lista = [self._grupo_atraso(li) for li in linhas if li["entregues"] >= min_pedidos]
        chaves = {
            "pct_atrasados": lambda x: (-x["pct_atrasados"], -x["entregues"], x["grupo"]),
            "entregues": lambda x: (-x["entregues"], x["grupo"]),
            "dias_atraso_medio": lambda x: (-(x["dias_atraso_medio_quando_atrasa"] or 0), x["grupo"]),
            "nota_media_atrasados": lambda x: ((x["nota_media_atrasados"] if x["nota_media_atrasados"] is not None
                                                else 99), x["grupo"]),
            "grupo": lambda x: x["grupo"],
        }
        lista.sort(key=chaves[ordenar_por])
        saida["pagina_grupos"] = paginar(len(lista), limit, offset)
        saida["grupos"] = lista[offset:offset + limit]
        return saida
