# Media stack

A Linux Docker Compose stack with Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent through NordVPN/Gluetun, SABnzbd/Newshosting, NZBgeek, FlareSolverr, and a local dashboard.

## Clone and set up

This deployment reproduces the settings verified on October 23, 2026, on an Intel Core Ultra 5 226V with Arc graphics. It requires Linux, Python 3.10+, Docker Engine with Compose v2.17+, `/dev/net/tun`, and enough disk space for downloads and transcodes. GPU acceleration is optional. The host must provide any GPU driver and firmware; setup does not change the kernel or BIOS. Automatic prerequisite installation supports Debian/Ubuntu; install Python/venv and Docker yourself on other Linux distributions.

1. Clone the repository and review the public `.env`. Paths under `CONTAINER_HOME` follow the clone automatically. `MEDIA_DIR` defaults to `/ext-drive/media`; Jellyfin config/transcodes stay under the clone on the internal disk. Match `OWNER_UID`/`OWNER_GID` to the account owning the media. The selected render device and its group ID are detected on the host.
2. Supply these values from your vault as exported environment variables, or run `install -m 600 .env.secrets.example .env.secrets` and fill it locally:

   | Variable | Purpose |
   |---|---|
   | `OPENVPN_PASSWORD` | NordVPN **service** password, paired with public `OPENVPN_USER` |
   | `UI_PASS` | Sonarr, Radarr, Prowlarr, qBittorrent and SAB browser password |
   | `JELLYFIN_PASSWORD` | Jellyfin administrator password; account name is `JELLYFIN_USER` |
   | `SAB_SRV_PASS` | Newshosting NNTP password, paired with `SAB_SRV_USER` |
   | `USENET_DEFAULT_APIKEY` | NZBgeek API key; requires active membership |
   | `MQTT_PASS` | Optional VPN-alert broker password |

3. Run `./setup.sh` as the media owner. Use sudo when it requests package/directory permissions. Docker must be available to that user; after a new Docker installation, grant access and re-login if necessary, then rerun setup. First setup lists detected GPUs and lets you choose a device or software transcoding.

`.env` is public and intended for Git. `.env.secrets` is ignored and should have mode `600`. Environment variables override `.env.secrets`, which overrides `.env`. Dotenv values are parsed as data, not sourced as shell commands; vault passwords containing `$`, `&`, `^`, quotes, and similar characters are preserved. Passwords and API keys are never printed by bootstrap. Previous Git commits may still contain old credentials; sanitizing the current `.env` does not remove history.

Use **`./compose.sh`** for Compose commands so the same vault/secret loading and portable paths are applied. Bare `docker compose` does not load `.env.secrets` or resolve the setup-specific `${STACK_ROOT}` placeholder.

```bash
./setup.sh --help
./setup.sh --validate-public               # validate public settings; no secrets needed
./setup.sh --validate                      # validate public settings + supplied secrets
./setup.sh                                # prerequisites, directories, build/start, configure, verify
./setup.sh --configure-only               # reapply settings to existing running containers
./setup.sh --check                        # configuration readback and integration tests
./setup.sh --check --skip-external-tests  # omit final indexer/NNTP tests
./compose.sh ps
./compose.sh logs --tail 100 jellyfin
```

Validation/check modes require the Python environment created by setup. `--configure-only` still requires the normal host prerequisites. Check mode logs into services and performs their Test operations; it does not change configuration, submit downloads or searches, or install packages. External indexers/providers can fail tests temporarily or when a subscription expires; setup exits nonzero instead of reporting success.

## What bootstrap configures

`config/stack-defaults.json` is the sanitized declarative snapshot; `scripts/config_stack.py` applies it using supported application APIs. API keys, IDs, initial qBittorrent passwords and application-to-application credentials are discovered/generated per installation. No live database or saved token is committed.

