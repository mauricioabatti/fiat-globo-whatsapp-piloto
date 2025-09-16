# routes.py
import os, csv, json, logging, threading, random, re
from datetime import datetime, timedelta
from xml.sax.saxutils import escape as xml_escape

from flask import Blueprint, current_app, request, Response, jsonify, render_template_string, abort
from twilio.rest import Client as TwilioClient

from catalog import tentar_responder_com_catalogo
from calendar_helpers import (
    build_gcal, is_slot_available, create_event, freebusy, business_hours_for
)

bp = Blueprint("routes", __name__)
log = logging.getLogger("fiat-whatsapp")
_lock = threading.Lock()

# =========================
# Sessões e leads (arquivos)
# =========================
def _atomic_write(path: str, payload: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, path)

def load_sessions_from_file(path: str):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Falha ao carregar {path}: {e}")
    return {}

def save_sessions(sessions_dict):
    path = current_app.config["SESSIONS_FILE"]
    with _lock:
        _atomic_write(path, json.dumps(sessions_dict, ensure_ascii=False, indent=2))

def save_lead(phone: str, message: str, resposta: str):
    path = current_app.config["LEADS_FILE"]
    header = ["timestamp", "telefone", "mensagem", "resposta"]
    row = [datetime.now().isoformat(), phone, message, resposta]
    with _lock:
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(header)
            w.writerow(row)

sessions = {}
@bp.record_once
def _load_state(setup_state):
    global sessions
    app = setup_state.app
    sessions = load_sessions_from_file(app.config["SESSIONS_FILE"])

# =========================
# Twilio helpers (envio via API)
# =========================
def _twilio_client():
    sid   = current_app.config.get("TWILIO_ACCOUNT_SID")
    token = current_app.config.get("TWILIO_AUTH_TOKEN")
    if not (sid and token): return None
    try: return TwilioClient(sid, token)
    except Exception:
        log.exception("Falha ao criar cliente Twilio"); return None

def send_via_twilio_api(to_phone_e164: str, body: str) -> bool:
    if not current_app.config.get("TWILIO_WHATSAPP_FROM"):
        return False
    client = _twilio_client()
    if not client: return False
    try:
        to_fmt = f"whatsapp:{to_phone_e164}" if not str(to_phone_e164).startswith("whatsapp:") else to_phone_e164
        msg = client.messages.create(
            from_=current_app.config["TWILIO_WHATSAPP_FROM"],
            to=to_fmt,
            body=body
        )
        log.info(f"Twilio API enviado: sid={msg.sid}")
        return True
    except Exception:
        log.exception("Falha ao enviar WhatsApp via Twilio API")
        return False

# =========================
# Saudação humana dinâmica (Felipe Fortes, casual)
# =========================
_GREET_CACHE = {}  # {phone: datetime}

def _now_hour(): return datetime.now(current_app.config["TZINFO"]).hour
def _part_of_day():
    h = _now_hour()
    if h < 12: return "Bom dia"
    if h < 18: return "Boa tarde"
    return "Boa noite"

def _mirror_salute(user_text: str) -> str | None:
    s = (user_text or "").strip().lower()
    if "boa noite" in s:  return "Boa noite"
    if "boa tarde" in s:  return "Boa tarde"
    if "bom dia"   in s:  return "Bom dia"
    if re.fullmatch(r"(oi|ol[aá]|salve|e[ai]?)\b.*", s): return _part_of_day()
    return None

def _vehicle_intent(s: str) -> bool:
    s = (s or "").lower()
    kws = [
        "oferta", "ofertas", "promo", "promoção", "promocao",
        "preço", "preco", "a partir", "por", "link",
        "agendar", "agenda", "test", "test drive", "modelo",
        "dispon", "estoque", "cores",
        "pulse", "toro", "strada", "mobi", "argo", "fastback", "cronos", "fiorino", "ducato"
    ]
    return any(k in s for k in kws)

def should_greet(phone: str, minutes: int = 15) -> bool:
    last = _GREET_CACHE.get(phone)
    if not last: return True
    return datetime.now() - last > timedelta(minutes=minutes)

def mark_greeted(phone: str) -> None:
    _GREET_CACHE[phone] = datetime.now()

