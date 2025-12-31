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
# Wir nutzen die stabilsten RSS Feeds
BREG_RSS_URL = "https://www.bundesregierung.de/service/rss/breg-de/aktuelles"
BVERFG_RSS_URL = "https://www.bundesverfassungsgericht.de/SiteGlobals/Functions/RSS/Pressemitteilungen/RSS_Pressemitteilungen.xml"

API_KEY = os.environ.get("BUNDESTAG_API_KEY") 

# --- HELFER ---

def create_error_item(source_name, error_msg, detail_msg=""):
    """Erstellt eine detaillierte Fehlerkarte zur Diagnose"""
    return {
        "id": f"error-{source_name}-{datetime.now().timestamp()}",
        "officialTitle": f"Diagnose {source_name}: {error_msg}",
        "simpleTitle": f"Fehler: {error_msg}", # Wird in der App groß angezeigt
        "summary": f"Details: {detail_msg}. Dies ist oft ein Blockieren der Vercel-Server-IP durch die Behörde.",
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
    elif "beratung" in st or "ausschuss" in st or "überwiesen" in st or "bundesrat" in st: return "committee" # Auch Zuleitung an BR ist "in Arbeit"
    elif "beschlussempfehlung" in st or "bericht" in st: return "committee"
    else: return "draft"

def map_category(text):
    t = str(text).lower()
    if "wirtschaft" in t or "finan" in t: return "economy"
    elif "umwelt" in t or "klima" in t: return "environment"
    elif "sozial" in t or "rente" in t or "arbeit" in t: return "social"
    elif "digital" in t: return "digital"
    elif "recht" in t or "innere" in t: return "justice"
    elif "verteidigung" in t or "wehr" in t: return "defense"
    elif "gesundheit" in t or "pflege" in t: return "health"
    else: return "other"

def map_type_bundestag(vorgangstyp):
    vt = str(vorgangstyp).lower()
    if "gesetz" in vt: return "bill"
    elif "verordnung" in vt: return "ordinance"
    elif "antrag" in vt: return "motion"
    else: return "bill"

# --- NEUE FETCH METHODE (URLLIB STATT REQUESTS) ---
# Diese Methode tarnt sich besser als "echter Browser" und umgeht manche Request-Blocks

def fetch_rss_stealth(url, source_name):
    try:
        req = urllib.request.Request(url)
        # Wir simulieren einen echten Mac Chrome Browser
        req.add_header('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8')
        req.add_header('Accept-Language', 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7')
        
        # Timeout setzen
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read()
            
            # Status prüfen (urllib wirft meist Errors bei != 200, aber sicher ist sicher)
            if response.getcode() != 200:
                return [create_error_item(source_name, f"HTTP {response.getcode()}", "Server lehnte ab")]
                
            # XML Parsen
            root = ET.fromstring(data)
            items = []
            rss_items = root.findall('.//item')
            
            if not rss_items:
                return [create_error_item(source_name, "Leeres XML", "RSS Feed geladen aber keine Items gefunden.")]

            for entry in rss_items[:4]:
                title = entry.find('title').text or "Nachricht"
                desc = entry.find('description').text or ""
                
                iso_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
                pub_date = entry.find('pubDate')
                if pub_date is not None:
                    try:
                        dt = parsedate_to_datetime(pub_date.text)
                        iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except: pass

                clean_desc = re.sub('<[^<]+?>', '', desc)[:250] + "..."

                item = {
                    "id": f"{source_name}-{hash(title)}",
                    "officialTitle": title,
                    "simpleTitle": title,
                    "summary": clean_desc,
                    "institution": "bundesregierung" if source_name == "Regierung" else "bundesverfassungsgericht",
                    "type": "motion" if source_name == "Regierung" else "ruling",
                    "category": map_category(title + " " + desc),
                    "datePublished": iso_date,
                    "lastUpdated": iso_date,
                    "status": "published" if source_name == "Regierung" else "effective",
                    "progress": 1.0,
                    "isBookmarked": False,
                    "voteResult": None
                }
                items.append(item)
            return items

    except urllib.error.HTTPError as e:
        # Hier fangen wir den 404/403 ab und lesen den Fehlergrund
        return [create_error_item(source_name, f"HTTP {e.code}", f"Grund: {e.reason}")]
    except urllib.error.URLError as e:
        return [create_error_item(source_name, "Verbindungsfehler", str(e.reason))]
    except Exception as e:
        return [create_error_item(source_name, "Crash", str(e))]


# --- QUELLEN ---

def fetch_bundestag():
    # Bundestag API lassen wir via requests laufen, da sie stabil und dokumentiert ist
    if not API_KEY: return [create_error_item("Bundestag", "API Key fehlt")]
    try:
        params = { "f.vorgangstyp": "Gesetzgebung", "format": "json", "limit": 15, "sort": "-aktualisiert" }
        headers = { "Authorization": f"ApiKey {API_KEY}" }
        resp = requests.get(DIP_API_URL, params=params, headers=headers)
        
        if resp.status_code != 200: return [create_error_item("Bundestag", str(resp.status_code))]
        
        items = []
        for doc in resp.json().get("documents", []):
            datum_str = doc.get("datum", "2025-01-01")
            
            # --- STATUS DETEKTIV (Wiederhergestellt) ---
            status_raw = doc.get("beratungsstand", "")
            if not status_raw: status_raw = doc.get("vorgangsstatus", "")
            if not status_raw: status_raw = doc.get("aktueller_stand", "Entwurf")

            # --- TITEL LOGIK (Mit Debug Info in Klammern) ---
            raw_title = doc.get("titel", "Ohne Titel")
            debug_title = f"{raw_title} [{status_raw}]" # Debug Info für dich
            
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
                "lastUpdated": f"{datum_str}T09:00:00Z", 
                "status": map_bundestag_status(status_raw),
                "progress": 0.5,
                "isBookmarked": False,
                "voteResult": None
            }
            items.append(item)
        return items
    except Exception as e:
        return [create_error_item("Bundestag", str(e))]

# --- MAIN ROUTE ---
@app.route('/api/policies')
def get_policies():
    try:
        # 1. Bundestag holen
        bt_items = fetch_bundestag()
        
        # 2. RSS Feeds mit der neuen "Stealth"-Methode holen
        breg_items = fetch_rss_stealth(BREG_RSS_URL, "Regierung")
        bverfg_items = fetch_rss_stealth(BVERFG_RSS_URL, "Gericht")
        
        all_items = bt_items + breg_items + bverfg_items
        
        # 3. Sortieren
        all_items.sort(key=lambda x: x.get('datePublished', ''), reverse=True)
        
        return jsonify(all_items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
