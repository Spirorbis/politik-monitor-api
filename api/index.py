from flask import Flask, jsonify
import requests
from datetime import datetime
import os

app = Flask(__name__)

# --- KONFIGURATION ---
DIP_API_URL = "https://search.dip.bundestag.de/api/v1/vorgang"
API_KEY = os.environ.get("BUNDESTAG_API_KEY") 

# --- HELFER (MAPPING) ---
def map_status(vorgang_status):
    st = str(vorgang_status).lower()
    
    # --- 1. FINAL / ABGESCHLOSSEN ---
    if "verkündet" in st or "bundesgesetzblatt" in st or "verkuendet" in st: 
        return "published"
    elif "in kraft" in st: 
        return "effective"
    elif "unterzeichnet" in st or "ausgefertigt" in st: 
        return "signed"
    elif "abgelehnt" in st or "erledigt" in st or "zurückgezogen" in st or "nicht zustande gekommen" in st: 
        return "stopped"

    # --- 2. BESCHLÜSSE (Meilensteine) ---
    # WICHTIG: Nur wenn explizit "zugestimmt" dabei steht!
    elif "bundesrat" in st and "zugestimmt" in st: 
        return "passedBundesrat"
    # Bundestag beschlossen
    elif "bundestag" in st and ("angenommen" in st or "beschlossen" in st or "verabschiedet" in st): 
        return "passedBundestag"
    elif "zweite beratung" in st and "dritte beratung" in st: # Oft gleichbedeutend mit Abschluss im BT
        return "passedBundestag"

    # --- 3. IN ARBEIT (Ausschuss / Beratung / Zuleitung) ---
    # Alles was "zugeleitet", "überwiesen" oder "beraten" wird
    elif "beratung" in st: return "committee"
    elif "ausschuss" in st: return "committee"
    elif "überwiesen" in st or "überweisung" in st: return "committee"
    elif "beschlussempfehlung" in st or "bericht" in st: return "committee"
    elif "änderungsantrag" in st or "entschließungsantrag" in st: return "committee"
    elif "antwort" in st: return "committee"
    # Hier fangen wir "Dem Bundesrat zugeleitet" ab -> Das ist Arbeitsprozess (Gelb)
    elif "zugeleitet" in st or "zuleitung" in st: return "committee"
    elif "vorlage" in st: return "committee"
        
    # --- 4. START (Entwurf) ---
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
        if not API_KEY: return jsonify({"error": "API Key fehlt"}), 500

        params = {
            "f.vorgangstyp": "Gesetzgebung",
            "format": "json",
            "limit": 20,
            "sort": "-aktualisiert"
        }
        
        headers = { "Authorization": f"ApiKey {API_KEY}" }
        
        response = requests.get(DIP_API_URL, params=params, headers=headers)
        if response.status_code == 401: return jsonify({"error": "Unauthorized"}), 401
        response.raise_for_status()
        data = response.json()
        
        swift_items = []
        
        for doc in data.get("documents", []):
            datum_str = doc.get("datum", "2024-01-01")
            
            # --- STATUS DETEKTIV ---
            # Wir prüfen der Reihe nach, wo der Status steht
            status_raw = doc.get("beratungsstand", "")
            if not status_raw: status_raw = doc.get("vorgangsstatus", "")
            if not status_raw: status_raw = doc.get("aktueller_stand", "Entwurf")
            
            # Titel sauber auslesen
            titel = doc.get("titel", "Ohne Titel")
            
            item = {
                "id": doc.get("id", "unknown"),
                "officialTitle": titel, # Voller Titel
                "simpleTitle": titel,   # Vor erst identisch (Kürzung bräuchte KI)
                "summary": doc.get("abstract", "Keine Zusammenfassung."),
                "institution": "bundestag",
                "type": map_type(doc.get("vorgangstyp", "")),
                "category": map_category(doc.get("sachgebiet", [])),
                "datePublished": f"{datum_str}T09:00:00Z", 
                "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": map_status(status_raw),
                "progress": 0.5, # Wird in Swift berechnet
                "isBookmarked": False,
                "voteResult": None
            }
            swift_items.append(item)
            
        return jsonify(swift_items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
