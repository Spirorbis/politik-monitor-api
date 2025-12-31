from flask import Flask, jsonify
import requests
from datetime import datetime
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

app = Flask(__name__)

# --- KONFIGURATION ---
DIP_API_URL = "https://search.dip.bundestag.de/api/v1/vorgang"
BREG_RSS_URL = "https://www.bundesregierung.de/service/rss/breg-de/pressemitteilungen"
BVERFG_RSS_URL = "https://www.bundesverfassungsgericht.de/SiteGlobals/Functions/RSS/Entscheidungen/RSS_Entscheidungen_Aktuell.xml"

API_KEY = os.environ.get("BUNDESTAG_API_KEY") 

# --- HELFER (MAPPING) ---
def map_bundestag_status(vorgang_status):
    st = str(vorgang_status).lower()
    if "verkündet" in st or "bundesgesetzblatt" in st or "verkuendet" in st: return "published"
    elif "in kraft" in st: return "effective"
    elif "unterzeichnet" in st or "ausgefertigt" in st: return "signed"
    elif "bundesrat" in st and "zugestimmt" in st: return "passedBundesrat"
    elif "beschlossen" in st or "angenommen" in st or "verabschiedet" in st: return "passedBundestag"
    elif "abgelehnt" in st or "erledigt" in st or "zurückgezogen" in st or "nicht zustande gekommen" in st: return "stopped"
    elif "beratung" in st: return "committee"
    elif "ausschuss" in st or "überwiesen" in st or "überweisung" in st or "zuweisung" in st: return "committee"
    elif "bundesrat" in st: return "committee" # Nur Zuleitung
    elif "beschlussempfehlung" in st or "bericht" in st: return "committee"
    elif "änderungsantrag" in st or "entschließungsantrag" in st: return "committee"
    elif "antwort" in st: return "committee" 
    else: return "draft"

def map_category(text):
    t = str(text).lower()
    if "wirtschaft" in t or "finanzen" in t or "haushalt" in t or "steuer" in t: return "economy"
    elif "umwelt" in t or "klima" in t or "energie" in t or "natur" in t: return "environment"
    elif "arbeit" in t or "soziales" in t or "rente" in t or "familie" in t: return "social"
    elif "medien" in t or "digital" in t or "kultur" in t or "internet" in t: return "digital"
    elif "recht" in t or "innere" in t or "polizei" in t or "sicherheit" in t: return "justice"
    elif "verteidigung" in t or "außen" in t or "bundeswehr" in t or "krieg" in t: return "defense"
    elif "gesundheit" in t or "sport" in t or "medizin" in t or "pflege" in t: return "health"
    else: return "other"

def map_type_bundestag(vorgangstyp):
    vt = str(vorgangstyp).lower()
    if "gesetz" in vt: return "bill"
    elif "verordnung" in vt: return "ordinance"
    elif "antrag" in vt: return "motion"
    else: return "bill"

# --- QUELLEN ---

def fetch_bundestag():
    if not API_KEY: return []
    try:
        params = { "f.vorgangstyp": "Gesetzgebung", "format": "json", "limit": 10, "sort": "-aktualisiert" }
        headers = { "Authorization": f"ApiKey {API_KEY}" }
        resp = requests.get(DIP_API_URL, params=params, headers=headers)
        if resp.status_code != 200: return []
        
        items = []
        for doc in resp.json().get("documents", []):
            datum_str = doc.get("datum", "2025-01-01")
            
            # Status Logic
            status_raw = doc.get("beratungsstand", "")
            if not status_raw: status_raw = doc.get("vorgangsstatus", "")
            if not status_raw: status_raw = doc.get("aktueller_stand", "Entwurf")

            # Title Logic
            raw_title = doc.get("titel", "Ohne Titel")
            simple_title = raw_title
            match = re.search(r'\((.*?gesetz.*?)\)', raw_title, re.IGNORECASE)
            if match: simple_title = match.group(1)
            if len(simple_title) > 100 and simple_title == raw_title: simple_title = raw_title[:97] + "..."

            item = {
                "id": f"bt-{doc.get('id', '0')}",
                "officialTitle": raw_title,
                "simpleTitle": simple_title,
                "summary": doc.get("abstract", "Keine Zusammenfassung."),
                "institution": "bundestag",
                "type": map_type_bundestag(doc.get("vorgangstyp", "")),
                "category": map_category(doc.get("sachgebiet", [])),
                "datePublished": f"{datum_str}T09:00:00Z", 
                "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": map_bundestag_status(status_raw),
                "progress": 0.5,
                "isBookmarked": False,
                "voteResult": None
            }
            items.append(item)
        return items
    except: return []

