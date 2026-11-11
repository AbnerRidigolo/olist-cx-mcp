"""Baixa o dataset público da Olist (Kaggle, sem token) e copia os CSVs para data/raw/."""
import shutil
from pathlib import Path

import kagglehub

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "data" / "raw"


def main() -> None:
    origem = Path(kagglehub.dataset_download("olistbr/brazilian-ecommerce"))
    DESTINO.mkdir(parents=True, exist_ok=True)
    for csv in sorted(origem.glob("*.csv")):
        shutil.copy(csv, DESTINO / csv.name)
        print(f"{csv.name:50s} {csv.stat().st_size / 2**20:7.1f} MB")


if __name__ == "__main__":
    main()
