# olist-cx-mcp em modo Streamable HTTP.
# Estágio 1 baixa o dataset público da Olist (Kaggle, sem token), classifica e monta o DuckDB.
# Estágio 2 leva só o banco pronto, o modelo e o código; roda como usuário sem privilégios.
FROM python:3.11.18-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements-server.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements-server.txt
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps .

# venv separado com as depend�ncias do pipeline (pandas, pyarrow, kagglehub); n�o vai para a imagem final
FROM base AS dados
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY models/motivo/modelo.joblib models/motivo/modelo.joblib
COPY scripts/fase0_baixar.py scripts/
RUN python scripts/fase0_baixar.py \
 && python -m olist_cx_mcp.pipeline --raw data/raw --banco data/olist.duckdb \
      --modelo models/motivo/modelo.joblib --parquet data/processed/motivos.parquet \
 && rm -rf data/raw /root/.cache/kagglehub

FROM python:3.11.18-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" \
    OLIST_DUCKDB_PATH=/app/data/olist.duckdb MOTIVO_MODELO_PATH=/app/models/motivo/modelo.joblib
WORKDIR /app
RUN useradd --create-home --uid 10001 mcp
COPY --from=base /opt/venv /opt/venv
COPY --from=dados /app/data/olist.duckdb data/olist.duckdb
COPY models/motivo/modelo.joblib models/motivo/modelo.joblib
USER mcp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), 2).close()"
CMD ["python", "-m", "olist_cx_mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
