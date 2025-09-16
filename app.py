import os
import csv
import json
from flask import Flask, request, Response, jsonify, render_template_string
from openai import OpenAI
from datetime import datetime

# === Configurações ===
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "1234")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Arquivos locais
SESSIONS_FILE = "data/sessions.json"
LEADS_FILE = "data/leads.csv"
os.makedirs("data", exist_ok=True)

# Carrega sessões
if os.path.exists(SESSIONS_FILE):
    with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
        sessions = json.load(f)
else:
    sessions = {}

# === Helpers ===
def save_sessions():
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)

def save_lead(phone, message, resposta):
    exists = os.path.exists(LEADS_FILE)
    with open(LEADS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["timestamp", "telefone", "mensagem", "resposta"])
        writer.writerow([datetime.now().isoformat(), phone, message, resposta])

def gerar_resposta(numero, mensagem):
    # Pega contexto da sessão
    historico = sessions.get(numero, [])
    historico.append({"role": "user", "content": mensagem})

    # Prompt base
    messages = [
        {"role": "system", "content": "Você é um consultor automotivo da Fiat Globo Itajaí. \
        Fale em tom humano e amigável, sempre em português. \
        Seu objetivo é informar, qualificar e marcar test drives. \
        Se o cliente disser SAIR, encerre educadamente."}
    ] + historico[-6:]  # mantém só últimas 6 mensagens

    try:
        resposta = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.7,
        )
        texto = resposta.choices[0].message.content
    except Exception as e:
        texto = f"[Erro IA: {str(e)}]"

    historico.append({"role": "assistant", "content": texto})
    sessions[numero] = historico[-10:]  # mantém últimas 10
    save_sessions()
    return texto

# === Rotas ===
@app.route("/")
def home():
    return "Servidor Flask rodando!"

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.form.get("From", "desconhecido")
    body = request.form.get("Body", "")

    if body.strip().upper() == "SAIR":
        resposta = "Você foi removido da nossa lista. Até breve!"
        sessions.pop(from_number, None)
        save_sessions()
    else:
        resposta = gerar_resposta(from_number, body)
        save_lead(from_number, body, resposta)

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{resposta}</Message>
</Response>"""
    return Response(twiml, mimetype="application/xml")

@app.route("/painel")
def painel():
    if not os.path.exists(LEADS_FILE):
        return "Nenhum lead ainda."
    rows = []
    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
    html = """
    <h2>Leads Registrados</h2>
    <table border="1" cellpadding="5">
    {% for r in rows %}
      <tr>{% for c in r %}<td>{{ c }}</td>{% endfor %}</tr>
    {% endfor %}
    </table>
    """
    return render_template_string(html, rows=rows)

@app.route("/reset", methods=["POST"])
def reset():
    token = request.args.get("token")
    if token != ADMIN_TOKEN:
        return "Acesso negado", 403
    if os.path.exists(LEADS_FILE):
        os.remove(LEADS_FILE)
    if os.path.exists(SESSIONS_FILE):
        os.remove(SESSIONS_FILE)
    return "Leads e sessões limpos!"

# === Run local ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
