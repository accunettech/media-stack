1. Modify .env
2. Run setup.sh

Once complete:
  - qBittorrent's container network will be forced through gluetun VPN tunnel. If VPN goes down, qBittorrent will lose network connectivity.
  - Make sure media directory specified in .env exists and containers have permission to the file system
  - Make sure there is a downloads subdirectory in media directory for qBittorrent and SABnzbd.
  - See URLs for each app in stack below and double check the items under each.
  - Username and password for all apps in the stack should be set to UI_USER and UI_PASS in .env

  qBittorrent: http://{docker-host}:8080/

  Sonarr: http://{docker-host}:8989/
        Root folder: /shows
        Download client category: sonarr
        Completed Download Handling: enabled
        Set priority of Usenet indexers higher (lower number) to favor Usenet

  Radarr: http://{docker-host}:7878/
        Root folder: /movies
        Download client category: radarr
        Completed Download Handling: enabled
        Set priority of Usenet indexers higher (lower number) to favor Usenet

  Jellyfin: http://{docker-host}:8096/
        Libraries: Movies → /media/movies, Shows → /media/shows
        Hardware Transcoding: enable VAAPI and select /dev/dri/renderD128 (if using device that support VAAPI)

  Prowlarr: http://{docker-host}:9696/
        If using usenet, set priority low and set torrent indexers high

  SABnzbd: http://{docker-host}:8081/
        Setup Downloader with API key (from sabnzbd setup) in Sanarr and Radarr if Usenet will be used


PERFORMANCE NOTES:
It is highly recommended to run the media server on a device with a GPU. Barebones suggestion that has been tested is an Intel N100 Mini PC running Ubuntu Linux. Without, there may be playback issues if a DV WebDL movie is downloaded (common these days) and the device being streamed to only supports SDR. CPU likely will not be able to handle the load. If running on a device like a raspberry pi, it's best to add TRaSH custom formats in Radarr to block DV (WEBDL) downloads from being processed and added to the library. That reduces how often tone-mapping is required in down-conversion during transcoding.

If hosting on an N100 Mini PC, the following changes should be made in Jellyfin (Dashboard > Playback > Transcoding):
- Hardware acceleration: Intel QuickSync (QSV) :: change acceleration mode as necessary, based on GPU in host
- Enable Hardware Decoding: H264, VC1, HEVC 10 bit, VP9 10 bit
- Enable Prefer OS native DXVA or VA-API hardware decoders
- Enable hardware encoding
- Enable tone mapping
- Tone mapping algorighm: BT.2390
- Tone mapping range: TV (unless main client is not a TV; then Auto)
