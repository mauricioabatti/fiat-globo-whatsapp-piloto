# app.py
import os, logging
from flask import Flask
from openai import OpenAI
from zoneinfo import ZoneInfo

# módulos locais
from routes import bp as routes_bp

# =========================
# Config & logging
# =========================
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fiat-whatsapp")

# =========================
# App Factory
# =========================
def create_app():
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    # Env
    app.config.update(
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY"),
        OPENAI_MODEL=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        ADMIN_TOKEN=os.getenv("ADMIN_TOKEN", "1234"),
        TZ=os.getenv("TZ", "America/Sao_Paulo"),
        TZINFO=ZoneInfo(os.getenv("TZ", "America/Sao_Paulo")),
        GCAL_CALENDAR_ID=os.getenv("GCAL_CALENDAR_ID"),
        GOOGLE_SERVICE_ACCOUNT_B64=os.getenv("GOOGLE_SERVICE_ACCOUNT_B64"),
        TWILIO_ACCOUNT_SID=os.getenv("TWILIO_ACCOUNT_SID"),
        TWILIO_AUTH_TOKEN=os.getenv("TWILIO_AUTH_TOKEN"),
        TWILIO_WHATSAPP_FROM=os.getenv("TWILIO_WHATSAPP_FROM"),
        DATA_DIR="data",
        SESSIONS_FILE=os.path.join("data", "sessions.json"),
        LEADS_FILE=os.path.join("data", "leads.csv"),
        APPT_FILE=os.path.join("data", "agendamentos.csv"),
        OFFERS_PATH=os.path.join("data", "ofertas.json"),
    )

    # Pastas
    os.makedirs(app.config["DATA_DIR"], exist_ok=True)

    # OpenAI
    api_key = app.config["OPENAI_API_KEY"]
    if not api_key:
        log.warning("OPENAI_API_KEY não definida — IA será fallback indisponível se precisar.")
    app.config["OPENAI_CLIENT"] = OpenAI(api_key=api_key) if api_key else None

    # Registra rotas (Blueprint)
    app.register_blueprint(routes_bp)

    return app


# =========================
# Run
# =========================
if __name__ == "__main__":
    app = create_app()
    port_env = os.getenv("PORT", "5000")
    try:
        port = int(port_env)
    except:
        port = 5000
        log.error(f"PORT inválida ('{port_env}'). Usando 5000 localmente.")
    app.run(host="0.0.0.0", port=port, debug=False)
