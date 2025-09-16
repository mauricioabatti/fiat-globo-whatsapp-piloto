# Imagem base
FROM python:3.11-slim

# Evita buffering de logs no Railway
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Diretório de trabalho
WORKDIR /app

# Copia e instala dependências primeiro (cache mais eficiente)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do projeto
COPY . /app

# Comando de start (Gunicorn ouvindo na porta do Railway)
CMD ["gunicorn", "-b", "0.0.0.0:${PORT:-5000}", "app:app"]
