# app.py
import os, re, csv, json, logging, threading, base64
from datetime import datetime, timedelta, date, time
from xml.sax.saxutils import escape as xml_escape
from flask import Flask, request, Response, jsonify, render_template_string, abort
from openai import OpenAI

# Google Calendar
from googleapiclient.discovery import build
from google.oauth2 import service_account

# Twilio (opcional p/ lembrete proativo)
from twilio.rest import Client as TwilioClient

# =========================
# Config & logging
# =========================
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fiat-whatsapp")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "1234")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Google Calendar
GCAL_CAL_ID = os.getenv("GCAL_CALENDAR_ID")          # ex.: xxxxx@group.calendar.google.com
SA_B64       = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")  # conteúdo do JSON em base64
TZ           = os.getenv("TZ", "America/Sao_Paulo")

# Twilio (opcional p/ lembretes)
TW_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TW_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TW_FROM  = os.getenv("TWILIO_WHATSAPP_FROM")  # ex.: whatsapp:+1415xxxxxxx

if not OPENAI_API_KEY:
    log.warning("OPENAI_API_KEY não definida — IA será fallback indisponível se precisar.")
client = OpenAI(api_key=OPENAI_API_KEY)

DATA_DIR      = "data"
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
LEADS_FILE    = os.path.join(DATA_DIR, "leads.csv")
APPT_FILE     = os.path.join(DATA_DIR, "agendamentos.csv")  # log local dos agendamentos
OFFERS_PATH   = os.path.join(DATA_DIR, "ofertas.json")      # catálogo opcional
os.makedirs(DATA_DIR, exist_ok=True)
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
            if new: w.writerow(header)
            w.writerow(row)

# =========================
# Catálogo (opcional - RAG simples)
# =========================
def load_offers():
    if not os.path.exists(OFFERS_PATH):
        return []
    try:
        with open(OFFERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        log.error(f"Erro lendo {OFFERS_PATH}: {e}")
        return []

OFERTAS = load_offers()

def fmt_brl(valor) -> str:
    if valor is None: return "indisponível"
    s = f"{float(valor):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")

def tokenize(text: str):
    return re.findall(r"[a-z0-9\.]+", (text or "").lower().replace(",", "."))

def score_offer(q_tokens, offer):
    campos = " ".join([
        offer.get("modelo",""), offer.get("versao",""),
        offer.get("motor",""), offer.get("cambio",""),
        " ".join(offer.get("tags",[])), " ".join(offer.get("publico_alvo",[])),
        " ".join(offer.get("condicoes",[]))
    ]).lower()
    return sum(1 for t in q_tokens if t in campos)

def buscar_oferta(query: str):
    if not OFERTAS: return None
    q = tokenize(query)
    if not q: return None
    best = max(OFERTAS, key=lambda o: score_offer(q, o))
    return best if score_offer(q, best) > 0 else None

def montar_texto_oferta(o):
    preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
    preco_label = "por" if o.get("preco_por") else ("a partir de" if o.get("preco_a_partir") else "de")
    linhas = [f"{o.get('modelo','')} {o.get('versao','')}".strip(), f"Preço {preco_label}: {fmt_brl(preco)}"]
    extras = []
    if o.get("motor"): extras.append(f"Motor {o['motor']}")
    if o.get("cambio"): extras.append(f"Câmbio {o['cambio']}")
    if o.get("combustivel"): extras.append(o["combustivel"])
    if extras: linhas.append(", ".join(extras))
    if o.get("condicoes"): linhas.append("Condições: " + "; ".join(o["condicoes"]))
    if o.get("publico_alvo"): linhas.append("Público-alvo: " + ", ".join(o["publico_alvo"]))
    if o.get("link_oferta"): linhas.append(f"Oferta: {o['link_oferta']}")
    if o.get("link_modelo"): linhas.append(f"Detalhes: {o['link_modelo']}")
    linhas.append("Quer consultar cores, disponibilidade e agendar um test drive?")
    return "\n".join(linhas)

