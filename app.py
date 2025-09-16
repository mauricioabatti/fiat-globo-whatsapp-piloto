# app.py
import os
import csv
import json
import logging
import threading
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from flask import Flask, request, Response, jsonify, abort
from openai import OpenAI

# -------------------------
# Configuração básica
# -------------------------
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("fiat-whatsapp")

DATA_DIR = "data"
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
LEADS_FILE = os.path.join(DATA_DIR, "leads.csv")
os.makedirs(DATA_DIR, exist_ok=True)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "1234")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    log.warning("OPENAI_API_KEY não definida — o /webhook vai falhar ao chamar a IA.")
client = OpenAI(api_key=OPENAI_API_KEY)

_lock = threading.Lock()

# Carrega sessões
if os.path.exists(SESSIONS_FILE):
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            sessions = json.load(f)
    except Exception as e:
        log.error(f"Falha ao ler {SESSIONS_FILE}: {e}")
        sessions = {}
else:
    sessions = {}

# -------------------------
# Helpers
# -------------------------
def save_sessions():
    with _lock:
        tmp = SESSIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SESSIONS_FILE)

def save_lead(phone, message, resposta):
    header = ["timestamp", "telefone", "mensagem", "resposta"]
    row = [datetime.now().isoformat(), phone, message, resposta]
    with _lock:
        new = not os.path.exists(LEADS_FILE)
        with open(LEADS_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(header)
            w.writerow(row)

def normalize_phone(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("whatsapp:"):
        raw = raw[len("whatsapp:"):]
    return raw

def system_prompt():
    return (
        "Você é um consultor automotivo da Fiat Globo Itajaí. "
        "Fale em tom humano e amigável, SEMPRE em português do Brasil. "
        "Objetivo: informar, qualificar e agendar test drives. "
        "Se o cliente escrever 'SAIR', encerre cordialmente e remova a sessão. "
        "Mantenha respostas curtas (2-4 frases) e inclua um convite claro para próximo passo."
    )

def gerar_resposta(numero: str, mensagem: str) -> str:
    historico = sessions.get(numero, [])
    historico.append({"role": "user", "content": mensagem})

    messages = [{"role": "system", "content": system_prompt()}] + historico[-8:]
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.7,
        )
        texto = (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.exception("Erro ao chamar OpenAI")
        texto = "Desculpe, estou indisponível agora. Pode tentar novamente em instantes?"

    historico.append({"role": "assistant", "content": texto})
    sessions[numero] = historico[-12:]
    save_sessions()
    return texto

def twiml(texto: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{xml_escape(texto or "")}</Message></Response>'

def require_admin():
    token = request.args.get("token") or request.headers.get("X-Admin-Token")
    if token != ADMIN_TOKEN:
        abort(403, description="Acesso negado")

# -------------------------
# Rotas
# -------------------------
@app.route("/")
def home():
    return "OK"

@app.route("/healthz")
def healthz():
    leads_count = 0
    if os.path.exists(LEADS_FILE):
        try:
            with open(LEADS_FILE, "r", encoding="utf-8") as f:
                leads_count = max(0, sum(1 for _ in f) - 1)
        except Exception:
            leads_count = -1
    return jsonify({"ok": True, "model": MODEL, "sessions": len(sessions), "leads": leads_count})

@app.route("/webhook", methods=["POST"])
def webhook():
    from_number = normalize_phone(request.form.get("From", ""))
    body = (request.form.get("Body", "") or "").strip()
    if not from_number:
        log.warning("Webhook sem From")
        return Response(twiml(""), mimetype="application/xml")

    if body.upper() == "SAIR":
        sessions.pop(from_number, None)
        save_sessions()
        return Response(twiml("Você foi removido. Quando quiser voltar, é só mandar OI. 👋"), mimetype="application/xml")

    resposta = gerar_resposta(from_number, body)
    save_lead(from_number, body, resposta)
    return Response(twiml(resposta), mimetype="application/xml")

@app.route("/simulate")
def simulate():
    frm = request.args.get("from", "whatsapp:+5500000000000")
    msg = request.args.get("msg", "Oi, quero informações do Pulse")
    with app.test_request_context("/webhook", method="POST", data={"From": frm, "Body": msg}):
        return webhook()

@app.route("/admin/leads")
def admin_leads():
    require_admin()
    if not os.path.exists(LEADS_FILE):
        return "Nenhum lead ainda."
    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/plain")

# -------------------------
# Main
# -------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
