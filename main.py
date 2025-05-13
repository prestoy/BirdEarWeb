import yaml  # For å lese config.yaml
from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from hashlib import sha256
from datetime import datetime, timedelta
import sqlite3
import csv
from collections import defaultdict
import os
import locale
import time
from bcrypt import checkpw

# Sett norsk locale
locale.setlocale(locale.LC_TIME, "nb_NO.UTF-8")

# Les config.yaml
def load_config(config_path="config.yaml"):
    with open(config_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)

config = load_config()

app = FastAPI()

# Sett opp Jinja2-templates
templates = Jinja2Templates(directory="web-maler")

# Legg til Jinja2-filter for datoformatering
def datetimeformat(value, format='%d. %B %Y %H:%M:%S'):
    try:
        # Håndter ISO 8601-tidsstempler med mikrosekunder
        formatted_date = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f').strftime(format)
    except ValueError:
        try:
            # Håndter ISO 8601-tidsstempler uten mikrosekunder
            formatted_date = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S').strftime(format)
        except ValueError:
            try:
                # Håndter tidsstempler uten 'T' (f.eks. 'YYYY-MM-DD HH:MM:SS')
                formatted_date = datetime.strptime(value, '%Y-%m-%d %H:%M:%S').strftime(format)
            except ValueError:
                # Håndter datoer uten tid (f.eks. 'YYYY-MM-DD')
                formatted_date = datetime.strptime(value, '%Y-%m-%d').strftime(format)
    return formatted_date

templates.env.filters['datetimeformat'] = datetimeformat

def month_name(month):
    months = [
        "Januar", "Februar", "Mars", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Desember"
    ]
    return months[month - 1]

templates.env.filters['month_name'] = month_name

# Mount statiske filer (for CSS, JS, etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mount lydfiler fra audio-path i config.yaml
#app.mount("/static/audio", StaticFiles(directory=config["audio-path"]), name="audio")
app.mount("/audio", StaticFiles(directory=config["audio-path"]), name="audio")

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

def get_db_connection():
    """
    Opprett en tilkobling til databasen i read-only-modus.
    """
    db_path = config["db-path"]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    return conn

def get_db_connection_rw():
    """
    Opprett en tilkobling til databasen i read-write-modus.
    """
    db_path = config["db-path"]
    conn = sqlite3.connect(db_path)  # Fjern "mode=ro" for å tillate skriving
    return conn

