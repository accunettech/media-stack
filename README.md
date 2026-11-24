# Media stack

Jellyfin streaming with Sonarr, Radarr, and Lidarr automation, Prowlarr indexers, SABnzbd Usenet downloads, qBittorrent through NordVPN/Gluetun, and a local dashboard.

## Install

Requires Linux, Python 3.10+, Docker Engine with Compose v2.17+, `/dev/net/tun`, and space for downloads and transcodes. A GPU is optional. Setup can install prerequisites on Debian/Ubuntu; install Python/venv and Docker yourself on other distributions. Run setup on the Docker host so hardware detection sees the correct devices.

1. Clone the repo and review `.env`. Set the media paths, `OWNER_UID`/`OWNER_GID`, hostname, and provider usernames for this host. Paths under `CONTAINER_HOME` follow the clone automatically. Keep `CONF_HOME` on a fast local disk: it holds application state and Jellyfin's transcode cache.
2. Supply secrets from your vault as exported environment variables, or create `.env.secrets` once and fill it locally:

   ```bash
   test -e .env.secrets || install -m 600 .env.secrets.example .env.secrets
   ```

   | Variable | Purpose |
   |---|---|
   | `OPENVPN_PASSWORD` | NordVPN **service** password for `OPENVPN_USER` |
   | `UI_PASS` | Sonarr, Radarr, Lidarr, Prowlarr, qBittorrent and SAB browser login |
   | `JELLYFIN_PASSWORD` | Jellyfin administrator login for `JELLYFIN_USER` |
   | `SAB_SRV_PASS` | NNTP provider password for `SAB_SRV_USER` |
   | `USENET_DEFAULT_APIKEY` | NZBgeek API key; separate from the NNTP login |
   | `MQTT_PASS` | Optional VPN-alert broker password |

3. Run `./setup.sh` as the media owner. Allow sudo for package/directory creation when needed. Docker must be accessible to that user; a new Docker installation may require granting access and logging in again before rerunning setup.

Setup builds/starts the pinned services, backs up application settings, configures integrations and Jellyfin's initial user/libraries, then verifies the result. It updates existing named integrations on reruns. External outages or expired subscriptions can fail verification; setup exits nonzero so failures remain visible.

`.env` is public; `.env.secrets` and generated hardware settings are ignored by Git. Precedence is **exported environment → `.env.secrets` → `.env`**. Values are parsed as data, so shell characters in passwords are preserved. Older Git commits may still contain credentials; removing values from the current `.env` does not remove history.

## Hardware and transcoding

On the first interactive run, setup lists detected GPUs and offers software transcoding. Without a terminal, automatic selection prefers Intel, then AMD, then NVIDIA; no detected GPU selects software. A requested GPU that is absent or fails its probe stops setup rather than enabling unverified acceleration.

| Hardware | Prepare on the host | Setup selects/tests |
|---|---|---|
| Intel | Enable the GPU in BIOS; install working Linux driver/firmware and expose a DRM render device | QSV encoding, VA-API decoder profiles, optional HEVC/AV1, low-power encoding and tone-mapping initialization |
| AMD | Working Linux driver/firmware and DRM render device; container image supplies Mesa drivers | VA-API decoder profiles and encoding; Vulkan initialization for tone mapping |
| NVIDIA | Working proprietary driver, `nvidia-smi`, and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) configured for Docker | NVENC encoding and sample NVDEC decoding tests |
| CPU / ARM | No GPU required | Software transcoding; board-specific ARM acceleration needs a suitable image and separate configuration |

The probe uses disposable containers and generated frames, with the selected device/group mapped automatically. Optional features are enabled according to probe results. Tone-mapping initialization is not a full HDR playback test; verify playback on the actual display. Intel has been tested on this deployment; AMD/NVIDIA selection logic has regression coverage but has not been physically validated here. Software mode can be too slow for demanding 4K/HDR conversion; prefer compatible direct-play media when possible.

```bash
./setup.sh --transcoding software
./setup.sh --transcoding intel --gpu-device /dev/dri/renderD129
./setup.sh --transcoding amd
./setup.sh --transcoding nvidia --gpu-device GPU-YOUR-UUID
./setup.sh --non-interactive --transcoding auto
./setup.sh --reprobe-hardware
```

`.hardware/compose.json` holds local device mappings; `.hardware/profile.json` holds the detected encoding settings. Reruns reuse these files. **Reprobe after changing GPU, drivers or device permissions.** When moving to another machine, leave `.hardware` behind and run full setup there. Setup does not install GPU drivers, change BIOS/kernel settings, choose storage paths, or measure available streaming bandwidth.

For platform-specific driver requirements and troubleshooting, see Jellyfin's [Intel](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/intel/), [AMD](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/amd/), and [NVIDIA](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/nvidia/) guides. This Compose deployment targets Linux; native macOS/Windows acceleration requires a different deployment approach.

## Defaults and customization

Edit `.env` for deployment values and `config/stack-defaults.json` for application defaults. Hardware-dependent Jellyfin encoding settings come from the local probe instead of assuming the original host's capabilities.

