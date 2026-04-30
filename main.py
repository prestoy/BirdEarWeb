import yaml
from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from datetime import datetime, timedelta
import httpx
import csv
import os
import locale
import time
from bcrypt import checkpw
from typing import Optional
import re

# Les config.yaml
def load_config(config_path="config.yaml"):
    with open(config_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)

config = load_config()

locale.setlocale(locale.LC_TIME, config.get("locale", ""))

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/audio", StaticFiles(directory=config["audio-path"]), name="audio")

templates = Jinja2Templates(directory="web-maler")

def datetimeformat(value, format='%d. %B %Y %H:%M:%S'):
    try:
        formatted_date = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f').strftime(format)
    except ValueError:
        try:
            formatted_date = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S').strftime(format)
        except ValueError:
            try:
                formatted_date = datetime.strptime(value, '%Y-%m-%d %H:%M:%S').strftime(format)
            except ValueError:
                formatted_date = datetime.strptime(value, '%Y-%m-%d').strftime(format)
    return formatted_date

templates.env.filters['datetimeformat'] = datetimeformat
templates.env.filters['month_name'] = lambda month: datetime(2000, month, 1).strftime("%B")

# Hjelpefunksjon: Les artsmapping fra CSV
def load_species_mapping(csv_path):
    mapping = {}
    try:
        with open(csv_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file, delimiter=";")
            for row in reader:
                mapping[row["SCI"]] = row["NO"]
    except KeyError as e:
        with open(csv_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            available_columns = reader.fieldnames
        raise KeyError(f"Kolonnen '{e.args[0]}' finnes ikke i CSV-filen. Tilgjengelige kolonner: {available_columns}")
    return mapping

species_mapping = load_species_mapping(config["species-map"])

# ----------------------------------------------------------------
# API-klient
# ----------------------------------------------------------------

API_URL = config["api-url"]

def api_get(path: str, params: dict = None):
    """Utfør GET-forespørsel mot BirdMic API."""
    try:
        response = httpx.get(f"{API_URL}{path}", params=params, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"API-feil: {e}")

def api_post(path: str, json: dict = None):
    """Utfør POST-forespørsel mot BirdMic API."""
    try:
        response = httpx.post(f"{API_URL}{path}", json=json, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"API-feil: {e}")

# ----------------------------------------------------------------
# Hjelpefunksjoner (kaller API)
# ----------------------------------------------------------------

def get_detection_days(year, month):
    return api_get("/detections/days", {"year": year, "month": month})

def get_detections_for_date(date, min_conf):
    rows = api_get("/detections/by_date", {"date": date, "min_conf": min_conf})
    return [(row["scientific_name"], row["hour"]) for row in rows]

def get_species_details(date, scientific_name, hour=None, min_conf=0.0):
    params = {"date": date, "scientific_name": scientific_name, "min_conf": min_conf}
    if hour is not None:
        params["hour"] = hour
    return api_get("/detections/species_details", params)

def get_species_list(date):
    rows = api_get("/detections/species_list", {"date": date})
    from collections import defaultdict
    species_data = defaultdict(list)
    for row in rows:
        species_data[row["scientific_name"]].append(row["confidence"])
    species_list = []
    for scientific_name, confidences in species_data.items():
        total_detections = len(confidences)
        confidence_median = calculate_median(confidences)
        common_name = species_mapping.get(scientific_name, "Ukjent")
        species_list.append({
            "scientific_name": scientific_name,
            "common_name": common_name,
            "total_detections": total_detections,
            "confidence_median": confidence_median
        })
    species_list.sort(key=lambda x: x["total_detections"], reverse=True)
    return species_list

# ----------------------------------------------------------------
# Autentisering
# ----------------------------------------------------------------

def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": time.time() + config["access_token_expire_seconds"]})
    return jwt.encode(to_encode, config["secret_key"], algorithm=config["secret_algorithm"])

def verify_token(token: str):
    try:
        payload = jwt.decode(token, config["secret_key"], algorithms=config["secret_algorithm"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Ugyldig eller utløpt token")

def verify_password(password: str):
    with open(config["password_hash_file"], "r") as file:
        stored_hash = file.read().strip().encode("utf-8")
    return checkpw(password.encode("utf-8"), stored_hash)

# ----------------------------------------------------------------
# Hjelpefunksjoner
# ----------------------------------------------------------------

def calculate_median(values):
    values = sorted(values)
    n = len(values)
    if n == 0:
        return None
    if n % 2 == 1:
        return values[n // 2]
    else:
        return (values[n // 2 - 1] + values[n // 2]) / 2

def calculate_offset_time(timestamp_str, offset):
    pattern = r"^(\d{4}-\d{2}-\d{2}).(\d{2}:\d{2}:\d{2})$"
    match = re.match(pattern, timestamp_str)
    if not match:
        raise ValueError(f"Invalid timestamp format: {timestamp_str}")
    date_part, time_part = match.groups()
    timestamp = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
    if isinstance(offset, (int, float)):
        offset = timedelta(seconds=offset)
    return (timestamp + offset).strftime("%Y-%m-%d %H:%M:%S")

# ----------------------------------------------------------------
# Ruter
# ----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def calendar_view(request: Request, year: int = None, month: int = None):
    if year is None or month is None:
        today = datetime.today()
        year, month = today.year, today.month

    first_day = datetime(year, month, 1)
    prev_month = (first_day - timedelta(days=1)).replace(day=1)
    next_month = (first_day + timedelta(days=31)).replace(day=1)

    detection_days = get_detection_days(year, month)

    last_day = (first_day + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    calendar = []
    current_day = first_day

    start_weekday = first_day.weekday()
    if start_weekday > 0:
        calendar.extend([None] * start_weekday)

    while current_day <= last_day:
        day_str = current_day.strftime("%Y-%m-%d")
        calendar.append({
            "day": current_day.day,
            "has_detections": day_str in detection_days,
            "link": f"/show_detections?date={day_str}" if day_str in detection_days else None
        })
        current_day += timedelta(days=1)

    end_weekday = last_day.weekday()
    if end_weekday < 6:
        calendar.extend([None] * (6 - end_weekday))

    return templates.TemplateResponse(request, "calendar.html", {
        "year": year,
        "month": month,
        "calendar": calendar,
        "prev_month": {"year": prev_month.year, "month": prev_month.month},
        "next_month": {"year": next_month.year, "month": next_month.month},
        "title": "Dager med lydregistrering av fugler"
    })


@app.get("/show_detections", response_class=HTMLResponse)
async def show_detections(request: Request, date: str, min_conf: float = 0.8):
    from collections import defaultdict
    detections = get_detections_for_date(date, min_conf)

    species_histogram = defaultdict(lambda: [0] * 24)
    for scientific_name, hour in detections:
        species_histogram[scientific_name][hour] += 1

    species_data = []
    for scientific_name, histogram in species_histogram.items():
        common_name = species_mapping.get(scientific_name, "Ukjent")
        total_count = sum(histogram)
        species_data.append({
            "scientific_name": scientific_name,
            "common_name": common_name,
            "histogram": histogram,
            "total_count": total_count
        })

    species_data.sort(key=lambda x: x["total_count"], reverse=True)

    return templates.TemplateResponse(request, "show_detections.html", {
        "date": date,
        "species_data": species_data,
        "min_conf": min_conf,
        "title": f"Deteksjoner for {date}"
    })


@app.get("/species_details", response_class=HTMLResponse)
async def species_details(
    request: Request,
    scientific_name: str,
    date: str,
    hour: int = None,
    min_conf: float = 0.0
):
    scientific_name = scientific_name.replace("_", " ")
    rows = get_species_details(date, scientific_name, hour, min_conf)

    detections = []
    for row in rows:
        audio_file_path = os.path.join(config["audio-path"], row["recording"]) if row["recording"] else None
        if audio_file_path and os.path.isfile(audio_file_path):
            audio_file = f"/audio/{row['recording']}"
        else:
            audio_file = None

        start_time_display = round(row["start_time"], 1) if row["start_time"] is not None else None
        end_time_display = round(row["end_time"], 1) if row["end_time"] is not None else None

        detections.append({
            "timestamp": row["timestamp"],
            "recording": row["recording"],
            "audio_file": audio_file,
            "start_time": start_time_display,
            "end_time": end_time_display,
            "confidence": row["confidence"] if row["confidence"] is not None else 0.0
        })

    common_name = species_mapping.get(scientific_name, "Ukjent")

    return templates.TemplateResponse(request, "species_day_details.html", {
        "scientific_name": scientific_name,
        "common_name": common_name,
        "date": date,
        "hour": hour,
        "min_conf": min_conf,
        "detections": detections,
        "total_detections": len(detections),
        "title": f"Detaljer for {common_name} ({scientific_name})"
    })


@app.post("/authenticate")
async def authenticate(
    password: str = Form(...),
    scientific_name: str = Form(...),
    date: str = Form(...)
):
    if verify_password(password):
        token = create_access_token({"sub": "admin"})
        response = RedirectResponse(
            url=f"/species_detections_admin?scientific_name={scientific_name}&date={date}",
            status_code=303
        )
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            max_age=config["access_token_expire_seconds"]
        )
        return response
    else:
        raise HTTPException(status_code=401, detail="Feil passord")


@app.get("/species_admin", response_class=HTMLResponse)
async def species_admin(request: Request, date: str):
    species_list = get_species_list(date)
    return templates.TemplateResponse(request, 'species_admin.html', {
        "date": date,
        "species_list": species_list
    })


@app.post("/species_admin/archive")
async def species_admin_archive(
    request: Request,
    archive_species: list[str] = Form(...),
    date: str = Form(...),
    confirm: Optional[bool] = Form(False)
):
    if not confirm:
        species_list = []
        total_detections = 0

        for scientific_name in archive_species:
            rows = api_get("/detections/admin", {"date": date, "scientific_name": scientific_name})
            common_name = species_mapping.get(scientific_name, "Ukjent")
            species_list.append({
                "scientific_name": scientific_name,
                "common_name": common_name,
                "detections": len(rows)
            })
            total_detections += len(rows)

        return templates.TemplateResponse(request, "confirmation_prompt.html", {
            "date": date,
            "species_list": species_list,
            "total_detections": total_detections
        })

    for scientific_name in archive_species:
        api_post("/detections/archive_species", {"date": date, "scientific_name": scientific_name})

    return RedirectResponse(url=f"/species_admin?date={date}", status_code=303)


@app.get("/species_detections_admin", response_class=HTMLResponse)
async def species_detections_admin(
    request: Request,
    scientific_name: str,
    date: str
):
    rows = api_get("/detections/admin", {"date": date, "scientific_name": scientific_name})
    common_name = species_mapping.get(scientific_name, "Ukjent")

    return templates.TemplateResponse(request, "species_detections_admin.html", {
        "scientific_name": scientific_name,
        "common_name": common_name,
        "date": date,
        "detections": rows,
        "confidence_threshold": 1.0,
        "total_detections": len(rows)
    })


@app.post("/species_detections_admin/archive")
async def species_detections_admin_archive(
    request: Request,
    scientific_name: str = Form(...),
    date: str = Form(...),
    archive_detections: Optional[list[str]] = Form(None),
    confirm: Optional[bool] = Form(False)
):
    if not confirm:
        false_positive_detections = []
        total_detections = 0
        common_name = species_mapping.get(scientific_name, "Ukjent")

        if archive_detections:
            ids = [int(i) for i in archive_detections]
            rows = api_get("/detections/by_ids", {"ids": ids})
            for row in rows:
                false_positive_detections.append({
                    "id": row["id"],
                    "timestamp": calculate_offset_time(row["timestamp"], row["start_time"])[11:],
                    "confidence": row["confidence"]
                })
            total_detections = len(rows)

        return templates.TemplateResponse(request, "confirmation_prompt.html", {
            "date": date,
            "scientific_name": scientific_name,
            "common_name": common_name,
            "false_positive_detections": false_positive_detections,
            "total_detections": total_detections
        })

    if archive_detections:
        ids = [int(i) for i in archive_detections]
        api_post("/detections/archive_by_ids", {"ids": ids})

    return RedirectResponse(
        url=f"/species_detections_admin?scientific_name={scientific_name}&date={date}",
        status_code=303
    )
