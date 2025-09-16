# Imagem base
FROM python:3.11-slim

# Logs sem buffering e pip sem cache
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Diretório de trabalho
WORKDIR /app

# Instala dependências primeiro (melhor aproveitamento de cache)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copia o restante do projeto (app.py, routes.py, catalog.py, calendar_helpers.py, wsgi.py, etc.)
COPY . /app

# Comando de start (Gunicorn ouvindo na porta do Railway)
# Obs.: shell form permite expandir ${PORT}; JSON form não faria a expansão.
CMD gunicorn wsgi:app -b 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 120