- **English:** Sonarr rejects detected non-English releases using a negative custom-format score; Radarr profiles require English. Jellyfin prefers English audio/subtitles, with smart subtitles and remembered audio choices disabled. Multilingual releases containing English are accepted. Unknown or incorrect tags can require manual review; preferences cannot create a missing English track.
- **Downloads:** Usenet is preferred with zero delay and a 60-minute torrent fallback delay, subject to configured bypass rules. SAB priority is 1; qBittorrent priority is 2. qBittorrent categories are `tv-sonarr`/`radarr`; SAB categories are `tv`/`movies`.
- **Indexers:** Prowlarr configures 1337x, EZTV, The Pirate Bay and NZBgeek, the tagged FlareSolverr proxy, and full sync to Sonarr, Radarr, and Lidarr (matching audio categories).
- **Music:** Lidarr uses `${MEDIA_DIR}/music` as `/music`, with the native Any quality and Standard metadata profiles. It uses SABnzbd category `audio` and qBittorrent category `lidarr`.
- **Jellyfin:** Movies, Shows, and Music use `/media/movies`, `/media/shows`, and `/media/music`. Transcodes use `/cache/transcodes`, throttling has a 180-second buffer, and no server-wide remote bitrate cap is imposed. Choose client playback quality to suit the connection.

Setup regenerates/discovers installation-specific API keys and IDs. It does not restore media, watch history or managed-library records: back up `CONF_HOME` separately for those. Current split download/library bind mounts can require copies during torrent imports; a shared-parent path migration is needed for hardlinks. Historical findings and playback evidence are in [STACK-AUDIT.md](STACK-AUDIT.md).

Service images and the Jellyfin base use pinned digests. OpenCL packages installed during image builds still come from the distribution repository. Updating images is a separate maintenance task; back up application state before upgrading.

## Manage and verify

Use **`./compose.sh`** so Compose loads vault/local secrets, portable paths and the selected hardware override. Bare `docker compose` does not apply this configuration loader.

```bash
./compose.sh ps
./compose.sh logs --tail 100 jellyfin
./setup.sh --validate-public              # public settings only
./setup.sh --validate                     # also require supplied secrets
./setup.sh --configure-only               # reapply settings using saved hardware profile
./setup.sh --check                        # readback and integration tests
./setup.sh --check --skip-external-tests
```

Validation/check modes use the Python environment installed by setup. `--configure-only` cannot change device mappings; use full setup with `--reprobe-hardware` for that.

After replacing Gluetun outside Compose, reconnect its dependent with `./compose.sh up -d --force-recreate qbittorrent`. Normal Compose-managed updates also restart the dependent; health checks and deunhealth provide recovery after a detection delay. Gluetun's firewall supplies the VPN kill switch.

MQTT VPN alerts are disabled by default. To enable them, configure `VPN_WATCHDOG_ENABLED=true`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, vault `MQTT_PASS`, and optional `MQTT_TLS=true`, then rerun setup. TLS verifies the broker certificate.

## Web interfaces

Replace `HOST` with the server's hostname/IP. The default `stuff` hostname requires LAN DNS or a hosts-file entry.

| Service | URL |
|---|---|
| Dashboard | `http://HOST/` |
| Radarr | `http://HOST:7878/` |
| Lidarr | `http://HOST:8686/` |
| Sonarr | `http://HOST:8989/` |
| Jellyfin | `http://HOST:8096/` |
| Prowlarr | `http://HOST:9696/` |
| qBittorrent | `http://HOST:8080/` |
| SABnzbd | `http://HOST:8081/` |

The dashboard starts automatically and builds links from the hostname used to open it. Customize files under `homepage/` and refresh the browser.

## Acceptance tests

After setup, install test dependencies and choose the desired coverage:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                         # offline regression tests
.venv/bin/python -m pytest -v --run-stack             # live checks, no test downloads
.venv/bin/python -m pytest -v --run-stack --run-transfers  # includes disposable transfers
```

Live tests require Docker access, the same secrets as setup, and internet access. They cover configuration, integrations, VPN, GPU, dashboard and playback; transfer tests submit dedicated Usenet/Creative Commons torrent jobs and clean up their own files. Run serially, without parallel workers. They exercise sync/health commands but do not reapply setup or import library media.

Playback tests need suitable library media; unavailable HDR sources, disabled integrations and software-mode GPU checks can skip. Add `-rs` to see skip reasons—a skipped feature has not been verified. For a machine-readable report, append `--junitxml=/tmp/media-stack-results.xml`. Shell syntax can be checked with `bash -n setup.sh compose.sh`.

## English subtitles

Bazarr runs at `http://HOST:6767/` and uses the stack's `UI_USER` / `UI_PASS` login.
Setup connects it to Sonarr/Radarr, imports their libraries, and assigns an **English full**
profile to existing and new movies/series. External subtitles are required even when an
embedded English track exists, and are saved beside the media for Jellyfin to discover.
Forced-only subtitles do not satisfy this profile. Hearing-impaired subtitles are accepted.

Gestdown, YIFY Subtitles and Subf2m are enabled. For broader coverage, optionally supply
`SUBDL_API_KEY`, or `OPENSUBTITLES_USERNAME` and `OPENSUBTITLES_PASSWORD`, in
`.env.secrets` or the environment and rerun `./setup.sh --configure-only`.
Provider availability and release matching determine coverage; missing subtitles remain
wanted and are searched every six hours. Bazarr also upgrades recent subtitle downloads.

Downloaded subtitles are automatically aligned against audio with frame-rate correction.
This does not rewrite embedded tracks or automatically repair every pre-existing sidecar;
use Bazarr's manual sync/search for those. Different movie edits can still need another match.
Jellyfin discovers sidecar files on its library scan; refresh the item if needed.

Setup preserves Bazarr state under `CONF_HOME/bazarr`, creates a native backup before
changing its settings, and reapplies the managed profile on reruns. `./setup.sh --check`
verifies settings and imported library IDs without starting subtitle searches. To backfill
an existing library immediately, run the missing-series/movie subtitle tasks in Bazarr.
