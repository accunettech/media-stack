#!/usr/bin/env python3
import os
import re
import time
import json
import subprocess
import requests
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

# If you want .env auto-loaded, keep this:
from dotenv import load_dotenv
load_dotenv(".env")

# ---------------------------
CONF_ROOT    = os.getenv("CONF_HOME", "")
WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "300"))
HOSTNAME = socket.gethostname()
CONTAINER_MOVIES_DIR='/movies'
CONTAINER_SHOWS_DIR='/shows'

AUTH_METHOD = os.getenv("AUTH_METHOD", "forms")  # or "basic"
UI_USER = os.getenv("UI_USER", "user")
UI_PASS = os.getenv("UI_PASS", "password")

PROWLARR_URL = os.getenv("PROWLARR_URL", "http://localhost:9696").rstrip("/")
PROWLARR_URL_IN_DOCKER = "http://prowlarr:9696"
PROWLARR_CFG = os.getenv("PROWLARR_CFG", f"{CONF_ROOT}/prowlarr/config.xml")
PROWLARR_REQ_TIMEOUT = int(os.getenv("PROWLARR_REQ_TIMEOUT", "120"))
PROWLARR_CONTAINER = 'prowlarr'
INDEXERS = [x.strip() for x in os.getenv("INDEXERS", "1337x,EZTV,TorrentGalaxyClone,ThePirateBay").split(",") if x.strip()]
TORRENT_INDEXER_DELAY = 60

SONARR_URL   = os.getenv("SONARR_URL",   "http://localhost:8989").rstrip("/")
SONARR_URL_IN_DOCKER   = "http://sonarr:8989"
SONARR_CFG   = os.getenv("SONARR_CFG",   f"{CONF_ROOT}/sonarr/config.xml")
SONARR_CONTAINER = 'sonarr'

RADARR_URL   = os.getenv("RADARR_URL",   "http://localhost:7878").rstrip("/")
RADARR_URL_IN_DOCKER   = "http://radarr:7878"
RADARR_CFG   = os.getenv("RADARR_CFG",   f"{CONF_ROOT}/radarr/config.xml")
RADARR_CONTAINER = 'radarr'

CREATE_PROXY = os.getenv("CREATE_PROXY", "true").lower() == "true"
FSR_NAME = os.getenv("FSR_NAME", "FlareSolverr")
FSR_URL_IN_DOCKER      = "http://flaresolverr:8191"
CF_TAG_LABEL = os.getenv("CF_TAG", "cf")

QBT_API_SCHEME = "https" if os.getenv("QBT_API_SSL","false").lower()=="true" else "http"
QBT_API_HOST   = os.getenv("QBT_API_HOST", "127.0.0.1")
QBT_API_PORT   = int(os.getenv("QBT_API_PORT", "8080"))
QBT_API_BASE   = f"{QBT_API_SCHEME}://{QBT_API_HOST}:{QBT_API_PORT}"
QBT_API_WAIT_TIMEOUT = int(os.getenv("QBT_API_WAIT_TIMEOUT", "240"))
QBT_CONTAINER = os.getenv("QBT_CONTAINER", "qbittorrent")
QBT_SET_KNOWN_CREDS = True
QBT_CAT_SONARR = os.getenv("QBITTORRENT_CAT_SONARR", "tv")
QBT_CAT_RADARR = os.getenv("QBITTORRENT_CAT_RADARR", "movies")

USENET_DEFAULT_APIKEY  = os.getenv("USENET_DEFAULT_APIKEY") or ""
USENET_DEFAULT_BASEURL = os.getenv("USENET_DEFAULT_BASEURL") or ""

SABNZBD_CFG = os.getenv("SABNZBD_CFG", f"{CONF_ROOT}/sabnzbd/sabnzbd.ini")
SAB_WHITELIST = [x.strip() for x in os.getenv("SAB_WHITELIST", f"sabnzbd,localhost,127.0.0.1,{HOSTNAME},{HOSTNAME}.local").split(",") if x.strip()]
SABNZBD_CONTAINER = os.getenv("SABNZBD_CONTAINER", "sabnzbd")
SAB_CATS = [x.strip() for x in os.getenv("SAB_CATEGORIES", "tv,movies").split(",") if x.strip()]
SAB_CONFIG_PROVIDER = os.getenv("SAB_CONFIG_PROVIDER","false").lower()=="true"
SAB_HTTP_PORT = int(os.getenv("SAB_HTTP_PORT", "8080"))
SAB_LANG        = os.getenv("SAB_LANG", "en")
SAB_SRV_NAME    = os.getenv("SAB_SRV_NAME", "provider")
SAB_SRV_HOST    = os.getenv("SAB_SRV_HOST", "")
SAB_SRV_PORT    = int(os.getenv("SAB_SRV_PORT", "563"))
SAB_SRV_SSL     = int(os.getenv("SAB_SRV_SSL", "1"))
SAB_SRV_USER    = os.getenv("SAB_SRV_USER", "")
SAB_SRV_PASS    = os.getenv("SAB_SRV_PASS", "")
SAB_SRV_CONNS   = int(os.getenv("SAB_SRV_CONNS", "20"))
SAB_SRV_PRIORITY= int(os.getenv("SAB_SRV_PRIORITY", "0"))

COMPLETED_DOWNLOADS = "/downloads"
INCOMPLETE_DOWNLOADS = "/downloads/incomplete"

# ---------------------------

