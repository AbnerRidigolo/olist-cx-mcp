"""Distribuição dos motivos (a partir do DuckDB) -> stdout e reports/fase0_distribuicao.json."""
import json
from pathlib import Path

import duckdb

RAIZ = Path(__file__).resolve().parents[1]

SQL_DIST = """
SELECT m.motivo, count(*) AS n,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct,
       round(avg(a.review_score), 2) AS nota_media,
       round(median(m.confianca), 3) AS confianca_mediana,
       round(100.0 * avg(CASE WHEN m.confianca < 0.5 THEN 1 ELSE 0 END), 1) AS pct_conf_abaixo_05
FROM motivos m JOIN avaliacoes a USING (review_id, order_id)
GROUP BY m.motivo ORDER BY n DESC
"""
SQL_NOTA = """
SELECT a.review_score AS nota, m.motivo, count(*) AS n
FROM motivos m JOIN avaliacoes a USING (review_id, order_id)
GROUP BY ALL ORDER BY nota, n DESC
"""


def main() -> None:
    with duckdb.connect(str(RAIZ / "data" / "olist.duckdb"), read_only=True) as con:
        dist = con.execute(SQL_DIST).df()
        nota = con.execute(SQL_NOTA).df().pivot(index="nota", columns="motivo", values="n").fillna(0).astype(int)
    print(dist.to_string(index=False))
    print("\nmotivo x nota (contagens)")
    print(nota.T.to_string())
    (RAIZ / "reports").mkdir(exist_ok=True)
    (RAIZ / "reports" / "fase0_distribuicao.json").write_text(json.dumps(
        {"total": int(dist.n.sum()), "por_motivo": dist.to_dict("records"),
         "motivo_x_nota": {str(k): v for k, v in nota.T.to_dict().items()}},
        ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
