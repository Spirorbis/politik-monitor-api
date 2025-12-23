from flask import Flask, jsonify
import requests
from datetime import datetime
import os

app = Flask(__name__)

# --- KONFIGURATION ---
DIP_API_URL = "https://search.dip.bundestag.de/api/v1/vorgang"
# Wir holen den Key sicher aus den Umgebungsvariablen (erkläre ich gleich)
API_KEY = os.environ.get("BUNDESTAG_API_KEY") 

# --- HELFER (MAPPING) ---
def map_status(vorgang_status):
    st = str(vorgang_status).lower()
    if "verkündet" in st: return "published"
    elif "in kraft" in st: return "effective"
    elif "unterzeichnet" in st: return "signed"
    elif "bundesrat" in st and "zugestimmt" in st: return "passedBundesrat"
    elif "beschlossen" in st or "angenommen" in st: return "passedBundestag"
    elif "beratung" in st: return "committee"
    elif "eingebracht" in st: return "draft"
    elif "erledigt" in st or "abgelehnt" in st: return "stopped"
    else: return "draft"

def map_category(sachgebiet_liste):
    if not sachgebiet_liste: return "other"
    haupt = str(sachgebiet_liste[0]).lower()
    if "wirtschaft" in haupt or "finanzen" in haupt: return "economy"
    elif "umwelt" in haupt or "klima" in haupt: return "environment"
    elif "arbeit" in haupt or "soziales" in haupt: return "social"
    elif "medien" in haupt or "digital" in haupt: return "digital"
    elif "recht" in haupt or "innere" in haupt: return "justice"
    elif "verteidigung" in haupt: return "defense"
    elif "gesundheit" in haupt: return "health"
    else: return "other"

def map_type(vorgangstyp):
    vt = str(vorgangstyp).lower()
    if "gesetz" in vt: return "bill"
    elif "verordnung" in vt: return "ordinance"
    elif "antrag" in vt: return "motion"
    else: return "bill"

# --- ROUTE ---
@app.route('/api/policies') # Vercel leitet Anfragen hierhin
def get_policies():
    try:
        # Parameter für die Bundestag API
        params = {
            "f.vorgangstyp": "Gesetzgebung",
            "format": "json",
            "limit": 15,
            "sort": "-aktualisiert",
            "apikey": API_KEY
        }
        
        # Abfrage
        response = requests.get(DIP_API_URL, params=params)
        response.raise_for_status() # Wirft Fehler bei schlechtem Status
        data = response.json()
        
        swift_items = []
        
        # Verarbeitung
        for doc in data.get("documents", []):
            datum_str = doc.get("datum", "2024-01-01")
            
            # Titel und Abstract bereinigen
            titel = doc.get("titel", "Ohne Titel")
            abstract = doc.get("abstract", "")
            if not abstract: abstract = "Keine Zusammenfassung verfügbar."
            
            item = {
                "id": doc.get("id", "unknown"),
                "officialTitle": titel,
                "simpleTitle": titel, 
                "summary": abstract,
                "institution": "bundestag",
                "type": map_type(doc.get("vorgangstyp", "")),
                "category": map_category(doc.get("sachgebiet", [])),
                "datePublished": f"{datum_str}T09:00:00Z", 
                "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": map_status(doc.get("aktueller_stand", "")),
                "progress": 0.5,
                "isBookmarked": False,
                "voteResult": None
            }
            swift_items.append(item)
            
        return jsonify(swift_items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Fallback für lokale Tests
if __name__ == '__main__':
    app.run(debug=True)
