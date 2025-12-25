from flask import Flask, jsonify
import requests
from datetime import datetime
import os

app = Flask(__name__)

# --- KONFIGURATION ---
DIP_API_URL = "https://search.dip.bundestag.de/api/v1/vorgang"
# Wir holen den Key aus den Umgebungsvariablen von Vercel
API_KEY = os.environ.get("BUNDESTAG_API_KEY") 

# --- HELFER (MAPPING) ---
def map_status(vorgang_status):
    st = str(vorgang_status).lower()
    
    # 1. Fertige Gesetze (Final)
    if "verkündet" in st or "bundesgesetzblatt" in st: 
        return "published"
    elif "in kraft" in st: 
        return "effective"
    elif "unterzeichnet" in st or "ausgefertigt" in st: 
        return "signed"
    
    # 2. Beschlüsse (Fortgeschritten)
    elif "zugestimmt" in st or "bundesrat" in st: 
        return "passedBundesrat"
    elif "beschlossen" in st or "angenommen" in st or "verabschiedet" in st: 
        return "passedBundestag"
    elif "abgelehnt" in st or "erledigt" in st or "zurückgezogen" in st or "nicht zustande gekommen" in st: 
        return "stopped"
        
    # 3. Arbeitsprozess (Ausschüsse & Beratungen)
    elif "beratung" in st: # Erste Beratung, Zweite Beratung...
        return "committee"
    elif "ausschuss" in st or "überwiesen" in st or "überweisung" in st: 
        return "committee"
    elif "beschlussempfehlung" in st or "bericht" in st: 
        return "committee"
    elif "änderungsantrag" in st or "entschließungsantrag" in st:
        return "committee"
    elif "antwort" in st: # Bei Kleinen Anfragen oft "Beantwortet"
        return "committee" 
        
    # 4. Alles andere ist ein Entwurf / Vorlage
    else: 
        return "draft"

def map_category(sachgebiet_liste):
    if not sachgebiet_liste: return "other"
    haupt = str(sachgebiet_liste[0]).lower()
    
    if "wirtschaft" in haupt or "finanzen" in haupt or "haushalt" in haupt: return "economy"
    elif "umwelt" in haupt or "klima" in haupt or "energie" in haupt: return "environment"
    elif "arbeit" in haupt or "soziales" in haupt: return "social"
    elif "medien" in haupt or "digital" in haupt or "kultur" in haupt: return "digital"
    elif "recht" in haupt or "innere" in haupt: return "justice"
    elif "verteidigung" in haupt or "außen" in haupt: return "defense"
    elif "gesundheit" in haupt or "sport" in haupt: return "health"
    else: return "other"

def map_type(vorgangstyp):
    vt = str(vorgangstyp).lower()
    if "gesetz" in vt: return "bill"
    elif "verordnung" in vt: return "ordinance"
    elif "antrag" in vt: return "motion"
    else: return "bill"

# --- ROUTE ---
@app.route('/api/policies')
def get_policies():
    try:
        # Prüfung ob API Key vorhanden ist
        if not API_KEY:
            return jsonify({"error": "API Key fehlt auf dem Server (Environment Variable prüfen)"}), 500

        # Parameter für die Bundestag API
        params = {
            "f.vorgangstyp": "Gesetzgebung", # Wir filtern nur Gesetze, keine kleinen Anfragen etc.
            "format": "json",
            "limit": 20, # Wir laden etwas mehr, um bessere Treffer zu haben
            "sort": "-aktualisiert"
        }
        
        # Authentifizierung im Header (Wichtig für 401 Fehler Vermeidung)
        headers = {
            "Authorization": f"ApiKey {API_KEY}"
        }
        
        # Abfrage starten
        response = requests.get(DIP_API_URL, params=params, headers=headers)
        
        if response.status_code == 401:
             return jsonify({"error": "Bundestag API Key abgelehnt (401)"}), 401
             
        response.raise_for_status()
        data = response.json()
        
        swift_items = []
        
        # Verarbeitung der Dokumente
        for doc in data.get("documents", []):
            datum_str = doc.get("datum", "2024-01-01")
            
            titel = doc.get("titel", "Ohne Titel")
            abstract = doc.get("abstract", "")
            if not abstract: abstract = "Keine Zusammenfassung verfügbar."
            
            # Status Mapping aufrufen
            status_raw = doc.get("aktueller_stand", "")
            mapped_status = map_status(status_raw)
            
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
                "status": mapped_status,
                # Progress wird in Swift berechnet, hier Platzhalter
                "progress": 0.5, 
                "isBookmarked": False,
                "voteResult": None
            }
            swift_items.append(item)
            
        return jsonify(swift_items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
