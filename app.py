from flask import Flask, request, render_template_string
import csv, os, datetime

app = Flask(__name__)

DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "leads.csv")
os.makedirs(DATA_DIR, exist_ok=True)

def ensure_csv_header():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["time", "from", "body"])
        return
    # valida/corrige header
    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            header = []
    norm = [h.strip().lower() for h in header]
    if norm != ["time", "from", "body"]:
        rows = []
        with open(DATA_FILE, newline="", encoding="utf-8") as f:
            for r in csv.reader(f):
                if r and any(x.strip() for x in r):
                    rows.append(r)
        with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "from", "body"])
            for r in rows:
                t  = datetime.datetime.utcnow().isoformat()
                frm = (r[0] if len(r) > 0 else "").strip()
                body = (r[1] if len(r) > 1 else "").strip()
                if frm or body:
                    w.writerow([t, frm, body])

ensure_csv_header()

@app.route("/")
def home():
    return "🚀 API do WhatsApp no ar"

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    """Webhook do WhatsApp (Twilio). Envie Body/From (x-www-form-urlencoded)."""
    from_number = (request.values.get("From") or "").strip()
    body = (request.values.get("Body") or "").strip()

    if from_number or body:
        with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([datetime.datetime.utcnow().isoformat(), from_number, body])

    # Resposta simples (TwiML)
    return (
        "<Response><Message>Temos o Fiat Pulse e o Fastback disponíveis em promoção!</Message></Response>",
        200,
        {"Content-Type": "application/xml"},
    )

@app.route("/painel")
def painel():
    msgs = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t   = (r.get("time") or "").strip().replace("T", " ")[:19]
                frm = (r.get("from") or "").strip()
                body = (r.get("body") or "").strip()
                if frm or body:
                    msgs.append({"time": t, "from": frm, "body": body})

    html = """
    <!doctype html><html lang="pt-BR"><head>
      <meta charset="utf-8"><title>Painel de Leads</title>
      <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    </head><body class="p-4"><div class="container">
      <h1 class="mb-4">📋 Painel de Leads</h1>
      {% if msgs %}
      <table class="table table-striped table-bordered">
        <thead class="table-dark"><tr><th>Data/Hora (UTC)</th><th>Telefone</th><th>Mensagem</th></tr></thead>
        <tbody>
          {% for m in msgs %}
            <tr><td class="text-nowrap">{{ m.time }}</td><td>{{ m.from }}</td><td>{{ m.body }}</td></tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
        <div class="alert alert-secondary">Nenhuma mensagem recebida ainda.</div>
      {% endif %}
    </div></body></html>
    """
    return render_template_string(html, msgs=msgs)

# endpoint admin para limpar CSV (proteja com variável ADMIN_TOKEN)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "reset123")

@app.route("/admin/reset", methods=["POST"])
def admin_reset():
    token = request.args.get("token", "")
    if token != ADMIN_TOKEN:
        return "forbidden", 403
    with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["time", "from", "body"])
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