def is_greeting(texto: str) -> bool:
    s = (texto or "").strip().lower()
    if _vehicle_intent(s): return False
    if len(s) > 25: return False
    gatilhos = ["oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "salve", "eai", "e aí", "boa"]
    return any(s == k or s.startswith(k) for k in gatilhos)

def _greet_templates(base: str, nome: str, loja: str):
    # Variações curtas, 1 pergunta no final, tom leve
    return [
        f"{base}! Aqui é o {nome}, da {loja}. Tem algum modelo em mente ou prefere ver ofertas?",
        f"{base}! {nome} falando, da {loja}. Quer falar de um modelo específico ou te mando sugestões rápidas?",
        f"{base}! Sou o {nome}, da {loja}. Tá buscando algo pra cidade, estrada ou família?",
        f"{base}! {nome} por aqui ( {loja} ). Posso te mostrar 3 opções populares ou você já tem um preferido?",
        f"{base}! {nome} – {loja}. Te ajudo com um carro específico ou já mando um top 3 pra começar?",
    ]

def _fallback_greeting(user_text: str) -> str:
    base = _mirror_salute(user_text) or _part_of_day()
    nome = current_app.config.get("CONSULTOR_NAME", "Felipe Fortes")
    loja = current_app.config.get("DEALERSHIP_NAME", "Fiat Globo Itajaí")
    frases = _greet_templates(base, nome, loja)
    return random.choice(frases)

def human_greeting(user_text: str) -> str:
    client = current_app.config.get("OPENAI_CLIENT")
    model  = current_app.config.get("OPENAI_MODEL")
    nome   = current_app.config.get("CONSULTOR_NAME", "Felipe Fortes")
    loja   = current_app.config.get("DEALERSHIP_NAME", "Fiat Globo Itajaí")
    try:
        if client and model:
            system = (
                f"Você é {nome}, consultor da {loja}. Gere uma saudação casual para WhatsApp (pt-BR), "
                "espelhando a saudação do cliente quando existir (ex.: 'Bom dia!'). "
                "Use 1 frase curta (6–16 palavras), sem emojis e com UMA pergunta simples (modelo ou ofertas)."
            )
            user = f"Mensagem do usuário: {user_text!r}. Gere a saudação."
            r = client.chat.completions.create(
                model=model, temperature=0.7,
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                timeout=5
            )
            text = (r.choices[0].message.content or "").strip()
            if 5 <= len(text.split()) <= 18:
                return text
    except Exception:
        pass
    return _fallback_greeting(user_text)

# =========================
# IA (prompt humano)
# =========================
def system_prompt() -> str:
    nome = current_app.config.get("CONSULTOR_NAME", "Felipe Fortes")
    loja = current_app.config.get("DEALERSHIP_NAME", "Fiat Globo Itajaí")
    return (
        f"Você é {nome}, consultor da {loja}, atendendo no WhatsApp. "
        "Responda em tom humano, casual e curto (1–3 frases). "
        "Priorize a intenção: se pedir link, envie só o link; se pedir um modelo específico, traga 1 resumo curto + link. "
        "Faça no máximo UMA pergunta por mensagem para avançar. "
        "Evite jargões e fichas técnicas longas. Não repita bordões. "
        "Convide para test drive quando fizer sentido. Nunca invente preços."
    )

def gerar_resposta(numero: str, mensagem: str) -> str:
    global sessions
    historico = sessions.get(numero, [])
    historico.append({"role": "user", "content": mensagem})
    messages = [{"role": "system", "content": system_prompt()}] + historico[-8:]
    client = current_app.config["OPENAI_CLIENT"]
    model  = current_app.config["OPENAI_MODEL"]
    fallback = "Fechado! Você tem algum modelo em mente ou prefere que eu mande as ofertas mais pedidas?"
    if not client:
        texto = fallback
    else:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=0.7, timeout=8)
            texto = (r.choices[0].message.content or "").strip() or fallback
        except Exception:
            log.exception("Erro ao chamar OpenAI"); texto = fallback
    historico.append({"role": "assistant", "content": texto})
    sessions[numero] = historico[-12:]
    save_sessions(sessions)
    return texto

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

    tzinfo = current_app.config["TZINFO"]
    tz     = current_app.config["TZ"]
    cal_id = current_app.config["GCAL_CALENDAR_ID"]
    sa_b64 = current_app.config["GOOGLE_SERVICE_ACCOUNT_B64"]

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
        dt = dt.replace(minute=0, second=0, microsecond=0)
        try:
            svc = build_gcal(sa_b64, cal_id)
            if not is_slot_available(svc, dt, tzinfo, cal_id, tz):
                return ("Esse horário não está disponível. "
                        "Envie outro horário (em blocos de 1h, ex.: 10:00, 11:00, 14:00). "
                        "Se quiser, diga *slots 21/09/2025* para ver horários livres do dia.")
        except Exception:
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
                svc = build_gcal(sa_b64, cal_id)
                start_dt = datetime.fromisoformat(data["start_iso"])
                if not is_slot_available(svc, start_dt, tzinfo, cal_id, tz):
                    appointments_state.pop(phone, None)
                    return "Esse horário acabou de ficar indisponível. Vamos escolher outro?"
                event_id, start_dt = create_event(
                    svc, tzinfo=tzinfo, tz=tz, calendar_id=cal_id,
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

    return start_flow(phone)

def save_appointment_log(row: dict):
    path = current_app.config["APPT_FILE"]
    header = ["timestamp_log", "telefone", "tipo", "nome", "carro", "cidade", "start_iso", "event_id"]
    with _lock:
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(header)
            w.writerow([
                datetime.now().isoformat(),
                row.get("telefone",""), row.get("tipo",""), row.get("nome",""), row.get("carro",""),
                row.get("cidade",""), row.get("start_iso",""), row.get("event_id","")
            ])

# =========================
# Utils HTTP
# =========================
def twiml(texto: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Message>' + xml_escape(texto or "") + '</Message></Response>'

def require_admin():
    token = request.args.get("token") or request.headers.get("X-Admin-Token")
    if token != current_app.config["ADMIN_TOKEN"]: abort(403, description="Acesso negado")

def normalize_phone(raw: str) -> str:
    raw = (raw or "").strip()
    return raw[len("whatsapp:"):] if raw.startswith("whatsapp:") else raw

def _send_and_http_respond(to_phone_e164: str, text: str) -> Response:
    """Envia via API (se toggle ligado) e sempre responde 200 (sem travar o Twilio)."""
    if current_app.config.get("FORCE_TWILIO_API_REPLY"):
        if send_via_twilio_api(to_phone_e164, text):
            return Response("", status=200, mimetype="text/plain")
    # fallback TwiML (Sandbox/sem toggle)
    return Response(twiml(text), mimetype="application/xml")

# =========================
# Rotas
# =========================
@bp.route("/healthz")
def healthz():
    leads_file = current_app.config["LEADS_FILE"]
    leads_count = 0
    if os.path.exists(leads_file):
        try:
            with open(leads_file, "r", encoding="utf-8") as f:
                leads_count = max(0, sum(1 for _ in f) - 1)
        except Exception:
            leads_count = -1
    return jsonify({
        "ok": True,
        "model": current_app.config["OPENAI_MODEL"],
        "sessions": len(sessions),
        "leads": leads_count,
        "port": os.getenv("PORT", "5000")
    })

@bp.route("/slots")
def slots():
    d_str = request.args.get("date")
    if not d_str: return jsonify({"error": "Passe ?date=YYYY-MM-DD"}), 400
    try:
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        tzinfo = current_app.config["TZINFO"]
        tz     = current_app.config["TZ"]
        cal_id = current_app.config["GCAL_CALENDAR_ID"]
        sa_b64 = current_app.config["GOOGLE_SERVICE_ACCOUNT_B64"]
        svc = build_gcal(sa_b64, cal_id)
        busy = freebusy(svc, d, tz, tzinfo, cal_id)
        bh_start, bh_end = business_hours_for(d, tzinfo)
        bh_start = bh_start.replace(tzinfo=None)
        bh_end   = bh_end.replace(tzinfo=None)

        slots = []
        cur = bh_start
        while cur < bh_end:
            end = cur + timedelta(hours=1)
            free = all(not (s < end and cur < e) for s, e in busy)
            if free: slots.append(cur.strftime("%H:%M"))
            cur = end
        return jsonify({"date": d_str, "timezone": tz, "slots": slots})
    except Exception:
        log.exception("Erro ao consultar slots")
        return jsonify({"error": "Falha ao consultar disponibilidade"}), 500

@bp.route("/cron/reminders", methods=["POST","GET"])
def cron_reminders():
    path = current_app.config["APPT_FILE"]
    if not os.path.exists(path): return jsonify({"ok": True, "sent": 0, "msg": "sem agendamentos"})
    alvo = (datetime.now() + timedelta(days=1)).date()
    enviados = 0
    with open(path, "r", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            try:
                start = datetime.fromisoformat(row["start_iso"])
                if start.date() == alvo:
                    nome = row.get("nome","").split()[0] or "cliente"
                    texto = (f"Olá {nome}! Só confirmando seu agendamento na {current_app.config.get('DEALERSHIP_NAME','Fiat Globo Itajaí')} amanhã às "
                             f"{start.strftime('%H:%M')} para {row.get('tipo','visita')}: {row.get('carro','carro')}.\n"
                             "Se precisar remarcar, me avise por aqui. Até breve! 🚗✨")
                    phone = row.get("telefone","")
                    if send_via_twilio_api(phone, texto): enviados += 1
            except Exception:
                log.exception("Erro ao processar lembrete")
    return jsonify({"ok": True, "sent": enviados})

def _handle_incoming():
    from_number = normalize_phone(request.form.get("From", ""))
    body = (request.form.get("Body", "") or "").strip()

    if not from_number:
        log.warning("Requisição sem From."); return Response("", status=200, mimetype="text/plain")

    if body.upper() == "SAIR":
        sessions.pop(from_number, None); save_sessions(sessions)
        appointments_state.pop(from_number, None)
        return _send_and_http_respond(from_number, "Você foi removido. Quando quiser voltar, é só mandar OI. 👋")

    # 1) agendamento (prioritário)
    if wants_appointment(body) or from_number in appointments_state:
        resp = step_flow(from_number, body) if from_number in appointments_state else start_flow(from_number)
        save_lead(from_number, body, resp)
        return _send_and_http_respond(from_number, resp)

    # 2) saudação humana (1x por 15 min)
    if is_greeting(body) and should_greet(from_number):
        resp = human_greeting(body)
        mark_greeted(from_number)
        save_lead(from_number, body, resp)
        return _send_and_http_respond(from_number, resp)

    # 3) catálogo (link curto / cards enxutos)
    resp_cat = tentar_responder_com_catalogo(body, current_app.config["OFFERS_PATH"])
    if resp_cat:
        save_lead(from_number, body, resp_cat)
        return _send_and_http_respond(from_number, resp_cat)

    # 4) IA fallback
    resp_ai = gerar_resposta(from_number, body)
    save_lead(from_number, body, resp_ai)
    return _send_and_http_respond(from_number, resp_ai)

@bp.route("/whatsapp", methods=["POST"])
def whatsapp(): return _handle_incoming()

@bp.route("/webhook", methods=["POST"])
def webhook():  return _handle_incoming()

@bp.route("/simulate")
def simulate():
    frm = request.args.get("from", "whatsapp:+5500000000000")
    msg = request.args.get("msg", "Bom dia")
    with current_app.test_request_context("/webhook", method="POST", data={"From": frm, "Body": msg}):
        return _handle_incoming()

@bp.route("/painel")
def painel():
    path = current_app.config["LEADS_FILE"]
    if not os.path.exists(path): return "Nenhum lead ainda."
    rows = []
    with open(path, "r", encoding="utf-8") as f:
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

@bp.route("/agenda")
def agenda():
    token = request.args.get("token")
    if token != current_app.config["ADMIN_TOKEN"]: return "Acesso negado", 403
    path = current_app.config["APPT_FILE"]
    if not os.path.exists(path): return "Nenhum agendamento ainda."
    rows = []
    with open(path, "r", encoding="utf-8") as f:
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

@bp.route("/reset", methods=["POST"])
def reset():
    token = request.args.get("token")
    if token != current_app.config["ADMIN_TOKEN"]: return "Acesso negado", 403
    deleted=[]
    with _lock:
        for p in [current_app.config["LEADS_FILE"], current_app.config["SESSIONS_FILE"], current_app.config["APPT_FILE"]]:
            if os.path.exists(p): os.remove(p); deleted.append(os.path.basename(p))
        sessions.clear(); save_sessions(sessions)
    return jsonify({"ok": True, "deleted": deleted})
