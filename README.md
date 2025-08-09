Requirements: Python 3 and Docker should be installed first!

Run setup.sh.

Once complete:
  qBittorrent: http://{docker-host}:8080/

  Sonarr: http://{docker-host}:8989/
        Root folder: /shows
        Download client category: sonarr
        Completed Download Handling: enabled

  Radarr: http://{docker-host}:7878/
        Root folder: /movies
        Download client category: radarr
        Completed Download Handling: enabled

  Jellyfin: http://{docker-host}:8096/
        Libraries: Movies → /media/movies, Shows → /media/shows
        Hardware Transcoding: enable VAAPI and select /dev/dri/renderD128 (if using device that support VAAPI)

  Prowlarr: http://{docker-host}:9696/