- All six Sonarr profiles use `Language: Not English` at `-10000`, minimum score `0`. All six Radarr profiles require built-in language `English`. Upgrades remain disabled. Multilingual releases containing English pass; detected German-only releases fail. Unknown language metadata can also fail until English is identified. The rule follows [TRaSH's language guidance](https://trash-guides.info/Sonarr/Tips/How-to-setup-language-custom-formats/).
- Both Arr apps prefer Usenet with zero delay and a **60-minute** torrent fallback delay. Existing bypass settings are reproduced. SAB priority is 1 and qBittorrent priority is 2.
- qBittorrent paths are `/downloads` and `/downloads/incomplete`; its actual Arr categories are `tv-sonarr` and `radarr`. SAB categories are `tv` and `movies` (plus the existing default/audio/software categories). SAB browser authentication, English UI, TLS NNTP provider with 20 connections, paths and post-processing defaults are configured through its API.
- Prowlarr gets 1337x, EZTV, The Pirate Bay and NZBgeek, priorities 30/10, the tagged FlareSolverr proxy, and full sync to Sonarr/Radarr. The same category filters and search flags are restored. Manage later indexer changes in Prowlarr; editing the declarative snapshot changes what future setup runs enforce.
- Jellyfin's startup wizard is completed automatically on a fresh install, with the supplied administrator login and Movies/Shows libraries at `/media/movies` and `/media/shows`. English metadata and audio/subtitle preferences are applied, with smart subtitles, file-default audio disabled and remembered audio choices disabled.
- Jellyfin acceleration and codec settings follow the local hardware probe described below. On the verified Intel Arc host this includes QSV encoding, native VA-API decoding, low-power H.264/HEVC encoding and VPP/OpenCL initialization. The 180-second transcode throttle buffer, disabled segment deletion, internal `/cache/transcodes` path and unlimited server remote bitrate are reproduced across hosts.
- Gluetun/qBittorrent health/recovery configuration, dashboard hosting and the disabled optional MQTT watchdog are retained in Compose. All service image references and the Jellyfin base are pinned to recorded digests. The custom Jellyfin image installs OpenCL packages from its distribution repository; those OS package versions are not a frozen package mirror.

Reruns update the named integrations instead of duplicating them and resolve IDs for each installation. Application-native Arr/SAB backups and a private Jellyfin settings backup are created before changing settings. Existing media files, library matches, watch history, monitoring choices and unrelated custom resources are not deleted or bulk-imported. This reproduces configuration, not media or prior database contents. Back up `CONF_HOME` separately when migrating watch history or managed-library records.

## Hardware selection

Setup detects Intel/AMD DRM render devices and NVIDIA GPUs reported by `nvidia-smi`. On an interactive first run it offers a device list including CPU software transcoding. Unattended setup prefers Intel, then AMD, then NVIDIA; without a detected GPU it selects software. An explicitly requested GPU that is missing or fails its probe stops setup.

| Hardware | Host requirements and behavior |
|---|---|
| Intel | Working DRM render device and firmware; QSV encoding and VA-API decoding. Optional HEVC/AV1, low-power encoding and tone-mapping initialization are probed. |
| AMD | Working DRM render device and Mesa drivers in the container; VA-API encoding/decoding. Vulkan initialization gates the tone-mapping setting. |
| NVIDIA | Working proprietary driver, `nvidia-smi` and NVIDIA Container Toolkit configured for Docker; NVENC encoding and sample NVDEC tests. |
| CPU / ARM | Software mode needs no GPU mapping. Board-specific ARM acceleration requires a separate image and configuration. |

Setup builds the pinned Jellyfin image and runs short disposable probe containers before starting the stack. Intel/AMD decoder support comes from VA-API driver profiles; encoders are exercised with generated frames. Tone-mapping initialization checks are not full HDR playback tests. NVIDIA tone mapping follows verified HEVC 10-bit decoding and still needs playback verification. AMD/NVIDIA hosts have not been physically tested in this deployment.

```bash
./setup.sh --transcoding software
./setup.sh --transcoding intel --gpu-device /dev/dri/renderD129
./setup.sh --transcoding nvidia --gpu-device GPU-YOUR-UUID
./setup.sh --reprobe-hardware
./setup.sh --non-interactive --transcoding auto
```

The generated `.hardware/compose.json` device mappings and `.hardware/profile.json` encoding settings are local and ignored by Git. Subsequent runs reuse them; run `--reprobe-hardware` after changing GPU, drivers or device permissions. `--configure-only` requires an existing hardware profile. Keep `.hardware` out of migration copies to another machine and run full setup there. `./compose.sh` automatically loads the local device override; use it for routine container management.

Keep transcodes on a fast disk with free space. Review `MEDIA_DIR`, `CONF_HOME`, owner IDs, hostname and provider settings on each host; hardware detection does not choose storage paths or measure network bandwidth.

## Playback and storage

Native Jellyfin TV apps generally avoid more conversions than browsers. Client quality, subtitles, audio support and available bandwidth still affect direct play/transcoding. The previous live checks verified actual HDR10 and 4K Dolby Vision/HDR10-to-SDR HLS streaming, plus H.264/HEVC/AV1 hardware encoders. Setup tests configuration/integrations without downloading test media or interrupting viewer sessions.

A language preference cannot create English audio in a file or fix incorrect/missing tags. The audit identified 91 items with unlabelled audio and one invalid Resident Alien S01E02 MP4 needing replacement; see [STACK-AUDIT.md](STACK-AUDIT.md).

Current split `/downloads`, `/shows` and `/movies` binds are preserved. They prevent cross-mount hardlinks and may require copies when importing torrents. Plan a shared-parent path migration with the future drive expansion. Setup does not recursively change media ownership, delete media, convert the collection, or run automatic replacement downloads.

## VPN recovery and optional alerts

qBittorrent's health check verifies the VPN interface/route and Web UI; deunhealth restarts unhealthy containers. Compose-managed Gluetun updates also restart qBittorrent through `depends_on.restart`. After replacing Gluetun outside Compose, run `./compose.sh up -d --force-recreate qbittorrent`. Gluetun's firewall supplies the VPN kill switch; health-check recovery has a detection delay.

The MQTT watcher is excluded from normal Compose startup. Setup reconciles it to a clean disabled exit when `VPN_WATCHDOG_ENABLED=false`. For alerts, set `VPN_WATCHDOG_ENABLED=true`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, vault `MQTT_PASS`, and optionally `MQTT_TLS=true`, then rerun setup or use `./compose.sh --profile vpn-alerts up -d vpn-watch`. TLS verifies the broker certificate. No real broker integration was configured during the audit.

## Browser URLs

| Service | URL |
|---|---|
| Dashboard | `http://stuff/` |
| Radarr | `http://stuff:7878/` |
| Sonarr | `http://stuff:8989/` |
| Jellyfin | `http://stuff:8096/` |
| Prowlarr | `http://stuff:9696/` |
| qBittorrent | `http://stuff:8080/` |
| SABnzbd | `http://stuff:8081/` |

Replace `stuff` with the server's hostname/IP; LAN DNS or hosts files must resolve it. The homepage starts automatically with `./setup.sh`; setup and `./setup.sh --check` verify the served page, stylesheet, link script, and all six service icons. The static dashboard derives links from the hostname used to open it. Edit `homepage/index.html`, `homepage/style.css` or `homepage/links.js` and refresh the browser to customize it; the directory is mounted read-only into nginx.

## Post-setup tests

Complete `./setup.sh` first, then run these commands from the repository directory. The live suite requires Docker access for the current user, the same vault environment variables or `.env.secrets` used during setup, and internet access for indexer/provider and VPN checks.

Install the test dependencies into the virtual environment created by setup:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

Run the full suite, including disposable Usenet and Creative Commons torrent downloads:

```bash
.venv/bin/python -m pytest -v --run-stack --run-transfers
```

The suite checks running images and health, declared configuration, Arr download clients, indexers, Prowlarr application sync, FlareSolverr, NNTP authentication, VPN routing and firewall protection, storage write access, GPU capabilities, dashboard assets, Jellyfin plugins, direct streaming, and HLS/HDR transcoding. It also runs the offline bootstrap and hardware regression tests. It tests the existing installation without reapplying setup settings or importing library media; Prowlarr sync and health-refresh commands are exercised.

Run serially; do not use parallel pytest workers. Download tests use dedicated test jobs/directories and remove their own jobs and files. If the test torrent already exists, its transfer test skips to preserve the existing download. Playback tests skip when suitable media is unavailable; scan a Jellyfin library containing playable media, HDR10, and 4K Dolby Vision/HDR10 sources for full playback coverage. GPU checks skip when software transcoding is selected. Disabled optional integrations are not exercised. External service outages or expired subscriptions can cause live tests to fail.

Other test commands:

```bash
# Live acceptance checks without submitting downloads:
.venv/bin/python -m pytest -v --run-stack

# Offline tests only; live tests report skips:
.venv/bin/python -m pytest -q

# Full suite with a JUnit XML report:
.venv/bin/python -m pytest -v --run-stack --run-transfers \
  --junitxml=/tmp/media-stack-pytest-results.xml
```

`--run-transfers` requires `--run-stack`. To display skip reasons, add `-rs` to a pytest command. A passing run with skips does not establish coverage of the skipped features. The verified Intel deployment passed all 74 tests, including real transfers and both HDR source types.

Additional development checks:

```bash
bash -n setup.sh compose.sh
./setup.sh --validate
./setup.sh --check
```

The earlier fresh-configuration integration audit used separate empty config directories, a separate Compose network and local ports, and the pinned application images. The post-setup pytest suite above targets the existing configured stack; it does not create a fresh installation.
