from flask import Flask, request, render_template_string, Response, jsonify
import os, csv, json, datetime, re, random
from openai import OpenAI

app = Flask(__name__)

# ---------- Config ----------
DATA_DIR = "data"
LEADS_CSV = os.path.join(DATA_DIR, "leads.csv")
SESSIONS_JSON = os.path.join(DATA_DIR, "sessions.json")
KB_DIR = "kb"
os.makedirs(DATA_DIR, exist_ok=True)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "reset123")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI()  # usa OPENAI_API_KEY do ambiente

DEFAULT_SYSTEM = (
    "Você é um consultor da Fiat Globo Itajaí. Fale em PT-BR, tom humano, educado e objetivo. "
    "Objetivo: entender a necessidade (informação, fotos/especificações, agendar test drive/oficina, financeiro) "
    "e conduzir com UMA pergunta por vez. "
    "Se o cliente disser que não quer fotos, avance para agendar. "
    "Para agendar, colete modelo/unidade (se preciso), dia e horário. "
    "Peça nome e e-mail com naturalidade quando o cliente já demonstrar interesse. "
    "Não invente preços/condições; diga que pode verificar com a equipe. "
    "Se houver hesitação, ofereça alternativa (mandar material, remarcar, falar com humano). "
    "Ofereça opt-out com a palavra SAIR (LGPD)."
)

def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def load_fewshots():
    p = os.path.join(KB_DIR, "fewshots.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            # garante formato {"user": "...", "assistant": "..."}
            return [d for d in data if d.get("user") and d.get("assistant")]
    except Exception:
        return []

SYSTEM_PROMPT = read_text(os.path.join(KB_DIR, "system_prompt.txt")) or DEFAULT_SYSTEM
FEWSHOTS = load_fewshots()

# ---------- Helpers de persistência ----------
def ensure_leads_header():
    if not os.path.exists(LEADS_CSV):
        with open(LEADS_CSV, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["time", "from", "body", "note"])

def load_sessions():
    if not os.path.exists(SESSIONS_JSON):
        return {}
    try:
        with open(SESSIONS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_sessions(data: dict):
    tmp = SESSIONS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, SESSIONS_JSON)

ensure_leads_header()
sessions = load_sessions()

def append_lead(from_number: str, body: str, note: str = ""):
    with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([datetime.datetime.utcnow().isoformat(), from_number, body, note])

def get_session(phone: str) -> dict:
    s = sessions.get(phone) or {"history": [], "slots": {}}
    # mantém histórico curto p/ reduzir custo
    s["history"] = s["history"][-8:]
    return s

def set_session(phone: str, session: dict):
    sessions[phone] = session
    save_sessions(sessions)

# ---------- IA ----------
def generate_ai_reply(phone: str, user_text: str) -> str:
    s = get_session(phone)

    # coleta simples de e-mail por regex
    slots = s.get("slots", {})
    found_email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_text)
    if found_email and not slots.get("email"):
        slots["email"] = found_email.group(0)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # injeta 2-3 fewshots aleatórios (se existirem)
    if FEWSHOTS:
        examples = random.sample(FEWSHOTS, k=min(3, len(FEWSHOTS)))
        for ex in examples:
            messages.append({"role": "user", "content": ex["user"]})
            messages.append({"role": "assistant", "content": ex["assistant"]})

    # histórico curto da sessão
    for turn in s["history"]:
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["assistant"]})

    # mensagem atual
    messages.append({
        "role": "user",
        "content": (
            f"Telefone do cliente: {phone}\n"
            f"Mensagem do cliente: {user_text}\n\n"
            "Responda como humano em PT-BR e avance o objetivo (informar/agendar/financeiro). "
            "Uma pergunta por vez. Seja breve."
        )
    })

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.6,
            messages=messages
        )
        reply = (resp.choices[0].message.content or "").strip()
        if not reply:
            reply = "Perfeito! Posso te mandar fotos e especificações do modelo e já sugerir um horário. Qual data te ajuda?"
    except Exception:
        reply = "Legal! Posso te mandar fotos e especificações do modelo e sugerir um horário. Qual data te ajuda?"

    # atualiza sessão
    s["history"].append({"user": user_text, "assistant": reply})
    s["slots"] = slots
    set_session(phone, s)
    return reply

