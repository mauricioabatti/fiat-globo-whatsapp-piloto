# app.py
import os
import re
import csv
import json
import logging
import threading
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from flask import (
    Flask, request, Response, jsonify, render_template_string, abort
)
from openai import OpenAI

# =========================
# Configurações básicas
# =========================
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

# Logs amigáveis
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("fiat-globo-whatsapp")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    log.warning("OPENAI_API_KEY não definido. Configure sua variável de ambiente.")
client = OpenAI(api_key=OPENAI_API_KEY)
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Segurança básica do painel/reset
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "1234")

# Arquivos locais
DATA_DIR = "data"
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
LEADS_FILE = os.path.join(DATA_DIR, "leads.csv")
os.makedirs(DATA_DIR, exist_ok=True)

# Controle de concorrência para IO em arquivo
_lock = threading.Lock()

# =========================
# Inicialização de sessões
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

# =========================
# Helpers
# =========================
def _atomic_write(path: str, content: str):
    """Escrita atômica para evitar corrupção em queda de processo."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)

def save_sessions():
    with _lock:
        payload = json.dumps(sessions, indent=2, ensure_ascii=False)
        _atomic_write(SESSIONS_FILE, payload)

def normalize_phone(raw: str) -> str:
    """Normaliza formatos como 'whatsapp:+5541999999999' -> '+5541999999999'."""
    if not raw:
        return "desconhecido"
    raw = raw.strip()
    m = re.search(r"(\+?\d{10,15})", raw)
    return m.group(1) if m else raw

def save_lead(phone: str, message: str, resposta: str):
    header = ["timestamp", "telefone", "mensagem", "resposta"]
    row = [datetime.now().isoformat(), phone, message, resposta]
    with _lock:
        exists = os.path.exists(LEADS_FILE)
        with open(LEADS_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(header)
            w.writerow(row)

def system_prompt() -> str:
    return (
        "Você é um consultor automotivo da Fiat Globo Itajaí. "
        "Fale em tom humano, simpático e objetivo, SEMPRE em português do Brasil. "
        "Objetivo: informar, qualificar o lead e oferecer/agendar test drive. "
        "Regras:\n"
        "- Pergunte o primeiro nome, a cidade e o carro de interesse (se ainda não souber).\n"
        "- Se o cliente citar preço/condições, responda com faixas e convide para simulação sem compromisso.\n"
        "- Sugira test drive e ofereça horários. Se o cliente aceitar, peça dia/horário e telefone de contato.\n"
        "- Se o cliente escrever 'SAIR' (qualquer caixa), encerre educadamente e remova a sessão.\n"
        "- Mantenha respostas curtas (2–4 frases) e com CTA claro.\n"
        "- Nunca peça dados sensíveis (CPF completo)."
    )

def gerar_resposta(numero: str, mensagem: str) -> str:
    historico = sessions.get(numero, [])
    historico.append({"role": "user", "content": mensagem})

    messages = [
        {"role": "system", "content": system_prompt()}
    ] + historico[-8:]  # mantém só as últimas 8 trocas para contexto

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.7,
        )
        texto = resp.choices[0].message.content.strip()
    except Exception as e:
        log.exception("Erro ao chamar OpenAI")
        texto = "[Desculpe, nosso consultor virtual está indisponível no momento. Tente novamente em instantes.]"

    # Atualiza sessão (guarda no máx. 12 eventos)
    historico.append({"role": "assistant", "content": texto})
    sessions[numero] = historico[-12:]
    save_sessions()
    return texto

def make_twiml(message_text: str) -> str:
    """Gera TwiML com escape seguro para XML."""
    safe = xml_escape(message_text or "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>{safe}</Message>
</Response>"""

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
                leads_count = sum(1 for _ in f) - 1  # desconta header
                if leads_count < 0:
                    leads_count = 0
        except Exception:
            leads_count = -1
    return jsonify({
        "ok": True,
        "model": MODEL,
        "sessions": len(sessions),
        "leads": leads_count
    })

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    # Twilio envia From='whatsapp:+55...' e Body='...'
    raw_from = request.form.get("From", "desconhecido")
    from_number = normalize_phone(raw_from)
    body = (request.form.get("Body", "") or "").strip()

    # Respeita comando SAIR diretamente aqui também
    if body.upper() == "SAIR":
        sessions.pop(from_number, None)
        save_sessions()
        reply = "Tudo certo! Você foi removido da nossa lista. Quando quiser voltar, é só mandar OI. 👋"
        return Response(make_twiml(reply), mimetype="application/xml")

    resposta = gerar_resposta(from_number, body)
    save_lead(from_number, body, resposta)
    return Response(make_twiml(resposta), mimetype="application/xml")

@app.route("/painel")
def painel():
    # Painel simples para visualizar leads
    if not os.path.exists(LEADS_FILE):
        return "Nenhum lead ainda."

    rows = []
    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)

    # coloca o mais recente no topo (mantém header)
    header, itens = rows[0], rows[1:]
    itens.reverse()

    html = """
    <html>
    <head>
      <meta charset="utf-8">
      <title>Leads - Fiat Globo Itajaí</title>
      <style>
        body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; padding:20px; }
        h2 { margin-top: 0; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ddd; padding: 8px; }
        th { background: #f5f5f5; text-align: left; }
        tr:nth-child(even) td { background: #fafafa; }
        .topbar { display:flex; gap:10px; align-items:center; margin-bottom:12px; }
        .tag { background:#e8f1ff; color:#0b5ed7; padding:4px 8px; border-radius:8px; font-size:12px; }
        .muted { color:#666; font-size:12px; }
      </style>
    </head>
    <body>
      <div class="topbar">
        <h2>Leads Registrados</h2>
        <span class="tag">Modelo: {{ model }}</span>
        <span class="muted">Total de leads: {{ total }}</span>
      </div>
      <table>
        <thead>
          <tr>
            {% for c in header %}<th>{{ c }}</th>{% endfor %}
          </tr>
        </thead>
        <tbody>
          {% for r in itens %}
            <tr>{% for c in r %}<td>{{ c }}</td>{% endfor %}</tr>
          {% endfor %}
        </tbody>
      </table>
    </body>
    </html>
    """
    return render_template_string(html, header=header, itens=itens, total=len(itens), model=MODEL)

@app.route("/reset", methods=["POST"])
def reset():
    require_admin()

    deleted = []
    with _lock:
        if os.path.exists(LEADS_FILE):
            os.remove(LEADS_FILE)
            deleted.append("leads.csv")
        if os.path.exists(SESSIONS_FILE):
            os.remove(SESSIONS_FILE)
            deleted.append("sessions.json")
        # limpa memória
        sessions.clear()

    return jsonify({"ok": True, "deleted": deleted})

@app.route("/simulate")
def simulate():
    """
    GET /simulate?from=+5541999999999&msg=Quero saber preço do Pulse
    Útil para testar sem o Twilio.
    """
    frm = normalize_phone(request.args.get("from", "+5500000000000"))
    msg = request.args.get("msg", "Oi")
    if msg.upper() == "SAIR":
        sessions.pop(frm, None)
        save_sessions()
        simulated = "Tudo certo! Você foi removido da nossa lista. Quando quiser voltar, é só mandar OI. 👋"
    else:
        simulated = gerar_resposta(frm, msg)
        save_lead(frm, msg, simulated)
    return jsonify({"from": frm, "msg": msg, "resposta": simulated})

# =========================
# Execução local
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
