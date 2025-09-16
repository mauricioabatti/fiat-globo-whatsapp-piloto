# app.py
import os, logging, json
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
    log = logging.getLogger("fiat-whatsapp")

    # ---------- ENV ----------
    app.config["ADMIN_TOKEN"] = os.getenv("ADMIN_TOKEN", "1234")
    app.config["TZ"] = os.getenv("TZ", "America/Sao_Paulo")
    app.config["TZINFO"] = ZoneInfo(app.config["TZ"])

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

    # ---------- KB (prompt + fewshots) ----------
    app.config["KB_DIR"] = os.getenv("KB_DIR", "kb")
    app.config["SYS_PROMPT_PATH"] = os.path.join(app.config["KB_DIR"], "system_prompt.txt")
    app.config["FEWSHOTS_PATH"]   = os.path.join(app.config["KB_DIR"], "fewshots.json")

    def _read_text(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return (f.read() or "").strip()
        except Exception:
            log.exception(f"Não consegui ler {path}")
            return ""

    def _read_messages_json(path: str):
        """Espera lista de {'role': 'user'|'assistant'|'system', 'content': '...'}"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            msgs = []
            if isinstance(data, list):
                for m in data:
                    if isinstance(m, dict) and "role" in m and "content" in m:
                        msgs.append({"role": m["role"], "content": m["content"]})
            return msgs
        except Exception:
            log.exception(f"Não consegui ler {path}")
            return []

    app.config["SYSTEM_PROMPT_TEXT"] = _read_text(app.config["SYS_PROMPT_PATH"])
    app.config["FEWSHOTS_MSGS"]      = _read_messages_json(app.config["FEWSHOTS_PATH"])
    log.info(
        f"[KB] system_prompt: {len(app.config['SYSTEM_PROMPT_TEXT'])} chars | "
        f"fewshots: {len(app.config['FEWSHOTS_MSGS'])} msgs"
    )

    # ---------- BLUEPRINT ----------
    from routes import bp as routes_bp
    app.register_blueprint(routes_bp)

    @app.route("/")
    def home(): return "Servidor Flask rodando! ✅"

    return app

if __name__ == "__main__":
    app = create_app()
    port_env = os.getenv("PORT", "5000")
    try:
        port = int(port_env)
    except:
        port = 5000
    app.run(host="0.0.0.0", port=port, debug=False)