def tentar_responder_com_catalogo(mensagem: str):
    if any(k in mensagem.lower() for k in ["oferta", "ofertas", "promo", "promoção", "promocao", "lista", "listar"]):
        if not OFERTAS: return None
        # mostra até 3 destaques mais baratos
        destaques = sorted(OFERTAS, key=lambda o: (o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de") or 9e9))[:3]
        cards = [montar_texto_oferta(o) for o in destaques]
        return "Algumas ofertas em destaque:\n\n" + "\n\n---\n\n".join(cards)
    o = buscar_oferta(mensagem)
    return montar_texto_oferta(o) if o else None

# =========================
# Google Calendar helpers
# =========================
def build_gcal():
    if not (SA_B64 and GCAL_CAL_ID):
        raise RuntimeError("Faltam GOOGLE_SERVICE_ACCOUNT_B64 ou GCAL_CALENDAR_ID.")
    try:
        creds_json = base64.b64decode(SA_B64).decode("utf-8")
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/calendar"])
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return svc
    except Exception as e:
        log.exception("Erro ao inicializar Google Calendar")
        raise

def to_rfc3339(dt: datetime) -> str:
    # garante timezone TZ
    # Google aceita 'YYYY-MM-DDTHH:MM:SS-03:00'; aqui enviamos como 'Z' se horária local não for crucial
    # Melhor: enviar 'timeZone' separadamente no payload do evento
    return dt.isoformat()

def round_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)

def business_hours_for(d: date):
    # janelas de 09:00 a 18:00 em TZ
    start = datetime.combine(d, time(9, 0))
    end   = datetime.combine(d, time(18, 0))
    return start, end

def freebusy(svc, d: date):
    start, end = business_hours_for(d)
    body = {
        "timeMin": start.isoformat() + ":00",
        "timeMax": end.isoformat() + ":00",
        "timeZone": TZ,
        "items": [{"id": GCAL_CAL_ID}]
    }
    fb = svc.freebusy().query(body=body).execute()
    busy = fb["calendars"][GCAL_CAL_ID].get("busy", [])
    # busy: list of {"start": "...", "end": "..."}
    return [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in busy]

def is_slot_available(svc, start_dt: datetime) -> bool:
    start_dt = round_to_hour(start_dt)
    end_dt = start_dt + timedelta(hours=1)
    # consulta freebusy do dia todo e verifica overlap
    for s, e in freebusy(svc, start_dt.date()):
        if (s < end_dt) and (start_dt < e):
            return False
    # fora horário comercial?
    bh_start, bh_end = business_hours_for(start_dt.date())
    if not (bh_start <= start_dt < bh_end):
        return False
    return True

def create_event(svc, *, tipo, nome, carro, cidade, telefone, start_dt: datetime):
    start_dt = round_to_hour(start_dt)
    end_dt = start_dt + timedelta(hours=1)
    summary = f"{'Test Drive' if 'test' in tipo else 'Visita'}: {nome} - {carro}"
    description = f"Cliente: {nome}\nTelefone: {telefone}\nCidade: {cidade}\nTipo: {tipo}\nFonte: WhatsApp"
    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TZ},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": TZ},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]}
    }
    created = svc.events().insert(calendarId=GCAL_CAL_ID, body=event).execute()
    return created.get("id"), start_dt

