from flask import Flask, request, jsonify
import csv, os

app = Flask(__name__)

# Caminhos dos CSVs de exemplo
VEICULOS_CSV = os.path.join(os.path.dirname(__file__), "data", "veiculos.csv")
LEADS_CSV = os.path.join(os.path.dirname(__file__), "data", "leads.csv")

@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    msg = request.form.get('Body', '').lower()
    resp = "Não entendi sua mensagem."
    
    if "suv" in msg:
        resp = "Temos o Fiat Pulse e o Fastback disponíveis em promoção!"
    elif "sedan" in msg:
        resp = "Temos o Fiat Cronos disponível para test drive!"
    elif "teste" in msg or "test drive" in msg:
        resp = "Agendamento realizado! Nossa equipe entrará em contato."
        with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([msg])
    
    return f"<Response><Message>{resp}</Message></Response>"

@app.route('/painel')
def painel():
    leads = []
    if os.path.exists(LEADS_CSV):
        with open(LEADS_CSV, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            leads = list(reader)
    html = "<h1>Painel de Leads</h1><ul>"
    for l in leads:
        html += f"<li>{l[0]}</li>"
    html += "</ul>"
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