def ensure_root_folder(app_url: str, api_key: str, path: str) -> bool:
    """
    Ensure a root folder exists in Sonarr/Radarr.
    Returns True if created, False if already present or on error.
    """
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    try:
        existing = requests.get(f"{app_url}/api/v3/rootfolder", headers=headers, timeout=10).json()
    except Exception as e:
        print(f"[!] List rootfolder failed at {app_url}: {e}")
        return False

    # Already there?
    for rf in existing or []:
        if (rf.get("path") or "").rstrip("/") == path.rstrip("/"):
            print(f"[=] Root folder already present in {app_url}: {path}")
            return False

    # Try the simplest payload first; fall back to variants if needed
    payload_variants = [
        {"path": path},
        {"path": path, "name": path},                          # older builds sometimes accept name
        {"path": path, "defaultTags": []},                     # harmless extra
    ]

    for payload in payload_variants:
        try:
            r = requests.post(f"{app_url}/api/v3/rootfolder", headers=headers, json=payload, timeout=15)
            if r.status_code in (200, 201):
                print(f"[+] Added root folder to {app_url}: {path}")
                return True
            # Common 400s: folder missing/not writable; print body once and bail to caller
            if r.status_code == 400:
                print(f"[!] Create rootfolder 400 at {app_url}: {r.text[:400]}")
                break
            else:
                print(f"[!] Create rootfolder failed at {app_url}: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"[!] Create rootfolder exception at {app_url}: {e}")

    return False

def set_prowlarr_indexer_priorities(usenet_prio=10, torrent_prio=30, api_key=""):
    headers = {"X-Api-Key": api_key}
    """Lower = higher priority. Set Usenet indexers ahead of torrent indexers."""
    try:
        idxs = requests.get(f"{PROWLARR_URL}/api/v1/indexer",
                            headers=headers, timeout=20).json()
    except Exception as e:
        print(f"[!] Could not list Prowlarr indexers: {e}")
        return
    changed = 0
    for idx in idxs:
        proto = (idx.get("protocol") or "").lower()
        target = usenet_prio if proto == "usenet" else torrent_prio
        if idx.get("priority") != target:
            idx["priority"] = target
            try:
                r = requests.put(f"{PROWLARR_URL}/api/v1/indexer/{idx['id']}",
                                 headers=headers, json=idx, timeout=20)
                if r.status_code in (200, 202):
                    changed += 1
                    print(f"[+] {idx.get('name')} -> priority {target} ({proto})")
                else:
                    print(f"[!] Failed to set priority for {idx.get('name')}: {r.status_code} {r.text[:200]}")
            except Exception as e:
                print(f"[!] Error updating {idx.get('name')}: {e}")
    if changed == 0:
        print("[=] Prowlarr indexer priorities already correct")

def favor_usenet_everywhere(app_url, api_key, torrent_delay=TORRENT_INDEXER_DELAY, usenet_delay=0, sab_first=True):
    """
    For Sonarr/Radarr:
      1) Prefer Usenet in indexer config (but keep torrents enabled)
      2) Set Delay Profiles: usenetDelay / torrentDelay + preferredProtocol=Usenet
      3) Put SABnzbd above qBittorrent in download clients

    torrent_delay: seconds to delay torrents (e.g. 180–600)
    usenet_delay:  usually 0 (grab immediately)
    sab_first:     True => SAB priority 1, qB priority 2
    """
    import requests
    H = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    # --- 1) App-wide indexer config: prefer Usenet, keep both protocols enabled ---
    try:
        cfg = requests.get(f"{app_url}/api/v3/config/indexer", headers=H, timeout=15).json()
        before = dict(cfg)
        if "enableUsenet" in cfg:  cfg["enableUsenet"] = True
        if "enableTorrent" in cfg: cfg["enableTorrent"] = True

        # Sonarr/Radarr use either 'preferUsenet' (bool) or 'preferredProtocol' ('usenet'/'torrent' or enum)
        if "preferUsenet" in cfg:
            cfg["preferUsenet"] = True
        elif "preferredProtocol" in cfg:
            cfg["preferredProtocol"] = "usenet"  # Radarr/Sonarr accept this; some builds use 1=usenet/2=torrent (we handle that next step)
        if cfg != before:
            # some builds reject unknown/readonly fields; prune if you’ve seen issues
            body = {k: v for k, v in cfg.items() if k != "updateAutomatically"}
            r = requests.put(f"{app_url}/api/v3/config/indexer", headers=H, json=body, timeout=15)
            print(f"[+] Prefer Usenet in {app_url}: {r.status_code}")
        else:
            print(f"[=] Indexer config already prefers Usenet in {app_url}")
    except Exception as e:
        print(f"[!] Indexer config update failed for {app_url}: {e}")

    # --- 2) Delay Profiles: Prefer Usenet and set delays on all profiles ---
    try:
        profiles = requests.get(f"{app_url}/api/v3/delayprofile", headers=H, timeout=15).json()
        for p in profiles:
            changed = False
            # preferredProtocol may be an enum (1=Usenet, 2=Torrent) or a string
            if "preferredProtocol" in p:
                if isinstance(p["preferredProtocol"], int):
                    if p["preferredProtocol"] != 1:
                        p["preferredProtocol"] = 1
                        changed = True
                else:
                    if str(p["preferredProtocol"]).lower() != "usenet":
                        p["preferredProtocol"] = "usenet"
                        changed = True
            # set delays
            if p.get("usenetDelay") != usenet_delay:
                p["usenetDelay"] = usenet_delay; changed = True
            if p.get("torrentDelay") != torrent_delay:
                p["torrentDelay"] = torrent_delay; changed = True

            if changed:
                r = requests.put(f"{app_url}/api/v3/delayprofile/{p['id']}", headers=H, json=p, timeout=15)
                print(f"[+] DelayProfile {p['id']} updated on {app_url}: {r.status_code}")
        print(f"[=] Delay Profiles set on {app_url} (usenetDelay={usenet_delay}s, torrentDelay={torrent_delay}s)")
    except Exception as e:
        print(f"[!] DelayProfile update failed for {app_url}: {e}")

    # --- 3) Download-client priority: SAB above qBittorrent ---
    try:
        clients = requests.get(f"{app_url}/api/v3/downloadclient", headers=H, timeout=15).json()
        sab_prio, qb_prio = (1, 2) if sab_first else (2, 1)
        any_changed = False
        for cli in clients:
            impl = (cli.get("implementation") or "").lower()
            desired = None
            if impl == "sabnzbd" and cli.get("priority") != sab_prio:
                desired = sab_prio
            elif impl in ("qbittorrent") and cli.get("priority") != qb_prio:
                desired = qb_prio
            elif impl == "qbittorrent" and cli.get("priority") != qb_prio:
                desired = qb_prio
            # normalize qB name variants
            if (cli.get("implementation") == "QBittorrent") and cli.get("priority") != qb_prio:
                desired = qb_prio

            if desired is not None:
                cli["priority"] = desired
                any_changed = True
                r = requests.put(f"{app_url}/api/v3/downloadclient/{cli['id']}", headers=H, json=cli, timeout=15)
                if r.status_code not in (200, 202):
                    print(f"[!] Failed updating client {cli.get('name')} on {app_url}: {r.status_code} {r.text[:200]}")

        if any_changed:
            print(f"[+] Download-client priorities set on {app_url} (SAB first={sab_first})")
        else:
            print(f"[=] Download-client priorities already correct on {app_url}")
    except Exception as e:
        print(f"[!] Download-client priority update failed for {app_url}: {e}")

def set_download_client_priorities(app_url, api_key, sab_first=True):
    """
    Ensure SABnzbd is preferred over qBittorrent when Usenet is in play.
    Lowest number = highest priority.
    - If sab_first=True:  SAB=1, qB=2, others untouched (or bumped to >=3 if conflicting)
    - If sab_first=False: qB=1 (if present), SAB=2 (if present)
    """
    import requests
    H = {"X-Api-Key": api_key, "Content-Type":"application/json"}

    try:
        clients = requests.get(f"{app_url}/api/v3/downloadclient", headers=H, timeout=15).json()
    except Exception as e:
        print(f"[!] Could not list download clients for {app_url}: {e}")
        return

    # Identify common clients
    sab = [c for c in clients if c.get("implementation") == "SABnzbd"]
    qb  = [c for c in clients if c.get("implementation") == "QBittorrent"]

    # Nothing to do
    if not sab and not qb:
        print(f"[=] No SAB/qB clients in {app_url}; leaving priorities as-is")
        return

    desired = {}
    if sab_first and sab:
        # Prefer SAB
        desired.update({sab[0]["id"]: 1})
        if qb: desired.update({qb[0]["id"]: 2})
    else:
        # Prefer qB (or SAB absent)
        if qb: desired.update({qb[0]["id"]: 1})
        if sab: desired.update({sab[0]["id"]: 2})

    # Keep all other clients at >=3 if they would collide
    used = set(desired.values())
    next_free = 3
    for c in clients:
        cid = c["id"]
        if cid in desired:
            continue
        pr = int(c.get("priority", 3))
        if pr in used or pr < 3:
            pr = next_free
            next_free += 1
        desired[cid] = pr
        used.add(pr)

    # Apply changes
    changed = False
    for c in clients:
        cid = c["id"]
        new_pr = desired[cid]
        if int(c.get("priority", 0)) != new_pr:
            c["priority"] = new_pr
            try:
                r = requests.put(f"{app_url}/api/v3/downloadclient/{cid}", headers=H, json=c, timeout=15)
                if r.status_code not in (200, 202):
                    print(f"[!] Failed updating client {cid} in {app_url}: {r.status_code} {r.text[:200]}")
                else:
                    changed = True
            except Exception as e:
                print(f"[!] Error updating client {cid} in {app_url}: {e}")

    if changed:
        print(f"[+] Updated download client priorities in {app_url} (sab_first={sab_first})")
    else:
        print(f"[=] Download client priorities already correct in {app_url}")

def smart_protocol_tuning(app_url, api_key, prowlarr_api_key, sab_first=True):
    """
    If the app has a Usenet indexer: prefer Usenet and delay torrents.
    If not: prefer Torrent and set *no* delays.
    """
    import requests
    H = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    has_usenet = prowlarr_has_usenet_indexer(prowlarr_api_key)

    if has_usenet:
        # Usenet present → prefer it + delay torrents (e.g. 5 min)
        favor_usenet_everywhere(app_url, api_key, torrent_delay=TORRENT_INDEXER_DELAY, usenet_delay=0, sab_first=sab_first)
        set_download_client_priorities(app_url, api_key, sab_first=True)
        return

    # No Usenet indexers → prefer torrents, zero delays, leave SAB priority alone (likely absent)
    # 1) indexer config (prefer torrent)
    try:
        cfg = requests.get(f"{app_url}/api/v3/config/indexer", headers=H, timeout=15).json()
        changed = False
        if "enableTorrent" in cfg and not cfg["enableTorrent"]:
            cfg["enableTorrent"] = True; changed = True
        if "enableUsenet" in cfg and cfg["enableUsenet"]:
            # you can leave this True; but making intent explicit is fine:
            cfg["enableUsenet"] = False; changed = True
        if "preferUsenet" in cfg and cfg["preferUsenet"]:
            cfg["preferUsenet"] = False; changed = True
        if "preferredProtocol" in cfg and str(cfg["preferredProtocol"]).lower() != "torrent":
            cfg["preferredProtocol"] = "torrent"; changed = True
        if changed:
            body = {k:v for k,v in cfg.items() if k != "updateAutomatically"}
            requests.put(f"{app_url}/api/v3/config/indexer", headers=H, json=body, timeout=15)
    except Exception as e:
        print(f"[!] Torrent-prefer config update failed for {app_url}: {e}")

    # 2) delay profiles → zero delays
    try:
        profiles = requests.get(f"{app_url}/api/v3/delayprofile", headers=H, timeout=15).json()
        for p in profiles:
            changed = False
            if p.get("usenetDelay") != 0:   p["usenetDelay"] = 0; changed = True
            if p.get("torrentDelay") != 0:  p["torrentDelay"] = 0; changed = True
            if "preferredProtocol" in p:
                if isinstance(p["preferredProtocol"], int):
                    if p["preferredProtocol"] != 2:  # 2=torrent
                        p["preferredProtocol"] = 2; changed = True
                else:
                    if str(p["preferredProtocol"]).lower() != "torrent":
                        p["preferredProtocol"] = "torrent"; changed = True
            if changed:
                requests.put(f"{app_url}/api/v3/delayprofile/{p['id']}", headers=H, json=p, timeout=15)
    except Exception as e:
        print(f"[!] DelayProfile zeroing failed for {app_url}: {e}")

def set_arr_updates_to_docker(app_url, api_key):
    import requests
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    # 1) Read host config
    r = requests.get(f"{app_url}/api/v3/config/host", headers=headers, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"GET host config failed {r.status_code}: {r.text[:300]}")
    cfg = r.json()
    cfg_id = cfg.get("id")
    if cfg_id is None:
        raise RuntimeError("Host config did not include 'id'")

    # 2) Tweak update-related fields if present
    desired = dict(cfg)

    # Many builds use 'updateMechanism' with values like 'docker' or 'builtin'
    if "updateMechanism" in desired:
        desired["updateMechanism"] = "docker"

    # Older/newer keys you may see; set them only if they exist
    for k, v in [
        ("branch", "stable"),
        ("updateAutomatically", False),  # some builds use this
        ("automatic", False),            # older name in a few branches
    ]:
        if k in desired:
            desired[k] = v

    if desired == cfg:
        print(f"[=] Updates already set to Docker in {app_url}")
        return

    # 3) PUT back to /config/host/{id}
    u = requests.put(f"{app_url}/api/v3/config/host/{cfg_id}",
                     headers=headers, json=desired, timeout=15)
    if u.status_code not in (200, 202):
        raise RuntimeError(f"PUT host config failed {u.status_code}: {u.text[:400]}")
    print(f"[+] Set updates to Docker in {app_url}")

def wait_for_container_ready(service: str, port: int | None = None, timeout: int = 180) -> bool:
    """Wait until the container is healthy (if healthcheck exists) or, if none,
    until HTTP on localhost:port inside the container responds."""
    def _cid(svc):
        try:
            return subprocess.run(["docker", "compose", "ps", "-q", svc],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=10).stdout.strip() or None
        except Exception:
            return None

    cid = _cid(service)
    if not cid:
        print(f"[!] No container id for '{service}'")
        return False

    print(f"[~] Waiting for '{service}' to be ready (timeout {timeout}s)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # Resilient template: use Health if present, else fall back to State.Status
            tmpl = "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}"
            st = subprocess.run(["docker", "inspect", cid, "--format", tmpl],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=5).stdout.strip()
        except Exception:
            st = ""

        if st == "healthy":
            print(f"[✔] '{service}' is healthy")
            return True

        # If there's no healthcheck (or just 'running'), optionally probe HTTP inside the container
        if port is not None:
            try:
                # Alpine-based images may not have bash; use sh -lc
                probe = f'curl -fsS http://localhost:{port}/ || wget -qO- http://localhost:{port}/'
                rc = subprocess.run(["docker", "compose", "exec", "-T", service, "sh", "-lc", probe],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=8)
                if rc.returncode == 0 and rc.stdout:
                    print(f"[✔] '{service}' HTTP is responding on localhost:{port}")
                    return True
            except Exception:
                pass

        time.sleep(3)

    print(f"[!] '{service}' not ready before timeout (status seen: '{st}')")
    return False

def restart_container(service: str):
    try:
        subprocess.run(["docker", "compose", "restart", service], check=False, timeout=60)
        print(f"[=] Sent container restart to '{service}'")
    except Exception as e:
        print(f"[!] Restart '{service}' failed: {e}")

def wait_for_http(url, timeout, app_name):
    print(f"[~] Waiting for {app_name} to start (timeout {timeout}s)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code < 500:
                print(f"[✔] {app_name} is up ({r.status_code})")
                return True
        except Exception as e:
            print(f"   ...still waiting...")
        time.sleep(3)
    return False

def wait_for_file(path_str, timeout):
    p = Path(path_str)
    print(f"[~] Waiting for file {p} (timeout {timeout}s)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if p.exists():
            print(f"[✔] Found {p}")
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

def _sab_set_misc_kv(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    changed = False
    misc_start, next_section = None, None
    for i, line in enumerate(lines):
        if line.strip().lower() == "[misc]":
            misc_start = i
            for j in range(i+1, len(lines)):
                s = lines[j].strip()
                if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
                    next_section = j
                    break
            break
    rx = re.compile(rf"^\s*{re.escape(key)}\s*=", re.I)
    if misc_start is None:
        lines += ["", "[misc]", f"{key} = {value}"]
        changed = True
    else:
        end = next_section if next_section is not None else len(lines)
        found = False
        for idx in range(misc_start+1, end):
            if rx.match(lines[idx] or ""):
                cur = lines[idx].split("=", 1)[1].strip()
                if cur != value:
                    lines[idx] = f"{key} = {value}"
                    changed = True
                found = True
                break
        if not found:
            lines.insert(misc_start+1, f"{key} = {value}")
            changed = True
    return lines, changed

def ensure_sab_language(path_str: str, lang: str="en") -> bool:
    p = Path(path_str)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[-] Read SAB config failed: {e}")
        return False
    lines = text.splitlines()
    lines, changed = _sab_set_misc_kv(lines, "language", lang)
    if not changed:
        print("[=] SAB language already set")
        return False
    try:
        backup = p.with_suffix(p.suffix + ".bak")
        backup.write_text(text, encoding="utf-8", errors="ignore")
        out = "\n".join(lines) + ("\n" if not text.endswith("\n") else "")
        p.write_text(out, encoding="utf-8")
        print(f"[+] Set SAB language={lang}")
        return True
    except Exception as e:
        print(f"[-] Write SAB language failed: {e}")
        return False

def ensure_sab_server(path_str: str, name: str, host: str, port: int,
                      ssl: int, username: str, password: str,
                      connections: int=20, priority: int=0) -> bool:
    """
    Add/overwrite a [[name]] server block under [servers].
    Returns True if file changed.
    """
    if not host:
        print("[~] No SAB server host provided; skipping server bootstrap.")
        return False
    p = Path(path_str)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[-] Read SAB config failed: {e}")
        return False

    lines = text.splitlines()
    changed = False

    # find [servers] section bounds
    srv_start, next_section = None, None
    for i, line in enumerate(lines):
        if line.strip().lower() == "[servers]":
            srv_start = i
            for j in range(i+1, len(lines)):
                s = lines[j].strip()
                if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
                    next_section = j
                    break
            break

    def block_for(nm: str) -> list[str]:
        nm = nm.strip()
        return [
            f"  [[{nm}]]",
            f"    host = {host}",
            f"    port = {port}",
            f"    username = {username}",
            f"    password = {password}",
            f"    connections = {connections}",
            f"    ssl = {ssl}",
            "    enable = 1",
            f"    priority = {priority}",
            "    retention = 0",
            "    optional = 0",
            "    send_group = 0",
            "    fetch_by_msgid = 0",
            "    server_usenet_only = 1"
        ]

    # find existing [[name]] block
    def find_block_indices(start, end, nm):
        open_rx = re.compile(rf"^\s*\[\[\s*{re.escape(nm)}\s*\]\]\s*$", re.I)
        start_idx = None
        for j in range(start+1, end):
            if open_rx.match(lines[j] if j < len(lines) else ""):
                start_idx = j
                # block ends at next [[...]] or next top-level [section]
                for k in range(j+1, end):
                    s = lines[k].strip()
                    if s.startswith("[[") and s.endswith("]]"):
                        return (start_idx, k)
                return (start_idx, end)
        return (None, None)

    if srv_start is None:
        # create [servers] and add our block
        lines += ["", "[servers]"] + block_for(name)
        changed = True
    else:
        end = next_section if next_section is not None else len(lines)
        s_idx, e_idx = find_block_indices(srv_start, end, name)
        new_block = block_for(name)
        if s_idx is None:
            # append new server at end of [servers]
            insert_at = end
            lines[insert_at:insert_at] = new_block
            changed = True
        else:
            # replace existing block if content differs
            cur = lines[s_idx:e_idx]
            if cur != new_block:
                lines[s_idx:e_idx] = new_block
                changed = True

    if not changed:
        print(f"[=] SAB server '{name}' already configured")
        return False

    try:
        backup = p.with_suffix(p.suffix + ".bak")
        backup.write_text(text, encoding="utf-8", errors="ignore")
        out = "\n".join(lines) + ("\n" if not text.endswith("\n") else "")
        p.write_text(out, encoding="utf-8")
        print(f"[+] Configured SAB server '{name}' ({host}:{port}, ssl={ssl})")
        return True
    except Exception as e:
        print(f"[-] Write SAB server failed: {e}")
        return False

def ensure_sab_folders(path_str: str, temp_dir: str, complete_dir: str) -> bool:
    """
    Set [misc] download_dir (temp/incomplete) and complete_dir (finished) in sabnzbd.ini.
    Returns True if the file was changed.
    """
    p = Path(path_str)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[-] Cannot read SAB config at {p}: {e}")
        return False

    lines = text.splitlines()
    changed = False

    # find [misc] section
    misc_start, next_section = None, None
    for i, line in enumerate(lines):
        if line.strip().lower() == "[misc]":
            misc_start = i
            for j in range(i+1, len(lines)):
                s = lines[j].strip()
                if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
                    next_section = j
                    break
            break

    def set_kv(name: str, value: str):
        nonlocal changed, lines, misc_start, next_section
        rx = re.compile(rf"^\s*{re.escape(name)}\s*=", re.I)
        if misc_start is None:
            # create [misc] at end
            lines += ["", "[misc]", f"{name} = {value}"]
            misc_start = len(lines) - 2
            changed = True
        else:
            end = next_section if next_section is not None else len(lines)
            # try to find existing key in [misc]
            for idx in range(misc_start+1, end):
                if rx.match(lines[idx] or ""):
                    cur = lines[idx].split("=", 1)[1].strip()
                    if cur != value:
                        lines[idx] = f"{name} = {value}"
                        changed = True
                    return
            # not found → insert right after [misc]
            lines.insert(misc_start+1, f"{name} = {value}")
            changed = True

    # Set desired paths
    set_kv("download_dir", temp_dir)    # "Temporary Download Folder"
    set_kv("complete_dir", complete_dir)  # "Completed Download Folder"
    # Clear dir_base if present so it doesn't override the above
    # (empty string is accepted; SAB will honor explicit dirs)
    set_kv("dir_base", "")

    if not changed:
        print("[=] SAB folders already set")
        return False

    try:
        backup = p.with_suffix(p.suffix + ".bak")
        backup.write_text(text, encoding="utf-8", errors="ignore")
        out = "\n".join(lines)
        if not out.endswith("\n"): out += "\n"
        p.write_text(out, encoding="utf-8")
        print(f"[✔] Updated SAB folders in {p} (backup {backup.name})")
        return True
    except Exception as e:
        print(f"[-] Failed writing SAB folders: {e}")
        return False

def ensure_sab_categories(path_str: str, categories: list[str]) -> bool:
    """
    Ensure SAB has [[<cat>]] blocks in the [categories] section.
    Returns True if file changed (requires restart), False otherwise.
    """
    p = Path(path_str)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[-] Cannot read SAB config at {p}: {e}")
        return False

    lines = text.splitlines()
    changed = False

    # find [categories] section boundary
    cat_start, next_section = None, None
    for i, line in enumerate(lines):
        if line.strip().lower() == "[categories]":
            cat_start = i
            # find where next top-level [section] begins
            for j in range(i+1, len(lines)):
                s = lines[j].strip()
                if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
                    next_section = j
                    break
            break

    # existing cat names: match '[[name]]' under [categories]
    existing = set()
    if cat_start is not None:
        end = next_section if next_section is not None else len(lines)
        for j in range(cat_start+1, end):
            m = re.match(r"\s*\[\[\s*([^\]]+?)\s*\]\]\s*$", lines[j])
            if m:
                existing.add(m.group(1).strip().lower())

    def cat_block(name: str) -> list[str]:
        # safe, minimal block; SAB fills defaults
        return [
            f"  [[{name}]]",
            "    priority = -100",
            "    pp = 3",
            "    script = ",
            f"    dir = {name}",
            "    newzbin = "
        ]

    if cat_start is None:
        # create [categories] at end
        lines += ["", "[categories]"]
        for c in categories:
            lines += cat_block(c)
        changed = True
    else:
        # append missing categories inside [categories]
        missing = [c for c in categories if c.lower() not in existing]
        if missing:
            insert_at = next_section if next_section is not None else len(lines)
            block = []
            for c in missing:
                block += cat_block(c)
            lines[insert_at:insert_at] = block
            changed = True

    if not changed:
        print("[=] SAB categories already present:", ", ".join(sorted(existing)))
        return False

    # backup + write
    try:
        backup = p.with_suffix(p.suffix + ".bak")
        backup.write_text(text, encoding="utf-8", errors="ignore")
        out = "\n".join(lines)
        if not out.endswith("\n"): out += "\n"
        p.write_text(out, encoding="utf-8")
        print(f"[✔] Added SAB categories {categories} in {p} (backup {backup.name})")
        return True
    except Exception as e:
        print(f"[-] Failed writing SAB categories: {e}")
        return False

def ensure_sab_whitelist(path_str: str, wanted_hosts: list[str]) -> bool:
    """
    Ensure sabnzbd.ini has [misc] host_whitelist including wanted_hosts.
    Returns True if a change was made (and container restarted), False otherwise.
    """
    p = Path(path_str)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[-] Cannot read SAB config at {p}: {e}")
        return False

    lines = text.splitlines()
    in_misc = False
    misc_start = None
    wl_idx = None

    # Locate [misc] section and any existing host_whitelist
    for i, line in enumerate(lines):
        s = line.strip()
        if s.lower() == "[misc]":
            in_misc = True
            misc_start = i
            continue
        if in_misc and s.startswith("[") and s.endswith("]"):
            # next section begins; stop scanning for whitelist
            break
        if in_misc and re.match(r"^\s*host_whitelist\s*=", line, flags=re.I):
            wl_idx = i

    changed = False
    def join_hosts(hosts: list[str]) -> str:
        # SAB accepts comma-separated list
        hosts = ",".join(dict.fromkeys([h for h in hosts if h]))  # dedupe, keep order
        print(f"[=] Setting whitelisted hosts: {hosts}")
        return hosts

    if misc_start is None:
        # no [misc] section; create one at end
        lines += ["", "[misc]", f"host_whitelist = {join_hosts(SAB_WHITELIST)}"]
        changed = True
    else:
        if wl_idx is not None:
            current_val = lines[wl_idx].split("=", 1)[1].strip()
            # split on commas or whitespace
            current_hosts = [h for h in re.split(r"[, \t]+", current_val) if h]
            merged = list(dict.fromkeys(current_hosts + wanted_hosts))
            if merged != current_hosts:
                lines[wl_idx] = f"host_whitelist = {join_hosts(merged)}"
                changed = True
        else:
            # insert right after [misc]
            insert_at = misc_start + 1
            lines.insert(insert_at, f"host_whitelist = {join_hosts(wanted_hosts)}")
            changed = True

    if not changed:
        print("[=] SAB host_whitelist already includes required hosts.")
        return False

    # Backup and write
    try:
        backup = p.with_suffix(p.suffix + ".bak")
        backup.write_text(text, encoding="utf-8", errors="ignore")
        out = "\n".join(lines)
        if not out.endswith("\n"): out += "\n"
        p.write_text(out, encoding="utf-8")
        print(f"[✔] Updated SAB host_whitelist in {p} (backup at {backup.name})")
    except Exception as e:
        print(f"[-] Failed writing SAB config: {e}")
        return False

    # Restart SAB container so the change takes effect
    try:
        subprocess.run(["docker", "restart", SABNZBD_CONTAINER], check=False, timeout=30)
        time.sleep(5)
        print(f"[=] Restarted container '{SABNZBD_CONTAINER}'")
    except Exception as e:
        print(f"[!] Could not restart SAB container '{SABNZBD_CONTAINER}': {e}")

    return True

def parse_sab_api_key(path_str) -> str:
    try:
        # very lightweight .ini scrape; avoids needing configparser section names
        with open(path_str, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip().lower().startswith("api_key"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip()
    except Exception as e:
        print(f"[-] Failed to parse SAB API key from {path_str}: {e}")
    return ""

def get_arr_keys():
    SONARR_API_KEY = parse_api_key_from_config(SONARR_CFG)
    RADARR_API_KEY = parse_api_key_from_config(RADARR_CFG)
    PROWLARR_API_KEY = parse_api_key_from_config(PROWLARR_CFG)
    print (f"[=] Discovered API keys :: Radarr: {RADARR_API_KEY}, Sonarr: {SONARR_API_KEY}, Prowlarr: {PROWLARR_API_KEY}")
    return RADARR_API_KEY, SONARR_API_KEY, PROWLARR_API_KEY

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
                print(f"[✔] Set auth for {base_url} (method={attempt_method})")
                return True
            else:
                print(f"[!] PUT host config failed for {base_url} ({attempt_method}): {u.status_code} {u.text[:300]}")
        except Exception as e:
            print(f"[!] Error updating host config for {base_url} ({attempt_method}): {e}")
    return False

def prow_get_apps(api_key):
    headers = {"X-Api-Key": api_key}
    r = requests.get(f"{PROWLARR_URL}/api/v1/applications",
                     headers=headers, timeout=PROWLARR_REQ_TIMEOUT)
    r.raise_for_status()
    return r.json()

def prow_get_indexers(api_key):
    headers = {"X-Api-Key": api_key}
    r = requests.get(f"{PROWLARR_URL}/api/v1/indexer", headers=headers, timeout=PROWLARR_REQ_TIMEOUT)
    r.raise_for_status()
    return r.json()

def _norm(s: str) -> str:
    return re.sub(r'\W+', '', (s or '').lower())

def _merge_fields(existing: list[dict], new_fields: list[dict]) -> list[dict]:
    out = list(existing or [])
    by_name = {f.get("name"): i for i,f in enumerate(out) if f.get("name")}
    for nf in new_fields or []:
        n = nf.get("name")
        if not n:
            continue
        if n in by_name:
            out[by_name[n]]["value"] = nf.get("value")
        else:
            out.append({"name": n, "value": nf.get("value")})
    return out

def http_put_with_retries(url, headers, json_obj, tries=5, delay=3, timeout=PROWLARR_REQ_TIMEOUT):
    for i in range(tries):
        try:
            r = requests.put(url, headers=headers, json=json_obj, timeout=timeout)
            return r
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
            if i == tries - 1: raise
            print(f"  ...PUT retry {i+1}/{tries} after error: {e}")
            time.sleep(delay)

def post_with_retries(url, headers=None, payload=None, tries=4, delay=4, timeout=PROWLARR_REQ_TIMEOUT):
    for i in range(tries):
        try:
            return requests.post(url, headers=headers or {}, json=payload, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
            if i == tries - 1:
                raise
            print(f"  ...POST retry {i+1}/{tries} to {url} after error: {e}")
            time.sleep(delay)

def add_app(app_name: str, base_url_in_docker: str, app_api_key: str, prowlarr_api_key):
    # Check if an app already exists with this Name or Implementation
    headers = {"X-Api-Key": prowlarr_api_key}
    try:
        apps = prow_get_apps(prowlarr_api_key)
    except Exception as e:
        print(f"[!] Could not list applications: {e}")
        apps = []

    existing = next((a for a in apps
                     if a.get("name") == app_name or a.get("implementation") == app_name), None)

    # Build/update fields
    def ensure_fields(fields):
        fields = list(fields or [])
        def set_field(n,v):
            for f in fields:
                if f.get("name") == n:
                    f["value"] = v
                    return
            fields.append({"name": n, "value": v})
        set_field("apiKey", app_api_key)
        set_field("baseUrl", base_url_in_docker)
        set_field("ProwlarrUrl", PROWLARR_URL_IN_DOCKER)
        return fields

    if existing:
        app_id = existing.get("id")
        obj = dict(existing)
        obj["fields"] = ensure_fields(existing.get("fields"))
        print(f"[=] App {app_name} exists in Prowlarr (id={app_id}); updating URLs/keys")
        try:
            r = http_put_with_retries(f"{PROWLARR_URL}/api/v1/applications/{app_id}",
                                      headers=headers, json_obj=obj)
            if r.status_code in (200, 202):
                print(f"[✔] Updated app {app_name} in Prowlarr (id={app_id})")
                time.sleep(2)  # small settle
                return app_id
            else:
                print(f"[!] Prowlarr update app {app_name} failed: {r.status_code} {r.text[:300]}")
                return app_id
        except Exception as e:
            print(f"[!] Prowlarr update app {app_name} error: {e}")
            return app_id

    # Create new if not found
    payload = {
        "name": app_name,
        "implementation": app_name,                 # "Sonarr" or "Radarr"
        "configContract": f"{app_name}Settings",
        "fields": ensure_fields([]),
        "enable": True
    }
    url = f"{PROWLARR_URL}/api/v1/applications"
    print(f"[~] Adding app {app_name} in Prowlerr pointing to {base_url_in_docker}")
    try:
        r = post_with_retries(url, headers=headers, payload=payload,
                              tries=3, delay=4, timeout=PROWLARR_REQ_TIMEOUT)
        if r.status_code in (200, 201):
            app_id = r.json().get("id")
            print(f"[✔] Added app {app_name} in Prowlarr (id={app_id})")
            time.sleep(2)
            return app_id
        elif r.status_code == 400 and "Should be unique" in r.text:
            # Another writer beat us; fetch the existing and continue
            print(f"[=] App {app_name} already exists in Prowlarr (400 unique); will update instead")
            apps = prow_get_apps(prowlarr_api_key)
            existing = next((a for a in apps if a.get("name") == app_name), None)
            return existing.get("id") if existing else None
        else:
            print(f"[!] Prowlarr add_app {app_name} failed: {r.status_code} {r.text[:300]}")
            return None
    except Exception as e:
        print(f"[!] Prowlarr add_app {app_name} error after retries: {e}")
        return None

def set_app_synclevel_and_sync(app_name: str, api_key: str):
    headers = {"X-Api-Key": api_key}
    try:
        apps = prow_get_apps(api_key)
    except Exception as e:
        print(f"[!] Could not list applications: {e}")
        return

    # Prefer exact implementation match; fall back to name
    app = next((a for a in apps if a.get("implementation") == app_name), None)
    if not app:
        app = next((a for a in apps if a.get("name") == app_name), None)
    if not app:
        print(f"[-] {app_name} app not found to set syncLevel")
        return

    app_id = app.get("id")
    fields = app.get("fields") or []
    # ensure ProwlarrUrl is set to in-docker URL
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
        try:
            u = http_put_with_retries(f"{PROWLARR_URL}/api/v1/applications/{app_id}",
                                      headers=headers, json_obj=obj,
                                      tries=5, delay=3, timeout=PROWLARR_REQ_TIMEOUT)
            if u.status_code in (200, 202):
                print(f"[✔] Set {app_name} syncLevel to {full_sync}")
                break
            else:
                print(f"[!] PUT syncLevel={full_sync} -> {u.status_code}: {u.reason}: {u.text[:300]}")
        except Exception as e:
            print(f"[!] PUT syncLevel error: {e}")

def list_tags(api_key):
    headers = {"X-Api-Key": api_key}
    r = requests.get(f"{PROWLARR_URL}/api/v1/tag", headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()

def ensure_tag_id(label: str, api_key) -> int | None:
    headers = {"X-Api-Key": api_key}
    try:
        tags = list_tags(api_key)
        for t in tags:
            if t.get("label") == label:
                return t.get("id")
    except Exception as e:
        print(f"[-] Could not list tags: {e}")
    try:
        r = requests.post(f"{PROWLARR_URL}/api/v1/tag",
                          headers=headers,
                          json={"label": label},
                          timeout=10)
        if r.status_code in (200, 201):
            tid = r.json().get("id")
            print(f"[✔] Created tag '{label}' (id={tid})")
            return tid
        else:
            print(f"[-] Create tag '{label}' failed: {r.status_code} {r.text[:300]}")
    except Exception as e:
        print(f"[-] Create tag error: {e}")
    return None

def get_proxy_base(api_key):
    headers = {"X-Api-Key": api_key}
    for base in ("/api/v1/indexerproxy", "/api/v1/proxy"):
        try:
            r = requests.get(f"{PROWLARR_URL}{base}", headers=headers, timeout=10)
            if r.status_code in (200, 204, 405):
                print(f"[=] Using proxy endpoint base: {base}")
                return base
        except Exception:
            pass
    print("[-] Could not determine proxy endpoint base. Fallback: /api/v1/indexerproxy")
    return "/api/v1/indexerproxy"

def list_proxies(proxy_base, api_key):
    headers = {"X-Api-Key": api_key}
    r = requests.get(f"{PROWLARR_URL}{proxy_base}", headers=headers, timeout=15)
    if r.status_code != 200:
        print(f"[-] list_proxies {proxy_base} status {r.status_code}: {r.text[:200]}")
        return None
    try:
        return r.json()
    except Exception as e:
        print(f"[-] list_proxies JSON error: {e}; body: {r.text[:200]}")
        return None

def create_proxy_if_needed(api_key):
    headers = {"X-Api-Key": api_key}
    if not CREATE_PROXY:
        print("[=] Proxy creation disabled (CREATE_PROXY=false)")
        return None

    proxy_base = get_proxy_base(api_key)
    tag_id = ensure_tag_id(CF_TAG_LABEL, api_key)

    existing = list_proxies(proxy_base, api_key)
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
    r1 = requests.post(url, headers=headers, json=payload, timeout=20)
    if r1.status_code in (200, 201):
        pid = r1.json().get("id")
        print(f"[✔] Created proxy '{FSR_NAME}' (id={pid}) at {proxy_base}")
        time.sleep(5)
        return pid

    alt_base = "/api/v1/proxy" if proxy_base.endswith("indexerproxy") else "/api/v1/indexerproxy"
    print(f"[!] POST {proxy_base} -> {r1.status_code}. Trying {alt_base}...")
    r2 = requests.post(f"{PROWLARR_URL}{alt_base}", headers=headers, json=payload, timeout=20)
    if r2.status_code in (200, 201):
        pid = r2.json().get("id")
        print(f"[✔] Created proxy '{FSR_NAME}' (id={pid}) at {alt_base}")
        time.sleep(5)
        return pid

    print(f"[-] Proxy create failed. {proxy_base} -> {r2.status_code} {r2.text[:300]}  |  {alt_base} -> {r2.status_code} {r2.text[:300]}")
    return None

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
        "fields": []
    }
    use_proxy_flag = use_proxy and not _is_usenet(defn)
    payload["useProxy"] = use_proxy_flag
    if proxy_id and use_proxy_flag:
        payload["proxy"] = proxy_id
        payload["proxyId"] = proxy_id

    overrides = _collect_overrides_for(defn)
    payload["fields"] = _build_fields_with_overrides(defn, overrides)
    return payload

def get_indexer_definitions(api_key):
    headers = {"X-Api-Key": api_key}
    print("[~] Fetching indexer definitions")
    r = requests.get(f"{PROWLARR_URL}/api/v1/indexer/schema", headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

def create_indexer_with_optional_proxy(defs, name: str, proxy_id: int | None, api_key):
    headers = {"X-Api-Key": api_key}
    wanted = _canon(name)
    match = next((d for d in defs if _canon(d.get("name","")) == wanted
                               or _canon(d.get("implementationName","")) == wanted), None)
    if not match:
        cands = [d for d in defs if wanted in _canon(d.get("name","")) or wanted in _canon(d.get("implementationName",""))]
        if not cands:
            print(f"[-] No definition found for '{name}'.")
            return
        match = cands[0]

    try:
        existing = prow_get_indexers(api_key)
    except Exception as e:
        print(f"[!] Could not list indexers: {e}")
        existing = []

    intended_name = match.get("name") or match.get("implementationName") or name
    current = next((i for i in existing if _norm(i.get("name")) == _norm(intended_name)), None)

    tag_id = ensure_tag_id(CF_TAG_LABEL, api_key)
    overrides = _collect_overrides_for(match)
    new_fields = _build_fields_with_overrides(match, overrides)

    use_proxy_flag = (not _is_usenet(match)) and bool(proxy_id)

    if current:
        obj = dict(current)
        obj["tags"] = [tag_id] if tag_id is not None else (obj.get("tags") or [])
        obj["useProxy"] = use_proxy_flag
        if use_proxy_flag:
            obj["proxy"] = proxy_id
            obj["proxyId"] = proxy_id
        obj["fields"] = _merge_fields(current.get("fields"), new_fields)

        try:
            u = http_put_with_retries(f"{PROWLARR_URL}/api/v1/indexer/{current.get('id')}",
                                      headers=headers, json_obj=obj)
            if u.status_code in (200, 202):
                print(f"[=] Indexer '{intended_name}' exists; updated settings.")
            else:
                print(f"[!] Update indexer '{intended_name}' -> {u.status_code} {u.text[:300]}")
        except Exception as e:
            print(f"[!] Update indexer '{intended_name}' error: {e}")
        return

    payload = _create_indexer_payload_from_def(match, tag_id, proxy_id, use_proxy=not _is_usenet(match))
    try:
        r = post_with_retries(f"{PROWLARR_URL}/api/v1/indexer", headers=headers, payload=payload,
                              tries=3, delay=6, timeout=PROWLARR_REQ_TIMEOUT)
        if r.status_code in (200, 201):
            idx_id = r.json().get("id")
            proto = (match.get("protocol") or "").lower()
            print(f"[✔] Created indexer '{intended_name}' (id={idx_id}, protocol={proto})")
        elif r.status_code == 400 and "Should be unique" in r.text:
            print(f"[=] Indexer '{intended_name}' already exists; will update instead.")
            try:
                ex = prow_get_indexers(api_key)
                cur = next((i for i in ex if _norm(i.get("name")) == _norm(intended_name)), None)
                if cur:
                    # fall into update path by recursion
                    create_indexer_with_optional_proxy(defs, intended_name, proxy_id, api_key)
            except Exception:
                pass
        else:
            print(f"[!] Failed creating indexer '{intended_name}': {r.status_code} {r.text[:400]}")
    except Exception as e:
        print(f"[!] Exception creating indexer '{intended_name}': {e}")

def prowlarr_has_usenet_indexer(api_key: str) -> bool:
    """Return True if Prowlarr has any enabled Usenet indexer."""
    try:
        idxs = prow_get_indexers(api_key)
    except Exception:
        return False
    return any((i.get("enable") is True) and (str(i.get("protocol","")).lower() == "usenet")
               for i in (idxs or []))

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
            print(f"[✔] Found temp qBittorrent password in logs: {pw}")
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
    if not wait_for_http(QBT_API_BASE, QBT_API_WAIT_TIMEOUT, 'qBittorrent'):
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
                print("[✔] Set known qBittorrent credentials via API")
                return (UI_USER, UI_PASS)
            else:
                print("[!] Failed setting known qBittorrent credentials; using temp password")
                return ("admin", temp_pw)
        else:
            print(f"[=] Keeping temporary password. (username=admin, password={temp_pw})")
            return ("admin", temp_pw)

    # No temp, but the user provided a new pass — just return those
    return (UI_USER, UI_PASS)

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
        "priority": 2,
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
            print(f"[✔] Added qBittorrent to {app_url}")
        elif r.status_code == 409:
            print(f"[=] qBittorrent already exists in {app_url}")
        else:
            print(f"[!] Failed to add qBittorrent to {app_url}: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[!] Exception adding qBittorrent to {app_url}: {e}")

def ensure_sab_client(app_url, api_key, name="sabnzbd",
                      host="sabnzbd", port=8080,
                      sab_api_key="", category=None, use_ssl=False,
                      username="", password=""):
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    # 1) Skip if already added
    try:
        existing = requests.get(f"{app_url}/api/v3/downloadclient", headers=headers, timeout=10).json()
        for cli in existing:
            if cli.get("implementation") == "SABnzbd":
                print(f"[=] SABnzbd already present in {app_url} (id={cli.get('id')})")
                return True
    except Exception as e:
        print(f"[!] Failed to list download clients for {app_url}: {e}")
        return False

    # 2) Try to fetch schema (robust)
    try:
        schemas = requests.get(f"{app_url}/api/v3/downloadclient/schema", headers=headers, timeout=10).json()
        sab_schema = next((s for s in schemas if s.get("implementation") == "SABnzbd"), None)
    except Exception as e:
        sab_schema = None
        print(f"[!] Could not fetch SAB schema from {app_url}: {e}")

    # Values we plan to set
    values = {
        "host": host,
        "port": port,
        "useSsl": bool(use_ssl),
        "urlBase": "",
        "apiKey": sab_api_key,
        "username": username or "",
        "password": password or "",
    }

    fields = []
    if sab_schema and sab_schema.get("fields"):
        # Prefer exact schema field names
        schema_fields = [f.get("name") for f in sab_schema["fields"] if f.get("name")]
        # Pick the right category field if present
        for cat in ("movieCategory", "tvCategory", "category"):
            if category and cat in schema_fields:
                values[cat] = category
                break

        # Build fields respecting schema order and defaults
        for f in sab_schema["fields"]:
            nm = f.get("name")
            if nm in values:
                fields.append({"name": nm, "value": values[nm]})
            else:
                fields.append({"name": nm, "value": f.get("value", f.get("defaultValue", ""))})

        payload = {
            "enable": True,
            "protocol": "usenet",
            "priority": 1,
            "configContract": sab_schema.get("configContract", "SABnzbdSettings"),
            "implementation": sab_schema.get("implementation", "SABnzbd"),
            "implementationName": sab_schema.get("implementationName", "SABnzbd"),
            "name": name,
            "fields": fields
        }
    else:
        # Fallback (older/newer builds): try common names; this is what used to 400
        fields = [
            {"name": "host", "value": host},
            {"name": "port", "value": port},
            {"name": "useSsl", "value": bool(use_ssl)},
            {"name": "urlBase", "value": ""},
            {"name": "apiKey", "value": sab_api_key},
            {"name": "username", "value": username or ""},
            {"name": "password", "value": password or ""},
        ]
        # Heuristic: choose movieCategory for Radarr, tvCategory for Sonarr
        if category:
            if "radarr" in app_url.lower():
                fields.append({"name": "movieCategory", "value": category})
            else:
                fields.append({"name": "tvCategory", "value": category})

        payload = {
            "enable": True,
            "protocol": "usenet",
            "priority": 1,
            "configContract": "SABnzbdSettings",
            "implementation": "SABnzbd",
            "implementationName": "SABnzbd",
            "name": name,
            "fields": fields
        }

    r = requests.post(f"{app_url}/api/v3/downloadclient", headers=headers, json=payload, timeout=15)
    if r.status_code in (200, 201):
        print(f"[✔] Added SABnzbd to {app_url}")
        return True
    elif r.status_code == 409:
        print(f"[=] SABnzbd already exists in {app_url}")
        return True
    else:
        print(f"[!] Failed to add SABnzbd to {app_url}: {r.status_code} {r.text}")
        return False

def qbt_ensure_paths(base: str, username: str, password: str,
                     completed=COMPLETED_DOWNLOADS, incomplete=INCOMPLETE_DOWNLOADS) -> bool:
    """
    Set global completed & incomplete download dirs in qBittorrent.
    Works on qB 4.1+.
    """
    sess = qbt_login_session(base, username, password)
    if not sess:
        print("[-] qB login failed; cannot set paths")
        return False

    prefs = {
        # Completed path
        "save_path": completed,
        # Incomplete path
        "temp_path_enabled": True,   # modern key
        "temp_path": incomplete,
        "use_temp_path": True,       # legacy toggle (harmless if ignored)
        # Optional quality-of-life:
        # "create_subfolder_enabled": False,           # don’t create per-torrent subfolder
        # "append_extension_enabled": True, "append_extension": ".!qB"
    }
    ok = qbt_set_preferences(sess, base, prefs)
    print(("[+]" if ok else "[=]") + f" qB global paths set: complete={completed}, incomplete={incomplete}")
    return ok

# ---------------------------
def main():
    # Wait for apps requiring config to start
    wait_for_http(RADARR_URL, WAIT_TIMEOUT, 'Radarr')
    wait_for_http(SONARR_URL, WAIT_TIMEOUT, 'Sonarr')
    wait_for_http(PROWLARR_URL, WAIT_TIMEOUT, 'Prowlarr')
    wait_for_http(QBT_API_BASE, WAIT_TIMEOUT, 'qBittorrent')

    # Read API keys from configs
    RADARR_API_KEY, SONARR_API_KEY, PROWLARR_API_KEY = get_arr_keys()
    if not (PROWLARR_API_KEY and SONARR_API_KEY and RADARR_API_KEY):
        print("[-] Missing API keys; ensure apps initialized or pass via env.")
        exit(1)

    # Update Sonarr and Radarr so they do not auto-update
    set_arr_updates_to_docker(RADARR_URL, RADARR_API_KEY)
    set_arr_updates_to_docker(SONARR_URL, SONARR_API_KEY)

    # Get temp password from QBT in order to login to set updated password
    qbt_user, qbt_pass = prepare_qbt_via_api()
    if not qbt_user and UI_PASS:
        # No temp found; assume new pass is already set
        qbt_user, qbt_pass = (UI_USER, UI_PASS)

    qbt_ensure_paths(QBT_API_BASE, qbt_user, qbt_pass,
                 completed=COMPLETED_DOWNLOADS,
                 incomplete=INCOMPLETE_DOWNLOADS)
    
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

    # Set web auth on *Arr
    sonarr_auth_ok = set_arr_auth(SONARR_URL, SONARR_API_KEY, 3, UI_USER, UI_PASS, AUTH_METHOD)
    radarr_auth_ok = set_arr_auth(RADARR_URL, RADARR_API_KEY, 3, UI_USER, UI_PASS, AUTH_METHOD)
    prowlarr_auth_ok = set_arr_auth(PROWLARR_URL, PROWLARR_API_KEY, 1, UI_USER, UI_PASS, AUTH_METHOD)

    # Register apps in Prowlarr + set sync level (use in-Docker URLs)
    add_app("Sonarr", SONARR_URL_IN_DOCKER, SONARR_API_KEY, PROWLARR_API_KEY)
    add_app("Radarr", RADARR_URL_IN_DOCKER, RADARR_API_KEY, PROWLARR_API_KEY)
    set_app_synclevel_and_sync("Sonarr", PROWLARR_API_KEY)
    set_app_synclevel_and_sync("Radarr", PROWLARR_API_KEY)

    # Create FlareSolverr proxy (optional) and seed indexers
    proxy_id = create_proxy_if_needed(PROWLARR_API_KEY)
    defs = get_indexer_definitions(PROWLARR_API_KEY)
    for idx in INDEXERS:
        create_indexer_with_optional_proxy(defs, idx, proxy_id, PROWLARR_API_KEY)

    print(f"[~] Setting Radarr download path to {CONTAINER_MOVIES_DIR}")
    created_r = ensure_root_folder(RADARR_URL, RADARR_API_KEY, CONTAINER_MOVIES_DIR)
    print(f"[~] Setting Sonarr download path to {CONTAINER_SHOWS_DIR}")
    created_s = ensure_root_folder(SONARR_URL, SONARR_API_KEY, CONTAINER_SHOWS_DIR)

    if not (created_r or created_s):
        print("[i] If you saw 'folder does not exist' or 'not writable', verify volume binds in docker-compose and PUID/PGID.")

    # Add SABnzbd to Sonarr/Radarr (containers will reach it at sabnzbd:8080 on media_net container network))
    SABNZBD_API_KEY = ""
    if wait_for_file(SABNZBD_CFG, WAIT_TIMEOUT):
        changed_wl = ensure_sab_whitelist(SABNZBD_CFG, SAB_WHITELIST)
        changed_cat = ensure_sab_categories(SABNZBD_CFG, SAB_CATS)
        changed_dirs = ensure_sab_folders(SABNZBD_CFG, temp_dir=INCOMPLETE_DOWNLOADS, complete_dir=COMPLETED_DOWNLOADS)
        if SAB_CONFIG_PROVIDER:
            changed_lang= ensure_sab_language(SABNZBD_CFG, SAB_LANG)
            changed_srv = ensure_sab_server(
                SABNZBD_CFG,
                name=SAB_SRV_NAME,
                host=SAB_SRV_HOST,
                port=SAB_SRV_PORT,
                ssl=SAB_SRV_SSL,
                username=SAB_SRV_USER,
                password=SAB_SRV_PASS,
                connections=SAB_SRV_CONNS,
                priority=SAB_SRV_PRIORITY)
        if changed_wl or changed_cat or changed_dirs or (SAB_CONFIG_PROVIDER and (changed_lang or changed_srv)):
            restart_container(SABNZBD_CONTAINER)
            if not wait_for_container_ready(SABNZBD_CONTAINER, port=SAB_HTTP_PORT, timeout=WAIT_TIMEOUT):
                time.sleep(8)
        SABNZBD_API_KEY = parse_sab_api_key(SABNZBD_CFG) or ""
    else:
        print("[-] SAB config file not found; skipping SAB setup.")

    if SABNZBD_API_KEY:
        ok1 = ensure_sab_client(
            SONARR_URL, SONARR_API_KEY,
            host="sabnzbd", port=8080,
            sab_api_key=SABNZBD_API_KEY,
            category="tv",
            use_ssl=False
        )
        ok2 = ensure_sab_client(
            RADARR_URL, RADARR_API_KEY,
            host="sabnzbd", port=8080,
            sab_api_key=SABNZBD_API_KEY,
            category="movies",
            use_ssl=False
        )
        if not (ok1 and ok2):
            print("[~] SAB add failed at first attempt; re-checking categories and retrying once...")
            ensure_sab_categories(SABNZBD_CFG, SAB_CATS)
            time.sleep(3)
            ensure_sab_client(SONARR_URL, SONARR_API_KEY, host="sabnzbd", port=8080,
                              sab_api_key=SABNZBD_API_KEY, category="tv", use_ssl=False)
            ensure_sab_client(RADARR_URL, RADARR_API_KEY, host="sabnzbd", port=8080,
                              sab_api_key=SABNZBD_API_KEY, category="movies", use_ssl=False)
    else:
        print("[-] SABNZBD_API_KEY missing; skip adding SAB client.")

    set_prowlarr_indexer_priorities(usenet_prio=10, torrent_prio=30, api_key=PROWLARR_API_KEY)
    smart_protocol_tuning(RADARR_URL, RADARR_API_KEY, PROWLARR_API_KEY)
    smart_protocol_tuning(SONARR_URL, SONARR_API_KEY, PROWLARR_API_KEY)

    restart_container(SONARR_CONTAINER)
    restart_container(RADARR_CONTAINER)
    restart_container(PROWLARR_CONTAINER)
    wait_for_http(SONARR_URL, WAIT_TIMEOUT, 'Sonarr')
    wait_for_http(RADARR_URL, WAIT_TIMEOUT, 'Radarr')
    wait_for_http(PROWLARR_URL, WAIT_TIMEOUT, 'Prowlarr')

    print("[✓] Bootstrap complete")

if __name__ == "__main__":
    main()