# log local de agendamentos (para lembretes D-1)
def save_appointment_log(row: dict):
    header = ["timestamp_log", "telefone", "tipo", "nome", "carro", "cidade", "start_iso", "event_id"]
    with _lock:
        new = not os.path.exists(APPT_FILE)
        with open(APPT_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(header)
            w.writerow([
                datetime.now().isoformat(),
                row.get("telefone",""), row.get("tipo",""), row.get("nome",""), row.get("carro",""),
                row.get("cidade",""), row.get("start_iso",""), row.get("event_id","")
            ])

# =========================
# Twilio (opcional) - lembrete proativo D-1
# =========================
def send_whatsapp(to_phone_e164: str, body: str):
    if not (TW_SID and TW_TOKEN and TW_FROM):
        log.info("Twilio não configurado; pular envio de WhatsApp.")
        return False
    try:
        tw = TwilioClient(TW_SID, TW_TOKEN)
        msg = tw.messages.create(
            from_=TW_FROM,
            to=f"whatsapp:{to_phone_e164}" if not to_phone_e164.startswith("whatsapp:") else to_phone_e164,
            body=body
        )
        log.info(f"Lembrete enviado: {msg.sid}")
        return True
    except Exception:
        log.exception("Falha ao enviar WhatsApp via Twilio")
        return False

# =========================
# Agendamento (FSM)
# =========================
appointments_state = {}  # { phone: {"step": str, "data": {...}} }

def parse_datetime_br(texto: str):
    t = (texto or "").strip().lower().replace("h", ":")
    for fmt in ["%d/%m/%Y %H:%M", "%d/%m/%y %H:%M"]:
        try: return datetime.strptime(t, fmt)
        except Exception: pass
    return None

def wants_appointment(msg: str) -> bool:
    s = (msg or "").lower()
    gatilhos = ["agendar", "agenda", "marcar", "test drive", "testdrive", "visita", "conhecer o carro"]
    return any(g in s for g in gatilhos)

def start_flow(phone: str):
    appointments_state[phone] = {"step": "tipo", "data": {"telefone": phone}}
    return ("Perfeito! Vamos agendar.\n"
            "Você prefere **visita ao showroom** ou **test drive**?\n"
            "Responda: *visita* ou *test drive*.")

def step_flow(phone: str, msg: str):
    st = appointments_state.get(phone, {"step": None, "data": {"telefone": phone}})
    step = st["step"]; data = st["data"]; s = (msg or "").strip()

    if s.lower() in ["cancelar", "cancel", "parar", "sair"]:
        appointments_state.pop(phone, None)
        return "Agendamento cancelado. Se quiser retomar depois, é só dizer *agendar*."

    if step == "tipo":
        if s.lower() in ["visita", "visitar", "showroom"]:
            data["tipo"] = "visita"
        elif "test" in s.lower():
            data["tipo"] = "test drive"
        else:
            return "Por favor, informe o tipo: *visita* ou *test drive*."
        st["step"] = "nome"; return "Seu primeiro nome, por favor?"

    if step == "nome":
        if len(s) < 2: return "Pode me dizer seu primeiro nome?"
        data["nome"] = s; st["step"] = "carro"
        return "Qual **modelo** você quer ver/dirigir? (ex.: *Pulse Drive 1.3* ou *Toro Ranch*)"

    if step == "carro":
        if len(s) < 2: return "Me diga o **modelo** (ex.: *Pulse Drive 1.3*)."
        data["carro"] = s; st["step"] = "cidade"
        return "Sua **cidade**?"

    if step == "cidade":
        if len(s) < 2: return "Qual é sua **cidade**?"
        data["cidade"] = s; st["step"] = "datahora"
        return ("Qual **data e hora** prefere?\n"
                "Formato: *dd/mm/aaaa hh:mm* (ex.: 21/09/2025 15:30)\n"
                "Dica: trabalhamos de 09:00 às 18:00.")

    if step == "datahora":
        dt = parse_datetime_br(s)
        if not dt: return "Não reconheci a data/hora. Informe no formato *dd/mm/aaaa hh:mm*."
        dt = dt.replace(minute=0, second=0, microsecond=0)  # 1h cravada
        try:
            svc = build_gcal()
            if not is_slot_available(svc, dt):
                return ("Esse horário não está disponível. "
                        "Envie outro horário (em blocos de 1h, ex.: 10:00, 11:00, 14:00). "
                        "Se quiser, diga *slots 21/09/2025* para ver horários livres do dia.")
        except Exception as e:
            log.exception("Erro verificando disponibilidade no Google Calendar")
            return "Tive um problema ao checar disponibilidade agora. Pode me enviar outro horário?"

        data["start_iso"] = dt.isoformat()
        st["step"] = "confirmar"
        hum = dt.strftime("%d/%m/%Y %H:%M")
        return (f"Confirmando:\n- Tipo: *{data['tipo']}*\n- Nome: *{data['nome']}*\n"
                f"- Carro: *{data['carro']}*\n- Cidade: *{data['cidade']}*\n- Data/Hora: *{hum}*\n\n"
                "Está correto? Responda *confirmar* ou *cancelar*.")

    if step == "confirmar":
        if s.lower() in ["confirmar", "confirmado", "sim"]:
            try:
                svc = build_gcal()
                start_dt = datetime.fromisoformat(data["start_iso"])
                # revalida o slot (race condition)
                if not is_slot_available(svc, start_dt):
                    appointments_state.pop(phone, None)
                    return "Esse horário acabou de ficar indisponível. Vamos escolher outro?"
                event_id, start_dt = create_event(
                    svc,
                    tipo=data["tipo"], nome=data["nome"], carro=data["carro"],
                    cidade=data["cidade"], telefone=phone, start_dt=start_dt
                )
                data["event_id"] = event_id
                save_appointment_log({
                    "telefone": phone, "tipo": data["tipo"], "nome": data["nome"],
                    "carro": data["carro"], "cidade": data["cidade"],
                    "start_iso": start_dt.isoformat(), "event_id": event_id
                })
                appointments_state.pop(phone, None)
                return ("Agendamento **confirmado** no calendário! ✅\n"
                        "Obrigado. No dia anterior, te envio uma confirmação por aqui.")
            except Exception:
                log.exception("Falha ao criar evento no Google Calendar")
                appointments_state.pop(phone, None)
                return "Não consegui concluir no calendário agora. Podemos tentar outro horário?"
        elif s.lower() in ["cancelar", "não", "nao"]:
            appointments_state.pop(phone, None)
            return "Sem problemas, cancelei o agendamento. Posso ajudar em algo mais?"
        else:
            return "Por favor, responda *confirmar* ou *cancelar*."

    # fallback (se alguém disser 'agendar' sem contexto)
    return start_flow(phone)

# =========================
# IA (fallback)
# =========================
def system_prompt() -> str:
    return (
        "Você é um consultor automotivo da Fiat Globo Itajaí. "
        "Fale em tom humano e objetivo (pt-BR). "
        "Use catálogo interno se possível; nunca invente preços. "
        "Convide para test drive. Se o cliente escrever 'SAIR', encerre e remova a sessão. "
        "Responda em 2–4 frases."
    )

def gerar_resposta(numero: str, mensagem: str) -> str:
    historico = sessions.get(numero, [])
    historico.append({"role": "user", "content": mensagem})
    messages = [{"role": "system", "content": system_prompt()}] + historico[-8:]
    try:
        r = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.7)
        texto = (r.choices[0].message.content or "").strip()
    except Exception:
        log.exception("Erro ao chamar OpenAI")
        texto = "Desculpe, estou indisponível agora. Pode tentar novamente em instantes? 🙏"
    historico.append({"role": "assistant", "content": texto})
    sessions[numero] = historico[-12:]; save_sessions()
    return texto

