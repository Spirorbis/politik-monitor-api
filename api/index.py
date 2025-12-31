from flask import Flask, jsonify
import requests
import urllib.request
import urllib.error
from datetime import datetime
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

app = Flask(__name__)

# --- KONFIGURATION ---
DIP_API_URL = "https://search.dip.bundestag.de/api/v1/vorgang"

# NEU: Wir nutzen das Presseportal (offizieller Kanal der BReg), da die eigene Website Vercel blockiert.
BREG_RSS_URL = "https://www.presseportal.de/rss/dienststelle_12760.rss2"

# NEU: Die exakte URL für Entscheidungen (oft stabiler als Pressemitteilungen)
BVERFG_RSS_URL = "https://www.bundesverfassungsgericht.de/SiteGlobals/Functions/RSS/Entscheidungen/RSS_Entscheidungen_Aktuell.xml"

API_KEY = os.environ.get("BUNDESTAG_API_KEY") 

# --- HELFER ---

def create_error_item(source_name, error_msg):
    return {
        "id": f"error-{source_name}-{datetime.now().timestamp()}",
        "officialTitle": f"Status: {error_msg}",
        "simpleTitle": f"Ladefehler {source_name}",
        "summary": "Die externe Quelle ist vorübergehend nicht erreichbar oder blockiert den Zugriff.",
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
    elif "sozial" in t or "rente" in t or "arbeit" in t: return "social"
    elif "digital" in t or "internet" in t: return "digital"
    elif "recht" in t or "innere" in t or "polizei" in t: return "justice"
    elif "verteidigung" in t or "wehr" in t: return "defense"
    elif "gesundheit" in t or "pflege" in t: return "health"
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
        # Wir laden 15 Items, um genug Material zu haben
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

            # Titel Logik mit Debug Info
            raw_title = doc.get("titel", "Ohne Titel")
            debug_title = f"{raw_title} [{status_raw}]" 
            
            simple_title = raw_title
            match = re.search(r'\((.*?gesetz.*?)\)', raw_title, re.IGNORECASE)
            if match: simple_title = match.group(1)
            if len(simple_title) > 100 and simple_title == raw_title: simple_title = raw_title[:97] + "..."

            item = {
                "id": f"bt-{doc.get('id', '0')}",
                "officialTitle": debug_title, 
                "simpleTitle": simple_title,
                "summary": doc.get("abstract", "Keine Zusammenfassung."),
                "institution": "bundestag",
                "type": map_type_bundestag(doc.get("vorgangstyp", "")),
                "category": map_category(doc.get("sachgebiet", [])),
                "datePublished": f"{datum_str}T09:00:00Z", 
                "lastUpdated": f"{datum_str}T09:00:00Z", # Echtes Datum nutzen
                "status": map_bundestag_status(status_raw),
                "progress": 0.5,
                "isBookmarked": False,
                "voteResult": None
            }
            items.append(item)
        return items
    except Exception as e:
        return [create_error_item("Bundestag", str(e))]

def fetch_rss_feed(url, source_name, institution_type):
    """Generische RSS Funktion mit Browser-Maskierung"""
    try:
        # Browser-Header simulieren
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.getcode() != 200:
                return [create_error_item(source_name, f"HTTP {response.getcode()}")]
            
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            
            items = []
            # Wir suchen 'item' überall im XML
            rss_items = root.findall('.//item')
            
            for entry in rss_items[:4]: # Top 4 News
                title = entry.find('title').text or "Nachricht"
                desc = entry.find('description').text or ""
                
                # Datum parsen
                iso_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
                pub_date = entry.find('pubDate')
                if pub_date is not None:
                    try:
                        dt = parsedate_to_datetime(pub_date.text)
                        iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except: pass

                # HTML entfernen
                clean_desc = re.sub('<[^<]+?>', '', desc)[:250] + "..."

                item = {
                    "id": f"{institution_type}-{hash(title)}",
                    "officialTitle": title,
                    "simpleTitle": title,
                    "summary": clean_desc,
                    "institution": institution_type,
                    "type": "motion" if institution_type == "bundesregierung" else "ruling",
                    "category": map_category(title + " " + desc),
                    "datePublished": iso_date,
                    "lastUpdated": iso_date,
                    "status": "published" if institution_type == "bundesregierung" else "effective",
                    "progress": 1.0,
                    "isBookmarked": False,
                    "voteResult": None
                }
                items.append(item)
            return items

    except urllib.error.HTTPError as e:
        return [create_error_item(source_name, f"HTTP {e.code}")]
    except Exception as e:
        return [create_error_item(source_name, str(e))]

# --- MAIN ROUTE ---
@app.route('/api/policies')
def get_policies():
    try:
        bt_items = fetch_bundestag()
        
        # Regierung via Presseportal (stabil)
        breg_items = fetch_rss_feed(BREG_RSS_URL, "Regierung", "bundesregierung")
        
        # Gericht via offizieller URL (mit Browser Header)
        bverfg_items = fetch_rss_feed(BVERFG_RSS_URL, "Gericht", "bundesverfassungsgericht")
        
        all_items = bt_items + breg_items + bverfg_items
        
        # Sortieren
        all_items.sort(key=lambda x: x.get('datePublished', ''), reverse=True)
        
        return jsonify(all_items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