# ---------- Rotas ----------
@app.route("/")
def home():
    return "✅ WhatsApp bot com IA no ar"

@app.route("/healthz")
def healthz():
    ok = os.path.exists(LEADS_CSV) and True
    return jsonify(ok=ok, leads=os.path.exists(LEADS_CSV), sessions=os.path.exists(SESSIONS_JSON))

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = (request.values.get("From") or "").strip()
    body = (request.values.get("Body") or "").strip()

    append_lead(from_number, body)  # log bruto

    # opt-out
    if body.upper().strip() in {"SAIR", "STOP", "CANCELAR"}:
        reply = "Sem problemas! Não enviaremos mais mensagens. Se mudar de ideia, é só mandar um oi. 👋"
    else:
        reply = generate_ai_reply(from_number, body)

    twiml = f"<Response><Message>{reply}</Message></Response>"
    return Response(twiml, mimetype="application/xml")

@app.route("/painel")
def painel():
    rows = []
    if os.path.exists(LEADS_CSV):
        with open(LEADS_CSV, newline="", encoding="utf-8") as f:
            rdr = csv.DictReader(f)
            for r in rdr:
                rows.append({
                    "time": (r.get("time") or "").replace("T"," ")[:19],
                    "from": r.get("from") or "",
                    "body": r.get("body") or "",
                    "note": r.get("note") or ""
                })

    html = """
    <!doctype html><html lang="pt-BR"><head>
      <meta charset="utf-8"><title>Painel de Leads</title>
      <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    </head><body class="p-4"><div class="container">
      <h1 class="mb-4">📋 Painel de Leads</h1>
      <div class="mb-3 d-flex gap-2">
        <form action="/admin/reset" method="post" onsubmit="return confirm('Limpar leads e sessões?');">
          <input type="hidden" name="token" value="__FORM__"/>
          <button class="btn btn-danger btn-sm">Limpar Leads</button>
        </form>
      </div>
      {% if rows %}
      <table class="table table-striped table-bordered">
        <thead class="table-dark"><tr><th>Data/Hora (UTC)</th><th>Telefone</th><th>Mensagem</th><th>Obs</th></tr></thead>
        <tbody>
        {% for r in rows %}
          <tr><td class="text-nowrap">{{ r.time }}</td><td>{{ r.from }}</td><td>{{ r.body }}</td><td>{{ r.note }}</td></tr>
        {% endfor %}
        </tbody>
      </table>
      {% else %}
        <div class="alert alert-secondary">Nenhuma mensagem recebida ainda.</div>
      {% endif %}
    </div></body></html>
    """
    return render_template_string(html.replace("__FORM__", ADMIN_TOKEN), rows=rows)

@app.route("/admin/reset", methods=["POST"])
def admin_reset():
    token = request.values.get("token") or request.args.get("token","")
    if token != ADMIN_TOKEN:
        return "forbidden", 403
    # recria CSV e limpa sessões
    with open(LEADS_CSV, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["time", "from", "body", "note"])
    with open(SESSIONS_JSON, "w", encoding="utf-8") as f:
        f.write("{}")
    return "ok", 200

# Placeholder de voz (vamos ligar depois)
@app.route("/voice", methods=["POST"])
def voice_welcome():
    twiml = """
<Response>
  <Say language="pt-BR" voice="alice">Olá! Em breve o atendimento por voz com IA estará disponível.</Say>
  <Hangup/>
</Response>""".strip()
    return Response(twiml, mimetype="application/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")))
