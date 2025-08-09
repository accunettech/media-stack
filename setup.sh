echo "Creating config directories in current location. This should be the location set for CONTAINER_HOME env var..."
mkdir -p ./conf/{gluetun,qbittorrent,jellyfin,sonarr,radarr,prowlarr}

docker compose up -d

python scripts/config_stack.py

cat << EOF
App config tips (once)
    qBittorrent:
        URL: http://{docker-host}:8080/
        Settings → Downloads
            Default Save Path: /downloads
            Category paths:
                radarr → /downloads
                sonarr → /downloads

    Sonarr:
        URL: http://{docker-host}:8989/
        Root folder: /shows
        Download client category: sonarr
        Completed Download Handling: enabled

    Radarr:
        URL: http://{docker-host}:7878/
        Root folder: /movies
        Download client category: radarr
        Completed Download Handling: enabled

    Jellyfin:
        URL: http://{docker-host}:8096 (or https://{docker-host}:8920 if SSL enabled)
        Libraries: Movies → /media/movies, Shows → /media/shows
        Hardware Transcoding: enable VAAPI and select /dev/dri/renderD128 (if using device that support VAAPI)

    Prowlarr:
        URL: http://{docker-host}:9696/
EOF
