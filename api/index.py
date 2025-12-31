from flask import Flask, jsonify
import requests
from datetime import datetime
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

app = Flask(__name__)

# --- NEUE KONFIGURATION (Aktualisierte URLs) ---
DIP_API_URL = "https://search.dip.bundestag.de/api/v1/vorgang"

# BREG: Wir nutzen jetzt den allgemeinen "Aktuelles" Feed, der ist stabiler
BREG_RSS_URL = "https://www.bundesregierung.de/service/rss/breg-de/aktuelles"

# BVerfG: Wir nutzen den Pressemitteilungs-Feed, da der Entscheidungs-Feed oft umzieht
BVERFG_RSS_URL = "https://www.bundesverfassungsgericht.de/SiteGlobals/Functions/RSS/Pressemitteilungen/RSS_Pressemitteilungen.xml"

API_KEY = os.environ.get("BUNDESTAG_API_KEY") 

RSS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

# --- HELFER ---

def create_error_item(source_name, error_msg):
    return {
        "id": f"error-{source_name}-{datetime.now().timestamp()}",
        "officialTitle": f"Fehler {source_name}",
        "simpleTitle": f"Ladefehler: {error_msg}",
        "summary": "Datenquelle nicht erreichbar.",
        "institution": "bundesregierung" if source_name == "Regierung" else "bundesverfassungsgericht",
        "type": "motion",
        "category": "other",
        "datePublished": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "stopped",
        "progress": 0.0,
        "isBookmarked": False,
        "voteResult": None
    }

def map_bundestag_status(vorgang_status):
    st = str(vorgang_status).lower()
    if "verkündet" in st or "bundesgesetzblatt" in st: return "published"
    elif "in kraft" in st: return "effective"
    elif "unterzeichnet" in st: return "signed"
    elif "bundesrat" in st and "zugestimmt" in st: return "passedBundesrat"
    elif "beschlossen" in st or "angenommen" in st or "verabschiedet" in st: return "passedBundestag"
    elif "abgelehnt" in st or "erledigt" in st: return "stopped"
    elif "beratung" in st or "ausschuss" in st: return "committee"
    else: return "draft"

def map_category(text):
    t = str(text).lower()
    if "wirtschaft" in t or "finan" in t: return "economy"
    elif "umwelt" in t or "klima" in t: return "environment"
    elif "sozial" in t or "rente" in t: return "social"
    elif "digital" in t: return "digital"
    elif "recht" in t or "innere" in t: return "justice"
    elif "verteidigung" in t: return "defense"
    elif "gesundheit" in t: return "health"
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
        params = { "f.vorgangstyp": "Gesetzgebung", "format": "json", "limit": 15, "sort": "-aktualisiert" }
        headers = { "Authorization": f"ApiKey {API_KEY}" }
        resp = requests.get(DIP_API_URL, params=params, headers=headers)
        
        if resp.status_code != 200: return [create_error_item("Bundestag", str(resp.status_code))]
        
        items = []
        for doc in resp.json().get("documents", []):
            datum_str = doc.get("datum", "2025-01-01")
            
            status_raw = doc.get("beratungsstand", "")
            if not status_raw: status_raw = doc.get("vorgangsstatus", "")
            if not status_raw: status_raw = doc.get("aktueller_stand", "Entwurf")

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
    except Exception as e:
        return [create_error_item("Bundestag", str(e))]

def fetch_bundesregierung():
    try:
        resp = requests.get(BREG_RSS_URL, headers=RSS_HEADERS, timeout=10)
        if resp.status_code != 200: return [create_error_item("Regierung", str(resp.status_code))]
        
        root = ET.fromstring(resp.content)
        items = []
        
        # Flexiblere Suche nach Items
        rss_items = root.findall('.//item')
        if not rss_items: return []

        for entry in rss_items[:4]:
            title = entry.find('title').text or "Ohne Titel"
            desc = entry.find('description').text or ""
            
            iso_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            pub_date = entry.find('pubDate')
            if pub_date is not None:
                try:
                    dt = parsedate_to_datetime(pub_date.text)
                    iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except: pass

            clean_desc = re.sub('<[^<]+?>', '', desc)[:200] + "..."

            item = {
                "id": f"breg-{hash(title)}",
                "officialTitle": title,
                "simpleTitle": title,
                "summary": clean_desc,
                "institution": "bundesregierung",
                "type": "motion",
                "category": map_category(title + " " + desc),
                "datePublished": iso_date,
                "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "published",
                "progress": 1.0,
                "isBookmarked": False,
                "voteResult": None
            }
            items.append(item)
        return items
    except Exception as e:
        return [create_error_item("Regierung", str(e))]

def fetch_bverfg():
    try:
        resp = requests.get(BVERFG_RSS_URL, headers=RSS_HEADERS, timeout=10)
        if resp.status_code != 200: return [create_error_item("Gericht", str(resp.status_code))]
        
        root = ET.fromstring(resp.content)
        items = []
        rss_items = root.findall('.//item')
        
        for entry in rss_items[:3]:
            title = entry.find('title').text or "Entscheidung"
            
            iso_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            pub_date = entry.find('pubDate')
            if pub_date is not None:
                try:
                    dt = parsedate_to_datetime(pub_date.text)
                    iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except: pass

            item = {
                "id": f"bverfg-{hash(title)}",
                "officialTitle": title,
                "simpleTitle": "Urteil / Pressemitteilung",
                "summary": title, 
                "institution": "bundesverfassungsgericht",
                "type": "ruling",
                "category": map_category(title),
                "datePublished": iso_date,
                "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "effective",
                "progress": 1.0,
                "isBookmarked": False,
                "voteResult": None
            }
            items.append(item)
        return items
    except Exception as e:
        return [create_error_item("Gericht", str(e))]

# --- MAIN ROUTE ---
@app.route('/api/policies')
def get_policies():
    try:
        bt_items = fetch_bundestag()
        breg_items = fetch_bundesregierung()
        bverfg_items = fetch_bverfg()
        
        all_items = bt_items + breg_items + bverfg_items
        
        # Sortieren nach Datum
        all_items.sort(key=lambda x: x.get('datePublished', ''), reverse=True)
        
        return jsonify(all_items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
