# media-stack

### Installation
1. Modify `.env`
2. Modify `docker-compose.yaml` if host device supports hardware acceleration (GPU onboard) ** See performance notes below
3. Run `./setup.sh`

************

- qBittorrent's container network will be forced through gluetun VPN tunnel. If VPN goes down, qBittorrent will lose network connectivity.
- Username and password for all apps in the stack should be set to UI_USER and UI_PASS in .env, except for Jellyfin, which you must complete startup wizard on first access
- Add/update/remove indexers using Prowlarr's UI (see Prowlarr URL below). Updates will automatically sync to Radarr and Sonarr
- If Usenet indexer and server were configured in .env, priority will automatically be set to favor Usenet over torrent and SABnzbd downloader over qBittorrent.
- If Usenet indexer and server are configured after initial bootstrap, you should set usenet indexers to a higher priority (lower number) than torrents and setup Profiles in Radarr and Sonarr to implement a wait period before they are used so Usenet can be searched first.
- On initial access of Jellyfin UI (see Jellyfin URL below), you will be pushed through setup wizard. When at the step, add Movies lib pointing at /media/movies and a Shows lib pointing at /media/shows.
- User Radarr to search for and download movies and Sonarr to search for and download shows. They will be placed in the correct spot and appear in Jellyfin once they are downloaded and processed.
- SABnzbd downloader is installed and configured automatically. If Usenet server was not specified in .env and setup during bootstrap, you must add it manually when you subscribe to a service. This is done in SABnzd UI (see SABnzbd URL below) in Settings > Servers > Add Server


******************
### Performance Notes
******************
It is highly recommended to run the media server on a device with a GPU. Barebones suggestion that has been tested is an Intel N100 Mini PC running Ubuntu Linux. Without, there may be playback issues if a DV WebDL movie is downloaded (common these days) and the device being streamed to only supports SDR. CPU likely will not be able to handle the load. If running on a device like a raspberry pi, it's best to add TRaSH custom formats in Radarr to block DV (WEBDL) downloads from being processed and added to the library. That reduces how often tone-mapping is required in down-conversion during transcoding.

If hosting on an N100 Mini PC, the following changes should be made in Jellyfin (Dashboard > Playback > Transcoding):
- Hardware acceleration: Intel QuickSync (QSV) :: change acceleration mode as necessary, based on GPU in host
- Enable Hardware Decoding: H264, VC1, HEVC 10 bit, VP9 10 bit
- Enable Prefer OS native DXVA or VA-API hardware decoders
- Enable hardware encoding


********************
### Language Notes
********************
If non-English language downloads are an issue, set a custom format in Radarr and a minimum score less than the custom format:
1. Settings > Custom Formats -> Add
2. Name it something notable like Favor English
3. Click Add (under conditions):
  - Name: English
  - Language: English
  - All other options unchecked
  - SAVE
4. Settings > Profiles
5. Click on each Quality Profile:
  - Set Custom Format Favor English (or whatever you named the CF)
  - Minimum custom format score: 75 (or a number less than the CF score (set below)
  - Language: English
  - Custom Format score: 100 (or a number greater than minimum custom format score)
  - SAVE


************
### URLs
************
- Radarr: http://{docker-host}:7878
- Sonarr: http://{docker-host}:8989
- Jellyfin: http://{docker-host}:8096
- Prowlarr: http://{docker-host}:9696/
- qBittorrent: http://{docker-host}:8080/
- SABnzbd: http://{docker-host}:8081/
