from flask import Flask, request
import csv, os

app = Flask(__name__)

# CSVs
VEICULOS_CSV = os.path.join(os.path.dirname(__file__), "data", "veiculos.csv")
LEADS_CSV   = os.path.join(os.path.dirname(__file__), "data", "leads.csv")

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    msg = (request.form.get("Body") or "").lower()
    resp = "Não entendi sua mensagem."

    if "suv" in msg:
        resp = "Temos o Fiat Pulse e o Fastback disponíveis em promoção!"
    elif "sedan" in msg:
        resp = "Temos o Fiat Cronos disponível para test drive!"
    elif "teste" in msg or "test drive" in msg:
        resp = "Agendamento realizado! Nossa equipe entrará em contato."
        os.makedirs(os.path.dirname(LEADS_CSV), exist_ok=True)
        with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([msg])

    # Twilio aceita TwiML simples em texto
    return f"<Response><Message>{resp}</Message></Response>"

@app.route("/painel")
def painel():
    leads = []
    if os.path.exists(LEADS_CSV):
        with open(LEADS_CSV, newline="", encoding="utf-8") as f:
            leads = list(csv.reader(f))

    html = "<h1>Painel de Leads</h1><ul>"
    for l in leads:
        if l: html += f"<li>{l[0]}</li>"
    html += "</ul>"
    return html

if __name__ == "__main__":
    # útil apenas para rodar localmente
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
