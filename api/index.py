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

# REGIERUNG: Presseportal (Offizieller Verteiler, technisch stabil)
BREG_RSS_URL = "https://www.presseportal.de/rss/dienststelle_12760.rss2"

# GERICHT: LTO Rechtsprechung (Umgeht die Firewall des BVerfG)
# Wir filtern später im Code, damit wir nur BVerfG Sachen anzeigen
LTO_RSS_URL = "https://www.lto.de/rss/rechtsprechung/"

API_KEY = os.environ.get("BUNDESTAG_API_KEY") 

# Headers für Browser-Simulation
RSS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# --- HELFER ---

def create_error_item(source_name, error_msg, detail_msg=""):
    return {
        "id": f"error-{source_name}-{datetime.now().timestamp()}",
        "officialTitle": f"Status: {error_msg}",
        "simpleTitle": f"Diagnose {source_name}",
        "summary": f"Technisches Detail: {detail_msg}",
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

def fetch_rss_feed(url, source_name, institution_type):
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.getcode() != 200:
                return [create_error_item(source_name, f"HTTP {response.getcode()}", "Server Fehler")]
            
            xml_data = response.read()
            
            try:
                root = ET.fromstring(xml_data)
            except Exception as e:
                return [create_error_item(source_name, "XML Fehler", str(e))]
            
            items = []
            
            # WICHTIG: Wir suchen jetzt flexibel nach 'item' egal wo es steckt
            # Das löst das Problem, dass Regierung "keine Karte" zeigte
            rss_items = root.findall('.//item')
            
            if not rss_items:
                # DEBUG: Wir zeigen an, dass XML okay war, aber leer
                return [create_error_item(source_name, "Keine Inhalte", "RSS Feed war technisch okay aber leer.")]

            for entry in rss_items[:5]:
                title = entry.find('title').text or "Nachricht"
                
                # Filter für Gericht: Wir wollen beim LTO nur Sachen, die nach BVerfG klingen
                if institution_type == "bundesverfassungsgericht":
                    if "BVerfG" not in title and "Verfassungsgericht" not in title and "Karlsruhe" not in title:
                        continue 

                desc = entry.find('description').text or ""
                
                # Datum parsen
                iso_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
                pub_date = entry.find('pubDate')
                if pub_date is not None:
                    try:
                        dt = parsedate_to_datetime(pub_date.text)
                        iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except: pass

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
            
            # Falls Filter alles rausgeworfen hat beim Gericht
            if institution_type == "bundesverfassungsgericht" and not items:
                 # Fallback: Nimm einfach das erste Item, auch wenn nicht explizit BVerfG draufsteht
                 # Damit du zumindest siehst, dass es technisch geht
                 if rss_items:
                     fallback = rss_items[0]
                     t_fallback = fallback.find('title').text or "Rechtsprechung"
                     items.append({
                        "id": f"fallback-{hash(t_fallback)}",
                        "officialTitle": t_fallback,
                        "simpleTitle": t_fallback,
                        "summary": "Allgemeine Rechtsprechung (Fallback)",
                        "institution": "bundesverfassungsgericht",
                        "type": "ruling",
                        "category": "justice",
                        "datePublished": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "status": "effective",
                        "progress": 1.0,
                        "isBookmarked": False,
                        "voteResult": None
                     })

            return items

    except urllib.error.HTTPError as e:
        return [create_error_item(source_name, f"HTTP {e.code}", f"Blockiert: {e.reason}")]
    except Exception as e:
        return [create_error_item(source_name, "Crash", str(e))]

# --- MAIN ROUTE ---
@app.route('/api/policies')
def get_policies():
    try:
        bt_items = fetch_bundestag()
        breg_items = fetch_rss_feed(BREG_RSS_URL, "Regierung", "bundesregierung")
        bverfg_items = fetch_rss_feed(LTO_RSS_URL, "Gericht", "bundesverfassungsgericht")
        
        all_items = bt_items + breg_items + bverfg_items
        
        # Sortieren nach Datum
        all_items.sort(key=lambda x: x.get('datePublished', ''), reverse=True)
        
        return jsonify(all_items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