def fetch_from_db(query, params=()):
    """
    Utfør en SQL-spørring og returner resultatene.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    return results

def execute_db(query, params=()):
    """
    Utfør en SQL-spørring som krever skrivetilgang.
    """
    conn = get_db_connection_rw()
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()  # Husk å lagre endringene
    conn.close()

# Hjelpefunksjon: Hent dager med detections for en gitt måned
def get_detection_days(year, month):
    start_date = f"{year}-{month:02d}-01"
    end_date = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=31)).replace(day=1).strftime("%Y-%m-%d")
    
    query = '''
        SELECT DISTINCT DATE(timestamp) as detection_date
        FROM detections
        WHERE DATE(timestamp) BETWEEN ? AND ?
    '''
    results = fetch_from_db(query, (start_date, end_date))
    return [row[0] for row in results]


# Hjelpefunksjon: Hent detections for en gitt dato
def get_detections_for_date(date, min_conf):
    query = '''
        SELECT scientific_name, strftime('%H', timestamp) as hour
        FROM detections
        WHERE DATE(timestamp) = ? AND confidence >= ?
    '''
    results = fetch_from_db(query, (date, min_conf))
    return [(row[0], int(row[1])) for row in results]


# Hjelpefunksjon: Hent detaljer for en art på en gitt dato
def get_species_details(date, scientific_name, hour=None):
    if hour is not None:
        query = '''
            SELECT DISTINCT timestamp as formatted_timestamp, 
                            recording, 
                            start_time, 
                            end_time
            FROM detections
            WHERE DATE(timestamp) = ? AND scientific_name = ? AND strftime('%H', timestamp) = ?
            ORDER BY formatted_timestamp
        '''
        params = (date, scientific_name, f"{hour:02d}")
    else:
        query = '''
            SELECT DISTINCT timestamp as formatted_timestamp, 
                            recording, 
                            start_time, 
                            end_time
            FROM detections
            WHERE DATE(timestamp) = ? AND scientific_name = ?
            ORDER BY formatted_timestamp
        '''
        params = (date, scientific_name)

    return fetch_from_db(query, params)


# "/" - Vis månedskalender
@app.get("/", response_class=HTMLResponse)
async def calendar_view(request: Request, year: int = None, month: int = None):
    if year is None or month is None:
        today = datetime.today()
        year, month = today.year, today.month

    # Beregn forrige og neste måned
    first_day = datetime(year, month, 1)
    prev_month = (first_day - timedelta(days=1)).replace(day=1)
    next_month = (first_day + timedelta(days=31)).replace(day=1)

    # Hent dager med detections
    detection_days = get_detection_days(year, month)

    # Generer kalenderdata
    last_day = (first_day + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    calendar = []
    current_day = first_day

    # Juster for ukens første dag
    start_weekday = first_day.weekday()  # 0 = mandag, 6 = søndag
    if start_weekday > 0:
        calendar.extend([None] * start_weekday)  # Fyll tomme celler før månedens første dag

    while current_day <= last_day:
        day_str = current_day.strftime("%Y-%m-%d")
        calendar.append({
            "day": current_day.day,
            "has_detections": day_str in detection_days,
            "link": f"/show_detections?date={day_str}" if day_str in detection_days else None
        })
        current_day += timedelta(days=1)

    # Fyll tomme celler etter månedens siste dag
    end_weekday = last_day.weekday()  # 0 = mandag, 6 = søndag
    if end_weekday < 6:
        calendar.extend([None] * (6 - end_weekday))

    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "year": year,
        "month": month,
        "calendar": calendar,
        "prev_month": {"year": prev_month.year, "month": prev_month.month},
        "next_month": {"year": next_month.year, "month": next_month.month},
        "title": "Dager med lydregistrering av fugler"
    })

# "/show_detections" - Vis arter for en gitt dato
@app.get("/show_detections", response_class=HTMLResponse)
async def show_detections(request: Request, date: str, min_conf: float = 0.8):
    detections = get_detections_for_date(date, min_conf)

    # Organiser detections etter art og time
    species_histogram = defaultdict(lambda: [0] * 24)
    for scientific_name, hour in detections:
        species_histogram[scientific_name][hour] += 1

    # Map vitenskapelige navn til norske navn og sorter etter totalt antall registreringer
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

    # Sorter etter totalt antall registreringer i synkende rekkefølge
    species_data.sort(key=lambda x: x["total_count"], reverse=True)

    return templates.TemplateResponse("show_detections.html", {
        "request": request,
        "date": date,
        "species_data": species_data,
        "min_conf": min_conf,
        "title": f"Deteksjoner for {date}"
    })

# "/species_details" - Vis registreringstidspunkter for en art på en gitt dato, med valgfri timefilter
@app.get("/species_details", response_class=HTMLResponse)
async def species_details(request: Request, scientific_name: str, date: str, hour: int = None):
    # Konverter understrek til mellomrom
    scientific_name = scientific_name.replace("_", " ")

    # Hent data fra databasen
    rows = get_species_details(date, scientific_name, hour)

    # Generer detections-listen med sjekk for lydfil
    detections = []
    for row in rows:
        formatted_timestamp = row[0]
        recording = row[1]
        start_time = row[2]
        end_time = row[3]

        # Sjekk om lydfilen eksisterer
        audio_file_path = os.path.join(config["audio-path"], recording) if recording else None
        if audio_file_path and os.path.isfile(audio_file_path):
            audio_file = f"/audio/{recording}"
        else:
            audio_file = None
            recording = None

        # Legg til tidsrom kun hvis start_time og end_time er tilgjengelige
        if start_time is not None and end_time is not None:
            start_time_display = round(start_time, 1)
            end_time_display = round(end_time, 1)
        else:
            start_time_display = None
            end_time_display = None

        detections.append({
            "timestamp": formatted_timestamp,
            "recording": recording,
            "audio_file": audio_file,
            "start_time": start_time_display,
            "end_time": end_time_display
        })

    # Hent norsk navn for arten
    common_name = species_mapping.get(scientific_name, "Ukjent")

    return templates.TemplateResponse("species_day_details.html", {
        "request": request,
        "scientific_name": scientific_name,
        "common_name": common_name,
        "date": date,
        "hour": hour,
        "detections": detections,
        "total_detections": len(detections),
        "title": f"Detaljer for {common_name} ({scientific_name})"
    })

# Funksjon for å generere JWT-token
def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": time.time() + config["access_token_expire_seconds"]})
    return jwt.encode(to_encode, config["secret_key"], algorithm=config["secret_algorithm"])

# Funksjon for å verifisere JWT-token
def verify_token(token: str):
    try:
        payload = jwt.decode(token, config["secret_key"], algorithms=config["secret_algorithm"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Ugyldig eller utløpt token")

# Funksjon for å verifisere passord med bcrypt
def verify_password(password: str):
    with open(config["password_hash_file"], "r") as file:
        stored_hash = file.read().strip().encode("utf-8")  # Les hashen og konverter til bytes
    return checkpw(password.encode("utf-8"), stored_hash)  # Sammenlign passordet med hashen

# Rute for "/species_detections_admin"
@app.get("/species_detections_admin", response_class=HTMLResponse)
async def species_detections_admin(
    request: Request,
    scientific_name: str,
    date: str,
    token: str = None  # Token hentes manuelt fra cookies
):
    # Sjekk om token er gyldig
    if not token:
        token = request.cookies.get("access_token")
    if not token or not verify_token(token):
# Hvis token mangler eller er ugyldig, vis passord-dialogen
        return templates.TemplateResponse("password_prompt.html", {"request": request, "scientific_name": scientific_name, "date": date})

    # Hent data fra databasen
    query = '''
        SELECT timestamp, recording, start_time, end_time, confidence
        FROM detections
        WHERE DATE(timestamp) = ? AND scientific_name = ?
        ORDER BY timestamp ASC, start_time ASC
    '''
    rows = fetch_from_db(query, (date, scientific_name.replace("_", " ")))

    # Konverter rader til en liste med ordbøker
    detections = [
        {
            "timestamp": row[0],
            "recording": row[1],
            "start_time": row[2],
            "end_time": row[3],
            "confidence": row[4],
        }
        for row in rows
    ]

    return templates.TemplateResponse("species_detections_admin.html", {
        "request": request,
        "scientific_name": species_mapping.get(scientific_name.replace("_", " ")),
        "date": date,
        "detections": detections,
        "title": f"Administrer deteksjoner for {species_mapping.get(scientific_name.replace("_", " "))} {date}"
    })


@app.post("/archive_false_positives")
async def archive_false_positives(
    request: Request,
    scientific_name: str = Form(...),
    date: str = Form(...),
    false_positive_ids: list[str] = Form(...)
):
    # Hent token fra cookies
    token = request.cookies.get("access_token")
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Flytt merkede deteksjoner til false_positives-tabellen
    query_insert = '''
        INSERT INTO false_positives (timestamp, recording, start_time, end_time, confidence, scientific_name)
        SELECT timestamp, recording, start_time, end_time, confidence, scientific_name
        FROM detections
        WHERE timestamp = ? AND scientific_name = ?
    '''
    query_delete = '''
        DELETE FROM detections
        WHERE timestamp = ? AND scientific_name = ?
    '''
    for detection_id in false_positive_ids:
        execute_db(query_insert, (detection_id, scientific_name))
        execute_db(query_delete, (detection_id, scientific_name))

    return RedirectResponse(url=f"/species_detections_admin?scientific_name={scientific_name}&date={date}", status_code=303)

# Rute for passordautentisering
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

@app.post("/delete_all_detections")
async def delete_all_detections(
    request: Request,
    scientific_name: str = Form(...),
    date: str = Form(...)
):
    # Hent token fra cookies
    token = request.cookies.get("access_token")
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Slett alle deteksjoner for arten på den angitte datoen
    query = '''
        DELETE FROM detections
        WHERE scientific_name = ? AND DATE(timestamp) = ?
    '''
    execute_db(query, (scientific_name, date))
    return RedirectResponse(url=f"/species_detections_admin?scientific_name={scientific_name}&date={date}", status_code=303)

@app.post("/delete_detection")
async def delete_detection(timestamp: str = Form(...), token: str = Depends(OAuth2PasswordBearer(tokenUrl="token"))):
    verify_token(token)
    query = '''
        DELETE FROM detections
        WHERE timestamp = ?
    '''
    fetch_from_db(query, (timestamp,))
    return RedirectResponse(url=f"/species_detections_admin", status_code=303)