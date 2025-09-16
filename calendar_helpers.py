# calendar_helpers.py
import base64, json
from datetime import datetime, timedelta, date, time

from googleapiclient.discovery import build
from google.oauth2 import service_account

# --------- Google Calendar ----------
def build_gcal(sa_b64: str, calendar_id: str):
    if not (sa_b64 and calendar_id):
        raise RuntimeError("Faltam GOOGLE_SERVICE_ACCOUNT_B64 ou GCAL_CALENDAR_ID.")
    try:
        creds_json = base64.b64decode(sa_b64).decode("utf-8")
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/calendar"]
        )
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return svc
    except Exception as e:
        raise RuntimeError(f"Erro ao inicializar Google Calendar: {e}")

def business_hours_for(d: date, tzinfo):
    start = datetime.combine(d, time(9, 0)).replace(tzinfo=tzinfo)
    end   = datetime.combine(d, time(18, 0)).replace(tzinfo=tzinfo)
    return start, end

def _to_local_naive(dt_str: str, tzinfo):
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt.astimezone(tzinfo).replace(tzinfo=None)

def freebusy(svc, d: date, tz: str, tzinfo, calendar_id: str):
    start, end = business_hours_for(d, tzinfo)
    body = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "timeZone": tz,
        "items": [{"id": calendar_id}]
    }
    fb = svc.freebusy().query(body=body).execute()
    busy = fb["calendars"][calendar_id].get("busy", [])
    return [(_to_local_naive(b["start"], tzinfo), _to_local_naive(b["end"], tzinfo)) for b in busy]

def is_slot_available(svc, start_dt: datetime, tzinfo, calendar_id: str, tz: str) -> bool:
    if start_dt.tzinfo is not None:
        start_dt = start_dt.astimezone(tzinfo).replace(tzinfo=None)
    else:
        start_dt = start_dt.replace(tzinfo=None)
    start_dt = start_dt.replace(minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(hours=1)

    bh_start, bh_end = business_hours_for(start_dt.date(), tzinfo)
    bh_start = bh_start.replace(tzinfo=None)
    bh_end   = bh_end.replace(tzinfo=None)
    if not (bh_start <= start_dt < bh_end):
        return False

    for s, e in freebusy(svc, start_dt.date(), tz, tzinfo, calendar_id):
        if (s < end_dt) and (start_dt < e):
            return False
    return True

def create_event(
    svc, *, tzinfo, tz, calendar_id: str,
    tipo: str, nome: str, carro: str, cidade: str, telefone: str, start_dt: datetime
):
    start_dt = start_dt.replace(minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(hours=1)

    start_aware = start_dt.replace(tzinfo=tzinfo)
    end_aware   = end_dt.replace(tzinfo=tzinfo)

    summary = f"{'Test Drive' if 'test' in tipo.lower() else 'Visita'}: {nome} - {carro}"
    description = (
        f"Cliente: {nome}\nTelefone: {telefone}\nCidade: {cidade}\nTipo: {tipo}\nFonte: WhatsApp"
    )
    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_aware.isoformat(), "timeZone": tz},
        "end":   {"dateTime": end_aware.isoformat(),   "timeZone": tz},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]}
    }
    created = svc.events().insert(calendarId=calendar_id, body=event).execute()
    return created.get("id"), start_dt
