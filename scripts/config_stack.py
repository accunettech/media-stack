#!/usr/bin/env python3
import os
import re
import time
import json
import subprocess
import requests
import xml.etree.ElementTree as ET
from pathlib import Path

# If you want .env auto-loaded, keep this:
from dotenv import load_dotenv
load_dotenv(".env")

# ---------------------------
# Host → apps (script runs on host)
PROWLARR_URL = os.getenv("PROWLARR_URL", "http://localhost:9696").rstrip("/")
SONARR_URL   = os.getenv("SONARR_URL",   "http://localhost:8989").rstrip("/")
RADARR_URL   = os.getenv("RADARR_URL",   "http://localhost:7878").rstrip("/")

# In-Docker (apps talk to each other using service DNS names)
PROWLARR_URL_IN_DOCKER = "http://prowlarr:9696"
SONARR_URL_IN_DOCKER   = "http://sonarr:8989"
RADARR_URL_IN_DOCKER   = "http://radarr:7878"
FSR_URL_IN_DOCKER      = "http://flaresolverr:8191"

# API keys (read from config.xml if not provided)
PROWLARR_API_KEY = (os.getenv("PROWLARR_API_KEY") or "").strip()
SONARR_API_KEY   = (os.getenv("SONARR_API_KEY") or "").strip()
RADARR_API_KEY   = (os.getenv("RADARR_API_KEY") or "").strip()

# Absolute host paths to *Arr config.xml files (left side of your volume mounts)
CONF_ROOT    = os.getenv("CONF_HOME", "")
SONARR_CFG   = os.getenv("SONARR_CFG",   f"{CONF_ROOT}/sonarr/config.xml")
RADARR_CFG   = os.getenv("RADARR_CFG",   f"{CONF_ROOT}/radarr/config.xml")
PROWLARR_CFG = os.getenv("PROWLARR_CFG", f"{CONF_ROOT}/prowlarr/config.xml")

# Indexers to seed in Prowlarr
INDEXERS = [x.strip() for x in os.getenv("INDEXERS", "1337x,EZTV,TorrentGalaxyClone,ThePirateBay").split(",") if x.strip()]
WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "300"))

# Prowlarr proxy (FlareSolverr)
CREATE_PROXY = os.getenv("CREATE_PROXY", "true").lower() == "true"
FSR_NAME = os.getenv("FSR_NAME", "FlareSolverr")

# Optional: set web auth on *Arr themselves
AUTH_METHOD = os.getenv("AUTH_METHOD", "forms")  # or "basic"
RESTART_AFTER_AUTH = os.getenv("RESTART_AFTER_AUTH", "true").lower() == "true"
UI_USER = "user"
UI_PASS = "password"

# qBittorrent — API endpoint (host-mapped) for bootstrap to talk to qB
QBT_API_SCHEME = "https" if os.getenv("QBT_API_SSL","false").lower()=="true" else "http"
QBT_API_HOST   = os.getenv("QBT_API_HOST", "127.0.0.1")
QBT_API_PORT   = int(os.getenv("QBT_API_PORT", "8080"))
QBT_API_BASE   = f"{QBT_API_SCHEME}://{QBT_API_HOST}:{QBT_API_PORT}"
QBT_API_WAIT_TIMEOUT = int(os.getenv("QBT_API_WAIT_TIMEOUT", "240"))

# qBittorrent container name to scrape logs from
QBT_CONTAINER = os.getenv("QBT_CONTAINER", "qbittorrent")

# If you want to set a known qB password now (recommended)
QBT_SET_KNOWN_CREDS = True

# Categories to use when adding the qB client into *Arr
QBT_CAT_SONARR = os.getenv("QBITTORRENT_CAT_SONARR", "tv")
QBT_CAT_RADARR = os.getenv("QBITTORRENT_CAT_RADARR", "movies")

# Tag used to tie FlareSolverr proxy to CF-prone indexers (harmless if unused)
CF_TAG_LABEL = os.getenv("CF_TAG", "cf")

