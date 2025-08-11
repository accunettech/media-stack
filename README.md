Run setup.sh.

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
