# app.py
import os, logging
from flask import Flask
from zoneinfo import ZoneInfo
from openai import OpenAI

def create_app():
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    # ---------- LOG ----------
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    # ---------- ENV ----------
    app.config["ADMIN_TOKEN"] = os.getenv("ADMIN_TOKEN", "1234")
    app.config["TZ"] = os.getenv("TZ", "America/Sao_Paulo")
    app.config["TZINFO"] = ZoneInfo(app.config["TZ"])

    # Consultor / Loja (defaults prontos)
    app.config["CONSULTOR_NAME"] = os.getenv("CONSULTOR_NAME", "Felipe Fortes")
    app.config["DEALERSHIP_NAME"] = os.getenv("DEALERSHIP_NAME", "Fiat Globo Itajaí")

    # OpenAI
    app.config["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
    app.config["OPENAI_MODEL"] = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    app.config["OPENAI_CLIENT"] = (
        OpenAI(api_key=app.config["OPENAI_API_KEY"]) if app.config["OPENAI_API_KEY"] else None
    )

    # Twilio (opcional)
    app.config["TWILIO_ACCOUNT_SID"] = os.getenv("TWILIO_ACCOUNT_SID")
    app.config["TWILIO_AUTH_TOKEN"] = os.getenv("TWILIO_AUTH_TOKEN")
    app.config["TWILIO_WHATSAPP_FROM"] = os.getenv("TWILIO_WHATSAPP_FROM")  # ex.: whatsapp:+1415...
    app.config["FORCE_TWILIO_API_REPLY"] = os.getenv("FORCE_TWILIO_API_REPLY", "0") in ("1", "true", "True")

    # Google Calendar
    app.config["GCAL_CALENDAR_ID"] = os.getenv("GCAL_CALENDAR_ID", "")
    app.config["GOOGLE_SERVICE_ACCOUNT_B64"] = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "")

    # ---------- FILES / DATA ----------
    DATA_DIR = "data"
    os.makedirs(DATA_DIR, exist_ok=True)
    app.config["DATA_DIR"] = DATA_DIR
    app.config["SESSIONS_FILE"] = os.path.join(DATA_DIR, "sessions.json")
    app.config["LEADS_FILE"] = os.path.join(DATA_DIR, "leads.csv")
    app.config["APPT_FILE"] = os.path.join(DATA_DIR, "agendamentos.csv")
    app.config["OFFERS_PATH"] = os.path.join(DATA_DIR, "ofertas.json")

    # ---------- BLUEPRINT ----------
    from routes import bp as routes_bp
    app.register_blueprint(routes_bp)

    @app.route("/")
    def home(): return "Servidor Flask rodando! ✅"

    return app

if __name__ == "__main__":
    app = create_app()
    port_env = os.getenv("PORT", "5000")
    try: port = int(port_env)
    except: port = 5000
    app.run(host="0.0.0.0", port=port, debug=False)
