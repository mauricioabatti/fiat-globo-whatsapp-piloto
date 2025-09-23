# calendar_helpers.py
import base64
import json
from datetime import datetime, timedelta, date, time
from typing import List, Tuple

from googleapiclient.discovery import build
from google.oauth2 import service_account


def _service_from_b64(sa_b64: str):
    if not sa_b64:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_B64 ausente.")
    payload = base64.b64decode(sa_b64).decode("utf-8")
    info = json.loads(payload)
    creds = service_account.Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/calendar"
    ])
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return svc


def build_gcal(sa_b64: str, calendar_id: str):
    svc = _service_from_b64(sa_b64)
    # simples ping para validar credenciais
    _ = calendar_id  # no-op
    return svc


def business_hours_for(d: date, tzinfo) -> Tuple[datetime, datetime]:
    # 09:00 às 18:00 por padrão
    start = datetime.combine(d, time(9, 0, 0), tzinfo=tzinfo)
    end   = datetime.combine(d, time(18, 0, 0), tzinfo=tzinfo)
    return start, end


def freebusy(svc, d: date, tz: str, tzinfo, calendar_id: str) -> List[Tuple[datetime, datetime]]:
    bh_start, bh_end = business_hours_for(d, tzinfo)
    body = {
        "timeMin": bh_start.isoformat(),
        "timeMax": bh_end.isoformat(),
        "timeZone": tz,
        "items": [{"id": calendar_id}],
    }
    resp = svc.freebusy().query(body=body).execute()
    busy = resp["calendars"][calendar_id].get("busy", [])
    slots = []
    for b in busy:
        s = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(tzinfo)
        e = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(tzinfo)
        slots.append((s.replace(tzinfo=None), e.replace(tzinfo=None)))
    return slots


def is_slot_available(svc, start_dt: datetime, tzinfo, calendar_id: str, tz: str) -> bool:
    start_dt = start_dt.replace(tzinfo=tzinfo)
    end_dt = start_dt + timedelta(hours=1)
    body = {
        "timeMin": start_dt.isoformat(),
        "timeMax": end_dt.isoformat(),
        "timeZone": tz,
        "items": [{"id": calendar_id}],
    }
    resp = svc.freebusy().query(body=body).execute()
    busy = resp["calendars"][calendar_id].get("busy", [])
    return len(busy) == 0


def create_event(
    svc, tzinfo, tz, calendar_id: str,
    tipo: str, nome: str, carro: str, cidade: str, telefone: str,
    start_dt: datetime
):
    start_dt = start_dt.replace(tzinfo=tzinfo)
    end_dt = start_dt + timedelta(hours=1)
    summary = f"[{tipo.upper()}] {nome} – {carro}"
    description = (
        f"Cliente: {nome}\n"
        f"Telefone: {telefone}\n"
        f"Tipo: {tipo}\n"
        f"Carro: {carro}\n"
        f"Cidade: {cidade}\n"
    )
    event_body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": tz},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": tz},
    }
    created = svc.events().insert(calendarId=calendar_id, body=event_body).execute()
    event_id = created.get("id")
    return event_id, start_dt
