import os
import json
import threading
from datetime import datetime
from flask import Flask, request, Response, jsonify
from markupsafe import escape
from openai import OpenAI

# Inicializa Flask
app = Flask(__name__)

# Inicializa cliente OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Arquivos de dados
SESSIONS_FILE = "sessions.json"
LEADS_FILE = "leads.csv"

# Lock para evitar concorrência em escrita
lock = threading.Lock()

# Carrega histórico de sessões da memória local
if os.path.exists(SESSIONS_FILE):
    with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
        sessions = json.load(f)
else:
    sessions = {}

# Função para salvar sessões
def save_sessions():
    with lock:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)

# Função para salvar leads em CSV
def save_lead(phone, name, intent):
    header = "datetime,phone,name,intent\n"
    line = f"{datetime.now().isoformat()},{phone},{name},{intent}\n"
    with lock:
        new_file = not os.path.exists(LEADS_FILE)
        with open(LEADS_FILE, "a", encoding="utf-8") as f:
            if new_file:
                f.write(header)
            f.write(line)

# Normaliza número do WhatsApp
def normalize_phone(phone):
    return phone.replace("whatsapp:", "").replace("+", "")

# -------------------------------
# ROTAS
# -------------------------------

@app.route("/healthz")
def healthz():
    """Verifica se o app está online"""
    return jsonify({
        "status": "ok",
        "service": "fiat-globo-whatsapp-piloto",
        "port": os.getenv("PORT", "5000")
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    """Recebe mensagens do Twilio WhatsApp"""
    from_number = normalize_phone(request.form.get("From", ""))
    body = request.form.get("Body", "").strip()

    if not from_number or not body:
        return Response("<Response></Response>", mimetype="text/xml")

    # Pega histórico da sessão do usuário
    history = sessions.get(from_number, [])
    history.append({"role": "user", "content": body})

    # Garante limite de histórico
    if len(history) > 10:
        history = history[-10:]

    # Chama OpenAI
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": (
                "Você é um assistente de vendas da Fiat Globo. "
                "Seu objetivo é captar leads, responder dúvidas sobre veículos, "
                "e incentivar agendamento de test drive. "
                "Sempre responda de forma clara, profissional e simpática."
            )}
        ] + history
    )

    answer = completion.choices[0].message.content.strip()

    # Salva histórico atualizado
    history.append({"role": "assistant", "content": answer})
    sessions[from_number] = history
    save_sessions()

    # Simulação simples de captura de lead
    if "teste drive" in body.lower() or "test drive" in body.lower():
        save_lead(from_number, "Cliente", "Agendamento de test drive")

    # Retorna resposta para o Twilio
    twiml = f"<Response><Message>{escape(answer)}</Message></Response>"
    return Response(twiml, mimetype="text/xml")

@app.route("/simulate", methods=["GET"])
def simulate():
    """Testa envio de mensagem sem precisar do Twilio"""
    msg = request.args.get("msg", "Oi, quero informações sobre carros")
    fake_request = {"From": "whatsapp:+5500000000000", "Body": msg}
    with app.test_request_context("/webhook", method="POST", data=fake_request):
        return webhook()

@app.route("/admin/leads", methods=["GET"])
def admin_leads():
    """Exibe os leads coletados em CSV (precisa de token simples)"""
    token = request.args.get("token")
    if token != os.getenv("ADMIN_TOKEN", "1234"):
        return "Unauthorized", 403

    if not os.path.exists(LEADS_FILE):
        return "Nenhum lead coletado ainda."

    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/plain")

# -------------------------------
# MAIN
# -------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
