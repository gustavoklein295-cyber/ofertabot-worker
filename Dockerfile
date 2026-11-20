FROM python:3.12-slim
WORKDIR /app
COPY requirements-server.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY bot_ofertas_ml.py painel.py ./
ENV PYTHONUNBUFFERED=1 HOST=127.0.0.1 PORT=8080 DB_FILE=/data/enviados.db
CMD ["python", "bot_ofertas_ml.py"]
