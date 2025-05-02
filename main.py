import yaml  # For å lese config.yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
import sqlite3
import csv
from collections import defaultdict
import os

# Les config.yaml
def load_config(config_path="config.yaml"):
    with open(config_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)

config = load_config()

app = FastAPI()

# Sett opp Jinja2-templates
templates = Jinja2Templates(directory="web-maler")

# Legg til Jinja2-filter for datoformatering
def datetimeformat(value, format='%d. %b %Y'):
    try:
        # Håndter ISO 8601-tidsstempler med mikrosekunder
        formatted_date = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f').strftime(format)
    except ValueError:
        try:
            # Håndter tidsstempler uten mikrosekunder
            formatted_date = datetime.strptime(value, '%Y-%m-%d %H:%M:%S').strftime(format)
        except ValueError:
            # Håndter datoer uten tid
            formatted_date = datetime.strptime(value, '%Y-%m-%d').strftime(format)
    
    # Konverter månedsnavn til små bokstaver hvis det finnes
    parts = formatted_date.split()
    if len(parts) > 1:  # Sjekk om det finnes et månedsnavn
        formatted_date = formatted_date.replace(parts[1], parts[1].lower())
    
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

# Hjelpefunksjon: Hent dager med detections for en gitt måned
def get_detection_days(year, month):
    conn = sqlite3.connect(config["db-path"])
    cursor = conn.cursor()
    
    # Beregn start- og sluttdato for måneden
    start_date = f"{year}-{month:02d}-01"
    end_date = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=31)).replace(day=1).strftime("%Y-%m-%d")
    
    # Hent unike datoer fra start_time i detections-tabellen
    cursor.execute('''
        SELECT DISTINCT DATE(start_time, 'unixepoch') as detection_date
        FROM detections
        WHERE DATE(start_time, 'unixepoch') BETWEEN ? AND ?
    ''', (start_date, end_date))
    
    days = [row[0] for row in cursor.fetchall()]
    conn.close()
    return days

# Hjelpefunksjon: Hent detections for en gitt dato
def get_detections_for_date(date, min_conf):
    conn = sqlite3.connect(config["db-path"])
    cursor = conn.cursor()
    cursor.execute('''
        SELECT scientific_name, strftime('%H', start_time, 'unixepoch') as hour
        FROM detections
        WHERE DATE(start_time, 'unixepoch') = ? AND confidence >= ?
    ''', (date, min_conf))
    detections = [(row[0], int(row[1])) for row in cursor.fetchall()]  # Konverter 'hour' til int
    conn.close()
    return detections

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
async def show_detections(request: Request, date: str, min_conf: float = 0.5):
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

    return templates.TemplateResponse("detections.html", {
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

    conn = sqlite3.connect(config["db-path"])
    cursor = conn.cursor()
    
    # Bygg SQL-spørring basert på om hour er angitt
    if hour is not None:
        cursor.execute('''
            SELECT DISTINCT strftime('%Y-%m-%d %H:%M:%S', start_time, 'unixepoch') as formatted_timestamp, 
                            recording, 
                            start_time, 
                            end_time
            FROM detections
            WHERE DATE(start_time, 'unixepoch') = ? AND scientific_name = ? AND strftime('%H', start_time, 'unixepoch') = ?
            ORDER BY formatted_timestamp
        ''', (date, scientific_name, f"{hour:02d}"))
    else:
        cursor.execute('''
            SELECT DISTINCT strftime('%Y-%m-%d %H:%M:%S', start_time, 'unixepoch') as formatted_timestamp, 
                            recording, 
                            start_time, 
                            end_time
            FROM detections
            WHERE DATE(start_time, 'unixepoch') = ? AND scientific_name = ?
            ORDER BY formatted_timestamp
        ''', (date, scientific_name))
    
    # Generer detections-listen med sjekk for lydfil
    detections = []
    for row in cursor.fetchall():
        formatted_timestamp = row[0]
        recording = row[1]
        start_time = row[2]
        end_time = row[3]

        # Sjekk om lydfilen eksisterer
        audio_file_path = os.path.join(config["audio-path"], recording) if recording else None
        if audio_file_path and os.path.isfile(audio_file_path):
            audio_file = f"/audio/{recording}"
        else:
            audio_file = None  # Sett til None hvis filen ikke finnes
            recording = None  # Fjern verdien for recording hvis filen ikke finnes
            start_time = None  # Sett start_time til None
            end_time = None    # Sett end_time til None

        detections.append({
            "timestamp": formatted_timestamp,
            "recording": recording,
            "audio_file": audio_file,
            "start_time": round(start_time - int(start_time), 1) if start_time else None,  # Rund av til én desimal
            "end_time": round(end_time - int(start_time), 1) if start_time and end_time else None  # Rund av til én desimal
        })
    conn.close()

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