# Usenet defaults (still supported)
USENET_DEFAULT_APIKEY  = os.getenv("USENET_DEFAULT_APIKEY") or ""
USENET_DEFAULT_BASEURL = os.getenv("USENET_DEFAULT_BASEURL") or ""

SABNZBD_CFG = os.getenv("SABNZBD_CFG", f"{CONF_ROOT}/sabnzbd/sabnzbd.ini")
SABNZBD_API_KEY = (os.getenv("SABNZBD_API_KEY") or "").strip()

# ---------------------------
# Small helpers
def wait_for_http(url, timeout):
    print(f"[~] Waiting for HTTP {url} (timeout {timeout}s)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code < 500:
                print(f"[+] {url} is up ({r.status_code})")
                return True
        except Exception as e:
            print(f"  ...still waiting: {e}")
        time.sleep(3)
    return False

def wait_for_file(path_str, timeout):
    p = Path(path_str)
    print(f"[~] Waiting for file {p} (timeout {timeout}s)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if p.exists():
            print(f"[+] Found {p}")
            return True
        time.sleep(2)
        print("  ...still waiting")
    return False

def parse_api_key_from_config(path_str) -> str:
    p = Path(path_str)
    try:
        tree = ET.parse(str(p))
        root = tree.getroot()
        key = root.findtext(".//ApiKey")
        return (key or "").strip()
    except Exception as e:
        print(f"[-] Failed to parse API key from {p}: {e}")
        return ""

def parse_sab_api_key(path_str) -> str:
    try:
        # very lightweight .ini scrape; avoids needing configparser section names
        with open(path_str, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip().lower().startswith("api_key"):
                    # formats like: api_key = abc123
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip()
    except Exception as e:
        print(f"[-] Failed to parse SAB API key from {path_str}: {e}")
    return ""

def ensure_keys():
    global SONARR_API_KEY, RADARR_API_KEY, PROWLARR_API_KEY
    wait_for_file(SONARR_CFG, WAIT_TIMEOUT)
    wait_for_file(RADARR_CFG, WAIT_TIMEOUT)
    wait_for_file(PROWLARR_CFG, WAIT_TIMEOUT)

    if not SONARR_API_KEY:
        SONARR_API_KEY = parse_api_key_from_config(SONARR_CFG)
        print(f"[=] SONARR_API_KEY: {'set' if SONARR_API_KEY else 'missing'}")
    if not RADARR_API_KEY:
        RADARR_API_KEY = parse_api_key_from_config(RADARR_CFG)
        print(f"[=] RADARR_API_KEY: {'set' if RADARR_API_KEY else 'missing'}")
    if not PROWLARR_API_KEY:
        PROWLARR_API_KEY = parse_api_key_from_config(PROWLARR_CFG)
        print(f"[=] PROWLARR_API_KEY: {'set' if PROWLARR_API_KEY else 'missing'}")

# ---------------------------
# *Arr auth (optional)
def set_arr_auth(base_url: str, api_key: str, api_ver: int, username: str, password: str, method: str = "forms"):
    if not (username and password):
        print(f"[=] Skipping auth for {base_url} (no username/password provided)")
        return False

    host_url = f"{base_url}/api/v{api_ver}/config/host"
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    try:
        r = requests.get(host_url, headers=headers, timeout=10)
        r.raise_for_status()
        cfg = r.json()
    except Exception as e:
        print(f"[!] GET host config failed for {base_url}: {e}")
        return False

    def apply_settings(cfg_obj, auth_method):
        cfg_obj = dict(cfg_obj)
        cfg_obj["authenticationMethod"] = auth_method
        cfg_obj["authenticationRequired"] = "Enabled"  # enum
        cfg_obj["username"] = username
        cfg_obj["password"] = password
        cfg_obj["passwordConfirmation"] = password
        return cfg_obj

    for attempt_method in (method, "basic" if method != "basic" else None):
        if not attempt_method:
            break
        payload = apply_settings(cfg, attempt_method)
        try:
            u = requests.put(host_url, headers=headers, json=payload, timeout=15)
            if u.status_code in (200, 202):
                print(f"[+] Set auth for {base_url} (method={attempt_method})")
                return True
            else:
                print(f"[!] PUT host config failed for {base_url} ({attempt_method}): {u.status_code} {u.text[:300]}")
        except Exception as e:
            print(f"[!] Error updating host config for {base_url} ({attempt_method}): {e}")
    return False

def restart_arr(base_url: str, api_ver: int, api_key: str):
    try:
        r = requests.post(f"{base_url}/api/v{api_ver}/system/restart",
                          headers={"X-Api-Key": api_key}, timeout=10)
        print(f"[=] restart {base_url} -> {r.status_code}")
    except Exception as e:
        print(f"[!] restart call failed for {base_url}: {e}")

# ---------------------------
# Prowlarr apps + proxy + indexers
def prow_headers():
    return {"X-Api-Key": PROWLARR_API_KEY}

def post_with_retries(url, headers=None, payload=None, tries=10, delay=3, timeout=15):
    for i in range(tries):
        try:
            return requests.post(url, headers=headers or {}, json=payload, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
            if i == tries - 1:
                raise
            print(f"  ...POST retry {i+1}/{tries} to {url} after error: {e}")
            time.sleep(delay)

def add_app(app_name: str, base_url_in_docker: str, api_key: str):
    payload = {
        "name": app_name,
        "implementation": app_name,  # "Sonarr" or "Radarr"
        "configContract": f"{app_name}Settings",
        "fields": [
            {"name": "apiKey",      "value": api_key},
            {"name": "baseUrl",     "value": base_url_in_docker},
            {"name": "ProwlarrUrl", "value": PROWLARR_URL_IN_DOCKER},
        ],
        "enable": True
    }
    url = f"{PROWLARR_URL}/api/v1/applications"
    print(f"[~] Adding app {app_name} pointing to {base_url_in_docker} with ProwlarrUrl={PROWLARR_URL_IN_DOCKER}")
    try:
        r = post_with_retries(url, headers=prow_headers(), payload=payload, tries=10, delay=3, timeout=15)
        if r.status_code in (200, 201):
            print(f"[+] Added app {app_name}")
        elif r.status_code == 409:
            print(f"[=] App {app_name} already exists (409)")
        else:
            print(f"[!] add_app {app_name} failed: {r.status_code} {r.text[:300]}")
    except Exception as e:
        print(f"[!] add_app {app_name} error after retries: {e}")

def set_app_synclevel_and_sync(app_name: str):
    apps = requests.get(f"{PROWLARR_URL}/api/v1/applications",
                        headers=prow_headers(), timeout=10).json()
    app = next((a for a in apps if a.get("implementation") == app_name), None)
    if not app:
        print(f"[-] {app_name} app not found to set syncLevel")
        return
    app_id = app.get("id")

    fields = app.get("fields") or []
    found = False
    for f in fields:
        if f.get("name","").lower() == "prowlarrurl":
            f["value"] = PROWLARR_URL_IN_DOCKER
            found = True
    if not found:
        fields.append({"name":"ProwlarrUrl","value":PROWLARR_URL_IN_DOCKER})

    for full_sync in ("FullSync", 2):
        obj = dict(app)
        obj["fields"] = fields
        obj["syncLevel"] = full_sync
        u = requests.put(f"{PROWLARR_URL}/api/v1/applications/{app_id}",
                         headers=prow_headers(), json=obj, timeout=10)
        if u.status_code in (200, 202):
            print(f"[+] Set {app_name} syncLevel to {full_sync}")
            break
        else:
            print(f"[!] PUT syncLevel={full_sync} -> {u.status_code}: {u.text[:300]}")

    try:
        t = requests.post(f"{PROWLARR_URL}/api/v1/applications/test",
                          headers=prow_headers(), json=[app_id], timeout=10)
        print(f"[=] applications/test -> {t.status_code}")
    except Exception as e:
        print(f"[!] applications/test error: {e}")

    for action in ("syncindexers", "SyncAppIndexers"):
        try:
            s = requests.post(f"{PROWLARR_URL}/api/v1/applications/action/{action}",
                              headers=prow_headers(), json=[app_id], timeout=10)
            print(f"[=] action/{action} -> {s.status_code}")
            if s.status_code in (200, 202, 204):
                break
        except Exception as e:
            print(f"[!] action/{action} error: {e}")

def list_tags():
    r = requests.get(f"{PROWLARR_URL}/api/v1/tag", headers=prow_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def ensure_tag_id(label: str) -> int | None:
    try:
        tags = list_tags()
        for t in tags:
            if t.get("label") == label:
                print(f"[=] Tag '{label}' exists (id={t.get('id')})")
                return t.get("id")
    except Exception as e:
        print(f"[-] Could not list tags: {e}")
    try:
        r = requests.post(f"{PROWLARR_URL}/api/v1/tag",
                          headers=prow_headers(),
                          json={"label": label},
                          timeout=10)
        if r.status_code in (200, 201):
            tid = r.json().get("id")
            print(f"[+] Created tag '{label}' (id={tid})")
            return tid
        else:
            print(f"[-] Create tag '{label}' failed: {r.status_code} {r.text[:300]}")
    except Exception as e:
        print(f"[-] Create tag error: {e}")
    return None

def get_proxy_base():
    for base in ("/api/v1/indexerproxy", "/api/v1/proxy"):
        try:
            r = requests.get(f"{PROWLARR_URL}{base}", headers=prow_headers(), timeout=10)
            if r.status_code in (200, 204, 405):
                print(f"[=] Using proxy endpoint base: {base}")
                return base
        except Exception:
            pass
    print("[-] Could not determine proxy endpoint base. Fallback: /api/v1/indexerproxy")
    return "/api/v1/indexerproxy"

def list_proxies(proxy_base):
    r = requests.get(f"{PROWLARR_URL}{proxy_base}", headers=prow_headers(), timeout=15)
    if r.status_code != 200:
        print(f"[-] list_proxies {proxy_base} status {r.status_code}: {r.text[:200]}")
        return None
    try:
        return r.json()
    except Exception as e:
        print(f"[-] list_proxies JSON error: {e}; body: {r.text[:200]}")
        return None

def create_proxy_if_needed():
    if not CREATE_PROXY:
        print("[=] Proxy creation disabled (CREATE_PROXY=false)")
        return None

    proxy_base = get_proxy_base()
    tag_id = ensure_tag_id(CF_TAG_LABEL)

    existing = list_proxies(proxy_base)
    if isinstance(existing, list):
        for p in existing:
            if p.get("name") == FSR_NAME and (p.get("implementation") in ("FlareSolverr", "FLARESOLVERR")):
                print(f"[=] Proxy '{FSR_NAME}' already exists (id={p.get('id')})")
                return p.get("id")

    payload = {
        "name": FSR_NAME,
        "implementation": "FlareSolverr",
        "configContract": "FlareSolverrSettings",
        "enable": True,
        "tags": [tag_id] if tag_id is not None else [],
        "fields": [
            {"name": "host", "value": FSR_URL_IN_DOCKER},
            {"name": "requestTimeout", "value": 60},
            {"name": "proxyType", "value": "FlareSolverr"}
        ]
    }

    url = f"{PROWLARR_URL}{proxy_base}"
    r = requests.post(url, headers=prow_headers(), json=payload, timeout=20)
    if r.status_code in (200, 201):
        pid = r.json().get("id")
        print(f"[+] Created proxy '{FSR_NAME}' (id={pid}) at {proxy_base}")
        return pid

    alt_base = "/api/v1/proxy" if proxy_base.endswith("indexerproxy") else "/api/v1/indexerproxy"
    print(f"[!] POST {proxy_base} -> {r.status_code}. Trying {alt_base}...")
    r2 = requests.post(f"{PROWLARR_URL}{alt_base}", headers=prow_headers(), json=payload, timeout=20)
    if r2.status_code in (200, 201):
        pid = r2.json().get("id")
        print(f"[+] Created proxy '{FSR_NAME}' (id={pid}) at {alt_base}")
        return pid

    print(f"[-] Proxy create failed. {proxy_base} -> {r.status_code} {r.text[:300]}  |  {alt_base} -> {r2.status_code} {r2.text[:300]}")
    return None

# ----- Indexers (torrent + usenet) -----
def _canon(s: str) -> str:
    return "".join(ch.lower() for ch in (s or "") if ch.isalnum())

def _norm_indexer_key(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isalnum()).upper()

def _is_usenet(defn: dict) -> bool:
    proto = (defn.get("protocol") or "").lower()
    impl  = (defn.get("implementation") or "").lower()
    return proto == "usenet" or "newznab" in impl

def _get_override(name_norm: str, field_name: str) -> str | None:
    env_key = f"IDX_{name_norm}__{field_name.upper()}"
    val = os.getenv(env_key)
    return val if (val is not None and val != "") else None

def _build_fields_with_overrides(defn: dict, overrides: dict[str, str]) -> list[dict]:
    out = []
    for f in defn.get("fields", []):
        fname = f.get("name")
        if not fname:
            continue
        if fname in overrides and overrides[fname] not in (None, ""):
            val = overrides[fname]
        elif "value" in f:
            val = f.get("value")
        else:
            val = f.get("defaultValue")
        out.append({"name": fname, "value": val})
    return out

def _collect_overrides_for(defn: dict) -> dict[str, str]:
    name_for_env = defn.get("name") or defn.get("implementationName") or ""
    key = _norm_indexer_key(name_for_env)
    overrides = {}
    schema_fields = [f.get("name") for f in defn.get("fields", []) if f.get("name")]
    for fname in schema_fields:
        ov = _get_override(key, fname)
        if ov is not None:
            overrides[fname] = ov
    if _is_usenet(defn):
        if "apiKey" in schema_fields and "apiKey" not in overrides and USENET_DEFAULT_APIKEY:
            overrides["apiKey"] = USENET_DEFAULT_APIKEY
        if "baseUrl" in schema_fields and "baseUrl" not in overrides and USENET_DEFAULT_BASEURL:
            overrides["baseUrl"] = USENET_DEFAULT_BASEURL
    return overrides

def _create_indexer_payload_from_def(defn: dict, tag_id: int | None,
                                     proxy_id: int | None, use_proxy: bool) -> dict:
    payload = {
        "name": defn.get("name") or defn.get("implementationName"),
        "implementationName": defn.get("implementationName") or defn.get("name"),
        "implementation": defn.get("implementation"),
        "configContract": defn.get("configContract"),
        "protocol": defn.get("protocol", "torrent"),
        "indexerUrls": defn.get("indexerUrls", []),
        "appProfileId": 1,
        "priority": 25,
        "tags": [tag_id] if tag_id is not None else [],
        "enable": True,
        "supportsRss": True,
        "supportsSearch": True,
        "enableRss": True,
        "enableSearch": True,
        "useProxy": bool(proxy_id) and use_proxy,
        "fields": []
    }
    overrides = _collect_overrides_for(defn)
    payload["fields"] = _build_fields_with_overrides(defn, overrides)
    if proxy_id and use_proxy:
        payload["proxy"] = proxy_id
        payload["proxyId"] = proxy_id
    return payload

def get_indexer_definitions():
    print("[~] Fetching indexer definitions")
    r = requests.get(f"{PROWLARR_URL}/api/v1/indexer/schema", headers=prow_headers(), timeout=20)
    r.raise_for_status()
    return r.json()

def create_indexer_with_optional_proxy(defs, name: str, proxy_id: int | None):
    wanted = _canon(name)
    match = next((
        d for d in defs
        if _canon(d.get("name","")) == wanted
        or _canon(d.get("implementationName","")) == wanted
    ), None)

    if not match:
        candidates = [
            d for d in defs
            if wanted in _canon(d.get("name","")) or wanted in _canon(d.get("implementationName",""))
            or _canon(d.get("name","")) in wanted or _canon(d.get("implementationName","")) in wanted
        ]
        if len(candidates) == 1:
            match = candidates[0]
        elif candidates:
            print(f"[~] Multiple close matches for '{name}':")
            for d in candidates[:10]:
                print(f"    - {d.get('name')} (impl={d.get('implementationName')})")
            match = candidates[0]

    if not match:
        print(f"[-] No definition found for '{name}'.")
        return

    tag_id = ensure_tag_id(CF_TAG_LABEL)
    payload = _create_indexer_payload_from_def(match, tag_id, proxy_id, use_proxy=True)

    try:
        r = requests.post(f"{PROWLARR_URL}/api/v1/indexer", headers=prow_headers(), json=payload, timeout=25)
        if r.status_code in (200, 201):
            idx_id = r.json().get("id")
            proto = (match.get("protocol") or "").lower()
            print(f"[+] Created indexer '{name}' (id={idx_id}, protocol={proto})")
        else:
            print(f"[!] Failed creating indexer '{name}': {r.status_code} {r.text[:400]}")
    except Exception as e:
        print(f"[!] Exception creating indexer '{name}': {e}")

# ---------------------------
# qBittorrent: temp password → login → (optional) set known password
def get_qbt_temp_password(container: str) -> str | None:
    try:
        out = subprocess.run(
            ["docker", "logs", container],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20
        ).stdout
    except Exception as e:
        print(f"[-] Could not read logs from {container}: {e}")
        return None

    patterns = [
        r"temporary password.*?:\s*([^\s]+)",
        r"temp.*password.*?:\s*([^\s]+)",
        r"password:\s*([^\s]+)",
        r"Web\s*UI.*credentials.*\badmin\b.*\b([A-Za-z0-9._-]{6,})",
    ]
    for pat in patterns:
        m = re.search(pat, out, flags=re.IGNORECASE)
        if m:
            pw = m.group(1).strip()
            print(f"[+] Found temp qBittorrent password in logs: {pw}")
            return pw
    print("[~] No temp password found in logs (maybe already configured).")
    return None

def qbt_login_session(base: str, username: str, password: str) -> requests.Session | None:
    s = requests.Session()
    try:
        r = s.post(f"{base}/api/v2/auth/login",
                   data={"username": username, "password": password},
                   headers={"Referer": f"{base}/", "Origin": base},
                   timeout=10)
        if r.status_code == 200 and r.text.strip().lower() == "ok.":
            return s
        print(f"[-] qBittorrent login failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[-] qBittorrent login exception: {e}")
    return None

def qbt_set_preferences(sess: requests.Session, base: str, prefs: dict) -> bool:
    try:
        r = sess.post(f"{base}/api/v2/app/setPreferences",
                      data={"json": json.dumps(prefs)},
                      headers={"Referer": f"{base}/", "Origin": base},
                      timeout=10)
        if r.status_code == 200:
            return True
        print(f"[-] setPreferences failed: {r.status_code} {r.text[:400]}")
    except Exception as e:
        print(f"[-] setPreferences exception: {e}")
    return False

def prepare_qbt_via_api() -> tuple[str, str] | tuple[None, None]:
    """Return (username, password) to use for *Arr download client."""
    # Make sure qB API is up (host-mapped)
    if not wait_for_http(QBT_API_BASE, QBT_API_WAIT_TIMEOUT):
        print(f"[-] qBittorrent API not reachable at {QBT_API_BASE}")
        return (None, None)

    temp_pw = get_qbt_temp_password(QBT_CONTAINER)
    if not temp_pw and not UI_PASS:
        # Nothing we can do; hope creds already set
        print("[~] No temp pass and no new pass provided; assuming qB already has stable creds.")
        return (UI_USER, UI_PASS) if UI_PASS else (None, None)

    # If we have a temp password, try to log in with it
    if temp_pw:
        sess = qbt_login_session(QBT_API_BASE, "admin", temp_pw)
        if not sess:
            print("[-] Could not log in with temp password. You can set QBITTORRENT_PASS and re-run.")
            return ("admin", temp_pw)  # still return so user sees it/you can try using it
        # Optionally set a known password (recommended)
        if QBT_SET_KNOWN_CREDS and UI_PASS:
            creds_prefs = {"web_ui_username": UI_USER, "web_ui_password": UI_PASS}
            if qbt_set_preferences(sess, QBT_API_BASE, creds_prefs):
                print("[+] Set known qBittorrent credentials via API")
                return (UI_USER, UI_PASS)
            else:
                print("[!] Failed setting known qBittorrent credentials; using temp password")
                return ("admin", temp_pw)
        else:
            print(f"[=] Keeping temporary password. (username=admin, password={temp_pw})")
            return ("admin", temp_pw)

    # No temp, but the user provided a new pass — just return those
    return (UI_USER, UI_PASS)

# ---------------------------
# Add qBittorrent to Sonarr/Radarr
def ensure_qbittorrent_client(app_url, api_key, name="qbittorrent",
                              host="gluetun", port=8080,
                              username="", password="",
                              category=None, use_ssl=False):
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    try:
        existing = requests.get(f"{app_url}/api/v3/downloadclient", headers=headers, timeout=10).json()
    except Exception as e:
        print(f"[!] Failed to list download clients for {app_url}: {e}")
        return

    for cli in existing:
        if cli.get("implementation") == "QBittorrent":
            print(f"[=] qBittorrent already present in {app_url} (id={cli.get('id')})")
            return

    payload = {
        "enable": True,
        "protocol": "torrent",
        "priority": 1,
        "configContract": "QBittorrentSettings",
        "implementation": "QBittorrent",
        "implementationName": "qBittorrent",
        "name": name,
        "fields": [
            {"name": "host", "value": host},
            {"name": "port", "value": port},
            {"name": "useSsl", "value": bool(use_ssl)},
            {"name": "urlBase", "value": ""},
            {"name": "username", "value": username or ""},
            {"name": "password", "value": password or ""},
        ]
    }
    if category:
        payload["fields"].append({"name": "category", "value": category})

    try:
        r = requests.post(f"{app_url}/api/v3/downloadclient", headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            print(f"[+] Added qBittorrent to {app_url}")
        elif r.status_code == 409:
            print(f"[=] qBittorrent already exists in {app_url}")
        else:
            print(f"[!] Failed to add qBittorrent to {app_url}: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[!] Exception adding qBittorrent to {app_url}: {e}")

# Add SAB to Sonarr/Radarr
def ensure_sab_client(app_url, api_key, name="sabnzbd",
                      host="sabnzbd", port=8080,
                      sab_api_key="", category=None, use_ssl=False):
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    try:
        existing = requests.get(f"{app_url}/api/v3/downloadclient", headers=headers, timeout=10).json()
        for cli in existing:
            if cli.get("implementation") == "SABnzbd":
                print(f"[=] SABnzbd already present in {app_url} (id={cli.get('id')})")
                return
    except Exception as e:
        print(f"[!] Failed to list download clients for {app_url}: {e}")
        return

    payload = {
        "enable": True,
        "protocol": "usenet",
        "priority": 1,
        "configContract": "SABnzbdSettings",
        "implementation": "SABnzbd",
        "implementationName": "SABnzbd",
        "name": name,
        "fields": [
            {"name": "host", "value": host},
            {"name": "port", "value": port},
            {"name": "useSsl", "value": bool(use_ssl)},
            {"name": "urlBase", "value": ""},
            {"name": "apiKey", "value": sab_api_key}
        ]
    }
    if category:
        payload["fields"].append({"name": "category", "value": category})

    try:
        r = requests.post(f"{app_url}/api/v3/downloadclient", headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            print(f"[+] Added SABnzbd to {app_url}")
        elif r.status_code == 409:
            print(f"[=] SABnzbd already exists in {app_url}")
        else:
            print(f"[!] Failed to add SABnzbd to {app_url}: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[!] Exception adding SABnzbd to {app_url}: {e}")

# ---------------------------
def main():
    # Ensure qB WebUI is reachable (host-mapped port)
    wait_for_http(QBT_API_BASE, QBT_API_WAIT_TIMEOUT)

    # Ensure *Arr/Prowlarr are up
    if not wait_for_http(PROWLARR_URL, WAIT_TIMEOUT): exit(1)
    if not wait_for_http(SONARR_URL, WAIT_TIMEOUT): exit(1)
    if not wait_for_http(RADARR_URL, WAIT_TIMEOUT): exit(1)

    # Read API keys from config if needed
    ensure_keys()
    if not (PROWLARR_API_KEY and SONARR_API_KEY and RADARR_API_KEY):
        print("[-] Missing API keys; ensure apps initialized or pass via env.")
        exit(1)

    # (Optional) Set web auth on *Arr
    sonarr_auth_ok = set_arr_auth(SONARR_URL, SONARR_API_KEY, 3, UI_USER, UI_PASS, AUTH_METHOD)
    radarr_auth_ok = set_arr_auth(RADARR_URL, RADARR_API_KEY, 3, UI_USER, UI_PASS, AUTH_METHOD)
    prowlarr_auth_ok = set_arr_auth(PROWLARR_URL, PROWLARR_API_KEY, 1, UI_USER, UI_PASS, AUTH_METHOD)

    if RESTART_AFTER_AUTH:
        if sonarr_auth_ok:  restart_arr(SONARR_URL, 3, SONARR_API_KEY)
        if radarr_auth_ok:  restart_arr(RADARR_URL, 3, RADARR_API_KEY)
        if prowlarr_auth_ok: restart_arr(PROWLARR_URL, 1, PROWLARR_API_KEY)
        if prowlarr_auth_ok: wait_for_http(PROWLARR_URL, 180)
        if sonarr_auth_ok:   wait_for_http(SONARR_URL, 180)
        if radarr_auth_ok:   wait_for_http(RADARR_URL, 180)

    # Register apps in Prowlarr + set sync level (use in-Docker URLs)
    add_app("Sonarr", SONARR_URL_IN_DOCKER, SONARR_API_KEY)
    add_app("Radarr", RADARR_URL_IN_DOCKER, RADARR_API_KEY)
    set_app_synclevel_and_sync("Sonarr")
    set_app_synclevel_and_sync("Radarr")

    # Create FlareSolverr proxy (optional) and seed indexers
    proxy_id = create_proxy_if_needed()
    defs = get_indexer_definitions()
    for idx in INDEXERS:
        create_indexer_with_optional_proxy(defs, idx, proxy_id)

    # qB: login with temp pass and optionally set a known password
    qbt_user, qbt_pass = prepare_qbt_via_api()
    if not qbt_user and UI_PASS:
        # No temp found; assume new pass is already set
        qbt_user, qbt_pass = (UI_USER, UI_PASS)

    # Add qBittorrent to Sonarr/Radarr (containers will reach it at gluetun:8080)
    ensure_qbittorrent_client(
        SONARR_URL, SONARR_API_KEY,
        host="gluetun", port=8080,
        username=qbt_user or "",
        password=qbt_pass or "",
        category=QBT_CAT_SONARR,
        use_ssl=False
    )
    ensure_qbittorrent_client(
        RADARR_URL, RADARR_API_KEY,
        host="gluetun", port=8080,
        username=qbt_user or "",
        password=qbt_pass or "",
        category=QBT_CAT_RADARR,
        use_ssl=False
    )

    # Add SABnzbd to Sonarr/Radarr (containers will reach it at sabnzbd:8080 on media_net container network))
    if not SABNZBD_API_KEY:
        if wait_for_file(SABNZBD_CFG, WAIT_TIMEOUT):
            SABNZBD_API_KEY = parse_sab_api_key(SABNZBD_CFG)

    if SABNZBD_API_KEY:
        # SAB runs on media_net, exposed as sabnzbd:8080 to other containers
        ensure_sab_client(
            SONARR_URL, SONARR_API_KEY,
            host="sabnzbd", port=8080,
            sab_api_key=SABNZBD_API_KEY,
            category="tv",     # matches SAB category you created
            use_ssl=False      # set True if you switch SAB UI to HTTPS
        )
        ensure_sab_client(
            RADARR_URL, RADARR_API_KEY,
            host="sabnzbd", port=8080,
            sab_api_key=SABNZBD_API_KEY,
            category="movies",
            use_ssl=False
        )
    else:
        print("[-] SABNZBD_API_KEY missing; skip adding SAB client.")

    print("[✓] Bootstrap complete")

if __name__ == "__main__":
    main()
