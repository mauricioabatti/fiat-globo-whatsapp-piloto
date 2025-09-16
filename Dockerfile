FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

# Usa a PORT do Railway; local = 5000
CMD ["sh","-c","gunicorn -b 0.0.0.0:${PORT:-5000} app:app"]