# =========================
# Utils HTTP
# =========================
def twiml(texto: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Message>' + xml_escape(texto or "") + '</Message></Response>'

def require_admin():
    token = request.args.get("token") or request.headers.get("X-Admin-Token")
    if token != ADMIN_TOKEN: abort(403, description="Acesso negado")

def normalize_phone(raw: str) -> str:
    raw = (raw or "").strip()
    return raw[len("whatsapp:"):] if raw.startswith("whatsapp:") else raw

# =========================
# Rotas
# =========================
@app.route("/")
def home(): return "Servidor Flask rodando! ✅"

@app.route("/healthz")
def healthz():
    leads_count = 0
    if os.path.exists(LEADS_FILE):
        try:
            with open(LEADS_FILE, "r", encoding="utf-8") as f:
                leads_count = max(0, sum(1 for _ in f) - 1)
        except Exception: leads_count = -1
    return jsonify({"ok": True, "model": MODEL, "sessions": len(sessions), "leads": leads_count, "port": os.getenv("PORT", "5000")})

# consulta slots livres (GET /slots?date=YYYY-MM-DD)
@app.route("/slots")
def slots():
    d_str = request.args.get("date")
    if not d_str: return jsonify({"error": "Passe ?date=YYYY-MM-DD"}), 400
    try:
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        svc = build_gcal()
        busy = freebusy(svc, d)
        bh_start, bh_end = business_hours_for(d)
        # constroi grade horária por hora
        slots = []
        cur = bh_start
        while cur < bh_end:
            free = True
            for s, e in busy:
                if (s < cur + timedelta(hours=1)) and (cur < e):
                    free = False; break
            if free: slots.append(cur.strftime("%H:%M"))
            cur += timedelta(hours=1)
        return jsonify({"date": d_str, "timezone": TZ, "slots": slots})
    except Exception:
        log.exception("Erro ao consultar slots")
        return jsonify({"error": "Falha ao consultar disponibilidade"}), 500

# cron diário para enviar lembretes D-1 (configure um Railway Cron para chamar 1x/dia, ex.: 09:00 BRT)
@app.route("/cron/reminders", methods=["POST", "GET"])
def cron_reminders():
    # lê logs locais e envia lembrete para agendamentos do dia seguinte
    if not os.path.exists(APPT_FILE): return jsonify({"ok": True, "sent": 0, "msg": "sem agendamentos"})
    alvo = (datetime.now() + timedelta(days=1)).date()
    enviados = 0
    with open(APPT_FILE, "r", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            try:
                start = datetime.fromisoformat(row["start_iso"])
                if start.date() == alvo:
                    nome = row.get("nome","").split()[0] or "cliente"
                    texto = (f"Olá {nome}! Só confirmando seu agendamento na Fiat Globo amanhã às "
                             f"{start.strftime('%H:%M')} para {row.get('tipo','visita')}: {row.get('carro','carro')}.\n"
                             "Se precisar remarcar, me avise por aqui. Até breve! 🚗✨")
                    phone = row.get("telefone","")
                    if send_whatsapp(phone, texto): enviados += 1
            except Exception:
                log.exception("Erro ao processar lembrete")
    return jsonify({"ok": True, "sent": enviados})

def _handle_incoming():
    from_number = normalize_phone(request.form.get("From", ""))
    body = (request.form.get("Body", "") or "").strip()

    if not from_number:
        log.warning("Requisição sem From."); return Response(twiml(""), mimetype="application/xml")

    if body.upper() == "SAIR":
        sessions.pop(from_number, None); save_sessions()
        appointments_state.pop(from_number, None)
        return Response(twiml("Você foi removido. Quando quiser voltar, é só mandar OI. 👋"), mimetype="application/xml")

    # fluxo de agendamento (prioritário)
    if wants_appointment(body) or from_number in appointments_state:
        resp = step_flow(from_number, body) if from_number in appointments_state else start_flow(from_number)
        save_lead(from_number, body, resp)
        return Response(twiml(resp), mimetype="application/xml")

    # catálogo (opcional)
    # cat = tentar_responder_com_catalogo(body)
    # if cat:
    #     save_lead(from_number, body, cat)
    #     return Response(twiml(cat), mimetype="application/xml")

    # IA fallback
    resp = gerar_resposta(from_number, body)
    save_lead(from_number, body, resp)
    return Response(twiml(resp), mimetype="application/xml")

@app.route("/whatsapp", methods=["POST"])
def whatsapp(): return _handle_incoming()

@app.route("/webhook", methods=["POST"])
def webhook():  return _handle_incoming()

@app.route("/simulate")
def simulate():
    frm = request.args.get("from", "whatsapp:+5500000000000")
    msg = request.args.get("msg", "Quero agendar test drive do Pulse")
    with app.test_request_context("/webhook", method="POST", data={"From": frm, "Body": msg}):
        return _handle_incoming()

@app.route("/painel")
def painel():
    if not os.path.exists(LEADS_FILE): return "Nenhum lead ainda."
    rows = []
    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        for r in csv.reader(f): rows.append(r)
    header, itens = rows[0], rows[1:][::-1]
    html = """
    <html><head><meta charset="utf-8"><title>Leads</title>
    <style>
    body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;padding:20px}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #ddd;padding:8px;text-align:left}
    th{background:#f5f5f5} tr:nth-child(even) td{background:#fafafa}
    </style></head><body>
    <h2>Leads Registrados</h2>
    <table><thead><tr>{% for c in header %}<th>{{c}}</th>{% endfor %}</tr></thead>
    <tbody>{% for r in itens %}<tr>{% for c in r %}<td>{{c}}</td>{% endfor %}</tr>{% endfor %}</tbody>
    </table></body></html>
    """
    return render_template_string(html, header=header, itens=itens)

@app.route("/agenda")
def agenda():
    token = request.args.get("token")
    if token != ADMIN_TOKEN: return "Acesso negado", 403
    if not os.path.exists(APPT_FILE): return "Nenhum agendamento ainda."
    rows = []
    with open(APPT_FILE, "r", encoding="utf-8") as f:
        for r in csv.reader(f): rows.append(r)
    header, itens = rows[0], rows[1:][::-1]
    html = """
    <html><head><meta charset="utf-8"><title>Agenda</title>
    <style>
    body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;padding:20px}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #ddd;padding:8px;text-align:left}
    th{background:#f5f5f5} tr:nth-child(even) td{background:#fafafa}
    </style></head><body>
    <h2>Agendamentos</h2>
    <table><thead><tr>{% for c in header %}<th>{{c}}</th>{% endfor %}</tr></thead>
    <tbody>{% for r in itens %}<tr>{% for c in r %}<td>{{c}}</td>{% endfor %}</tr>{% endfor %}</tbody>
    </table></body></html>
    """
    return render_template_string(html, header=header, itens=itens)

@app.route("/reset", methods=["POST"])
def reset():
    token = request.args.get("token")
    if token != ADMIN_TOKEN: return "Acesso negado", 403
    deleted=[]
    with _lock:
        for p in [LEADS_FILE, SESSIONS_FILE, APPT_FILE]:
            if os.path.exists(p): os.remove(p); deleted.append(os.path.basename(p))
        sessions.clear()
    return jsonify({"ok": True, "deleted": deleted})

# =========================
# Run
# =========================
if __name__ == "__main__":
    port_env = os.getenv("PORT", "5000")
    try:    port = int(port_env)
    except: port, _ = 5000, log.error(f"PORT inválida ('{port_env}'). Usando 5000 localmente.")
    app.run(host="0.0.0.0", port=port, debug=False)