def fetch_bundesregierung():
    try:
        resp = requests.get(BREG_RSS_URL, timeout=5)
        if resp.status_code != 200: return []
        
        root = ET.fromstring(resp.content)
        items = []
        
        # XML Namespace Handling ist in Python manchmal tricky, wir iterieren simpel
        for entry in root.findall('./channel/item')[:5]: # Nur die neuesten 5
            title = entry.find('title').text if entry.find('title') is not None else "Ohne Titel"
            desc = entry.find('description').text if entry.find('description') is not None else ""
            
            # Datum parsen (RFC 822 Format)
            pub_date_str = entry.find('pubDate').text
            try:
                dt = parsedate_to_datetime(pub_date_str)
                iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except:
                iso_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

            # HTML Tags aus Beschreibung entfernen
            clean_desc = re.sub('<[^<]+?>', '', desc)[:200] + "..."

            item = {
                "id": f"breg-{hash(title)}",
                "officialTitle": title,
                "simpleTitle": title,
                "summary": clean_desc,
                "institution": "bundesregierung",
                "type": "motion", # Pressemitteilungen sind meist Initiativen/Ankündigungen
                "category": map_category(title + " " + desc),
                "datePublished": iso_date,
                "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "published", # Pressemitteilungen sind "veröffentlicht"
                "progress": 1.0,
                "isBookmarked": False,
                "voteResult": None
            }
            items.append(item)
        return items
    except Exception as e:
        print(f"Error BREG: {e}")
        return []

def fetch_bverfg():
    try:
        resp = requests.get(BVERFG_RSS_URL, timeout=5)
        if resp.status_code != 200: return []
        
        root = ET.fromstring(resp.content)
        items = []
        
        for entry in root.findall('./channel/item')[:5]:
            title = entry.find('title').text
            desc = entry.find('description').text
            pub_date_str = entry.find('pubDate').text
            
            try:
                dt = parsedate_to_datetime(pub_date_str)
                iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except:
                iso_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

            item = {
                "id": f"bverfg-{hash(title)}",
                "officialTitle": title,
                "simpleTitle": "Urteil / Entscheidung", # Oft sind Titel beim Gericht Aktenzeichen
                "summary": title, # Beim BVerfG ist der Titel oft die Zusammenfassung
                "institution": "bundesverfassungsgericht",
                "type": "ruling",
                "category": map_category(title),
                "datePublished": iso_date,
                "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "effective", # Urteile sind sofort wirksam
                "progress": 1.0,
                "isBookmarked": False,
                "voteResult": None
            }
            items.append(item)
        return items
    except Exception as e:
        print(f"Error BVerfG: {e}")
        return []

# --- MAIN ROUTE ---
@app.route('/api/policies')
def get_policies():
    try:
        # 1. Daten aus allen Quellen holen
        bt_items = fetch_bundestag()
        breg_items = fetch_bundesregierung()
        bverfg_items = fetch_bverfg()
        
        # 2. Zusammenfügen
        all_items = bt_items + breg_items + bverfg_items
        
        # 3. Sortieren (Neuestes oben)
        # Wir müssen sicherstellen, dass das Datum parslich ist, sonst Fallback
        all_items.sort(key=lambda x: x['datePublished'], reverse=True)
        
        return jsonify(all_items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
