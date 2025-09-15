from flask import Flask, request, render_template_string
import csv
import os

app = Flask(__name__)

# Caminho para armazenar os leads
DATA_FILE = "data/leads.csv"

# Cria a pasta e o arquivo CSV se não existirem
os.makedirs("data", exist_ok=True)
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["from", "body"])  # Cabeçalho do CSV


@app.route("/")
def home():
    return "🚀 API do WhatsApp está no ar!"


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    """Endpoint que recebe mensagens do Twilio WhatsApp"""
    from_number = request.form.get("From")
    body = request.form.get("Body")

    # Grava no CSV
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([from_number, body])

    # Resposta XML para Twilio
    return f"""
    <Response>
        <Message>Temos o Fiat Pulse e o Fastback disponíveis em promoção!</Message>
    </Response>
    """, 200, {"Content-Type": "application/xml"}


@app.route("/painel")
def painel():
    """Exibe os leads recebidos"""
    mensagens = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            mensagens = list(reader)

    html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Painel de Leads</title>
        <link rel="stylesheet"
              href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    </head>
    <body class="p-4">
        <div class="container">
            <h1 class="mb-4">📋 Painel de Leads</h1>
            {% if mensagens %}
            <table class="table table-striped table-bordered">
                <thead class="table-dark">
                    <tr>
                        <th>Telefone</th>
                        <th>Mensagem</th>
                    </tr>
                </thead>
                <tbody>
                {% for msg in mensagens %}
                    <tr>
                        <td>{{ msg['from'] }}</td>
                        <td>{{ msg['body'] }}</td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>
            {% else %}
                <p>Nenhuma mensagem recebida ainda.</p>
            {% endif %}
        </div>
    </body>
    </html>
    """
    return render_template_string(html, mensagens=mensagens)


if __name__ == "__main__":
    # Só roda localmente; no Railway o Gunicorn cuida disso
    app.run(host="0.0.0.0", port=5000, debug=True)
