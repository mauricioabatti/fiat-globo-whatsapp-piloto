# app.py
import os
import re
import csv
import json
import logging
import threading
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from flask import Flask, request, Response, jsonify, render_template_string, abort
from openai import OpenAI

# =========================
# Configuração e logging
# =========================
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fiat-whatsapp")

# Variáveis de ambiente
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "1234")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

if not OPENAI_API_KEY:
    log.warning("OPENAI_API_KEY não definida — chamadas à IA falharão até você configurá-la.")

client = OpenAI(api_key=OPENAI_API_KEY)

# Pastas/arquivos
DATA_DIR = "data"
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
LEADS_FILE = os.path.join(DATA_DIR, "leads.csv")
os.makedirs(DATA_DIR, exist_ok=True)

# Controle de concorrência
_lock = threading.Lock()

# =========================
# Sessões
# =========================
if os.path.exists(SESSIONS_FILE):
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            sessions = json.load(f)
    except Exception as e:
        log.error(f"Falha ao carregar {SESSIONS_FILE}: {e}")
        sessions = {}
else:
    sessions = {}

def _atomic_write(path: str, payload: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, path)

def save_sessions():
    with _lock:
        _atomic_write(SESSIONS_FILE, json.dumps(sessions, ensure_ascii=False, indent=2))

def save_lead(phone: str, message: str, resposta: str):
    header = ["timestamp", "telefone", "mensagem", "resposta"]
    row = [datetime.now().isoformat(), phone, message, resposta]
    with _lock:
        new = not os.path.exists(LEADS_FILE)
        with open(LEADS_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(header)
            w.writerow(row)

# =========================
# Helpers
# =========================
def normalize_phone(raw: str) -> str:
    """'whatsapp:+55...' -> '+55...' ; remove espaços extras."""
    raw = (raw or "").strip()
    if raw.startswith("whatsapp:"):
        raw = raw[len("whatsapp:"):]
    return raw

def system_prompt() -> str:
    return (
        "Você é um consultor automotivo da Fiat Globo Itajaí. "
        "Fale em tom humano, simpático e objetivo, SEMPRE em português do Brasil. "
        "Objetivo: informar, qualificar o lead e convidar para test drive. "
        "Se o cliente escrever 'SAIR', encerre cordialmente e remova a sessão. "
        "Responda em 2–4 frases e sempre deixe um próximo passo claro."
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
        texto = "Desculpe, estou indisponível agora. Pode tentar novamente em instantes? 🙏"

    historico.append({"role": "assistant", "content": texto})
    sessions[numero] = historico[-12:]
    save_sessions()
    return texto

def twiml(texto: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{xml_escape(texto or '')}</Message></Response>"
    )

def require_admin():
    token = request.args.get("token") or request.headers.get("X-Admin-Token")
    if token != ADMIN_TOKEN:
        abort(403, description="Acesso negado")

# =========================
# Rotas
# =========================
@app.route("/")
def home():
    return "Servidor Flask rodando! ✅"

@app.route("/healthz")
def healthz():
    leads_count = 0
    if os.path.exists(LEADS_FILE):
        try:
            with open(LEADS_FILE, "r", encoding="utf-8") as f:
                leads_count = max(0, sum(1 for _ in f) - 1)
        except Exception:
            leads_count = -1
    return jsonify({
        "ok": True,
        "model": MODEL,
        "sessions": len(sessions),
        "leads": leads_count,
        "port": os.getenv("PORT", "5000"),
    })

def _handle_incoming():
    """Handler compartilhado para /whatsapp e /webhook."""
    from_number = normalize_phone(request.form.get("From", ""))
    body = (request.form.get("Body", "") or "").strip()

    if not from_number:
        log.warning("Requisição sem From.")
        return Response(twiml(""), mimetype="application/xml")

    if body.upper() == "SAIR":
        sessions.pop(from_number, None)
        save_sessions()
        return Response(
            twiml("Você foi removido. Quando quiser voltar, é só mandar OI. 👋"),
            mimetype="application/xml",
        )

    resposta = gerar_resposta(from_number, body)
    save_lead(from_number, body, resposta)
    return Response(twiml(resposta), mimetype="application/xml")

# Alias para evitar confusão de endpoint no Twilio
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    return _handle_incoming()

@app.route("/webhook", methods=["POST"])
def webhook():
    return _handle_incoming()

@app.route("/simulate")
def simulate():
    """Teste rápido sem Twilio: /simulate?from=+5541999999999&msg=Oi"""
    frm = request.args.get("from", "whatsapp:+5500000000000")
    msg = request.args.get("msg", "Oi, quero informações do Pulse")
    with app.test_request_context("/webhook", method="POST", data={"From": frm, "Body": msg}):
        return _handle_incoming()

@app.route("/painel")
def painel():
    if not os.path.exists(LEADS_FILE):
        return "Nenhum lead ainda."
    rows = []
    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        for r in csv.reader(f):
            rows.append(r)
    header, itens = rows[0], rows[1:]
    itens.reverse()
    html = """
    <html><head><meta charset="utf-8"><title>Leads</title>
    <style>
    body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;padding:20px}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #ddd;padding:8px;text-align:left}
    th{background:#f5f5f5}
    tr:nth-child(even) td{background:#fafafa}
    </style></head><body>
    <h2>Leads Registrados</h2>
    <table><thead><tr>{% for c in header %}<th>{{c}}</th>{% endfor %}</tr></thead>
    <tbody>{% for r in itens %}<tr>{% for c in r %}<td>{{c}}</td>{% endfor %}</tr>{% endfor %}</tbody>
    </table></body></html>
    """
    return render_template_string(html, header=header, itens=itens)

@app.route("/reset", methods=["POST"])
def reset():
    require_admin()
    deleted = []
    with _lock:
        if os.path.exists(LEADS_FILE):
            os.remove(LEADS_FILE); deleted.append("leads.csv")
        if os.path.exists(SESSIONS_FILE):
            os.remove(SESSIONS_FILE); deleted.append("sessions.json")
        sessions.clear()
    return jsonify({"ok": True, "deleted": deleted})

# =========================
# Run
# =========================
if __name__ == "__main__":
    # Sanitiza PORT para evitar crash se alguém cadastrou errado no painel
    port_env = os.getenv("PORT", "5000")
    try:
        port = int(port_env)
    except Exception:
        log.error(f"PORT inválida ('{port_env}'). Usando 5000 localmente.")
        port = 5000

    app.run(host="0.0.0.0", port=port, debug=False)
