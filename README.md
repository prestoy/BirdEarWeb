# BirdEarWeb 🐦

Nettbasert visningsgrensesnitt for lydregistreringer av fugler. BirdEarWeb er en FastAPI-applikasjon som lar deg bla gjennom, lytte til og administrere automatiske fugledeteksjoner lagret i en SQLite-database.

---

## Funksjoner

- **Kalendervisning** – Se hvilke dager som har fugleregistreringer
- **Dagsvisning** – Oversikt over registrerte arter per dag med histogram over tidspunkt (time for time)
- **Artsdetaljer** – Vis individuelle lydklipp for en art på en gitt dag, med mulighet for timefiltrering
- **Konfidensfiltrering** – Filtrer deteksjoner basert på konfidensterskel
- **Norske artsnavn** – Automatisk oversetting fra vitenskapelige navn via NNKF-artsliste
- **Admin-grensesnitt** – Passordbeskytt administrasjon for arkivering av arter og deteksjoner
- **JWT-autentisering** – Sikker innlogging med token-basert sesjonshåndtering

---

## Teknologi

| Komponent | Teknologi |
|---|---|
| Webramme | [FastAPI](https://fastapi.tiangolo.com/) |
| Maler | Jinja2 |
| Database | SQLite (read-only for visning, read-write for admin) |
| Autentisering | JWT (python-jose) + bcrypt |
| Artsliste | NNKF-CSV (norske fugle artsnavn) |

---

## Forutsetninger

- Python 3.10+
- Norsk locale (`nb_NO.UTF-8`) installert på systemet
- En SQLite-database med fugledeteksjoner (kompatibel med f.eks. [BirdNET-Analyzer](https://github.com/kahst/BirdNET-Analyzer))
- Lydklipp tilgjengelig i en katalog

---

## Installasjon

### 1. Klon repoet

```bash
git clone https://github.com/prestoy/BirdEarWeb.git
cd BirdEarWeb
```

### 2. Installer avhengigheter

```bash
pip install -r requirements.txt
pip install fastapi uvicorn pyyaml
```

> **Merk:** `requirements.txt` inneholder kjerneavhengigheter. `fastapi`, `uvicorn` og `pyyaml` må installeres separat.

### 3. Konfigurer applikasjonen

Rediger `config.yaml` med dine egne stier og innstillinger:

```yaml
# Sti til SQLite-databasen
db-path: "/sti/til/birdmic_detections.db"

# Sti til CSV-filen med norske artsnavn
species-map: "kilder/nnkf_komplett.20241111.csv"

# Sti til lydklippene
audio-path: "/sti/til/audioarkiv"

# Påloggingskonfigurasjon
password_hash_file: "pwd_hash.txt"
secret_key: "din-hemmelige-nøkkel"   # Bytt til en tilfeldig, sikker streng
secret_algorithm: "HS256"
access_token_expire_seconds: 86400    # 1 dag
```

### 4. Sett opp locale

Sjekk om norsk _locale_ er installert

```
locale -a | grep nb
```

Installer locale hvis det ikke finnes.

```
sudo locale-gen nb_NO.UTF-8
sudo update-locale
```


### 4. Sett opp adminpassord

Generer en bcrypt-hash av passordet ditt og lagre det i `pwd_hash.txt`:

```python
import bcrypt
hashed = bcrypt.hashpw(b"dittpassord", bcrypt.gensalt())
print(hashed.decode())
```

Lim inn outputen i `pwd_hash.txt`.

### 5. Start applikasjonen

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Åpne nettleseren på `http://localhost:8000`.

---

## Mappestruktur

```
BirdEarWeb/
├── main.py                  # Hovedapplikasjon (FastAPI)
├── config.yaml              # Konfigurasjon
├── requirements.txt         # Python-avhengigheter
├── pwd_hash.txt             # Bcrypt-hash for adminpassord
├── kilder/
│   └── nnkf_komplett.*.csv  # Norsk artsliste (vitenskapelig → norsk navn)
└── web-maler/               # Jinja2 HTML-maler
    ├── base.html
    ├── calendar.html
    ├── show_detections.html
    ├── species_day_details.html
    ├── species_admin.html
    ├── species_detections_admin.html
    ├── confirmation_prompt.html
    └── password_prompt.html
```

---

## API-endepunkter

| Metode | Endepunkt | Beskrivelse |
|---|---|---|
| GET | `/` | Kalendervisning med månedsoversikt |
| GET | `/show_detections?date=YYYY-MM-DD` | Deteksjoner for en gitt dato |
| GET | `/species_details?scientific_name=...&date=...` | Detaljer og lydklipp for en art |
| POST | `/authenticate` | Innlogging til adminpanel |
| GET | `/species_admin` | Administrer arter (krever innlogging) |
| POST | `/species_admin/archive` | Arkiver en art |
| GET | `/species_detections_admin` | Administrer deteksjoner |
| POST | `/species_detections_admin/archive` | Arkiver deteksjoner |

---

## Database

Applikasjonen forventer en SQLite-database med en tabell kalt `detections` med følgende kolonner:

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `timestamp` | TEXT | Tidsstempel for deteksjonen (ISO 8601) |
| `scientific_name` | TEXT | Vitenskapelig artsnavn |
| `confidence` | REAL | Konfidensscore (0.0–1.0) |
| `recording` | TEXT | Filsti til lydklipp |
| `start_time` | REAL | Startposisjon i lydklipp (sekunder) |
| `end_time` | REAL | Sluttposisjon i lydklipp (sekunder) |

---

## Sikkerhet

- Admin-grensesnittet er beskyttet med bcrypt-hashet passord og JWT-tokens
- Databasetilgang er read-only for visningssider
- **Viktig:** Bytt ut `secret_key` i `config.yaml` med en tilfeldig, sterk nøkkel før produksjonsbruk

---

## Lisens

Ikke spesifisert. Ta kontakt med prosjekteier for bruksrettigheter.
