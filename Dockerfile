# Hermes AIH -> Bitrix24  (NAO TESTADO neste ambiente: valide o build antes de usar em producao)
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    LEDGER_PATH=/data/ledger.sqlite3

# o ledger (SQLite) precisa de um volume gravavel: docker run -v hermes-aih-data:/data ...
RUN useradd --system --uid 10001 hermes && mkdir /data && chown hermes /data
USER hermes
VOLUME /data
EXPOSE 8080

# defina BITRIX_WEBHOOK_URL, HERMES_API_KEY e (opcional) OL_DIALOG_IDS por variaveis de ambiente
CMD ["python", "-m", "aih_bitrix", "serve", "--host", "0.0.0.0", "--port", "8080", "--with-poller"]
