from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "Servidor Flask rodando!"

@app.route("/healthz")
def healthz():
    return {"status": "ok"}
