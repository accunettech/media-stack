echo "Setting up python virtual environment..."
python3 -m venv venv

echo "Adding python libraries..."
pip install -r requirements.txt

echo "Creating config directories..."
mkdir -p ./conf/{gluetun,qbittorrent,jellyfin,sonarr,radarr,prowlarr}

docker compose up -d

python scripts/config_stack.py

cat << EOF
Notes:
    - qBittorrent's container network will be forced through gluetun VPN tunnel. If VPN goes down, qBittorrent will lose network connectivity.
    - Make sure media directory specified in .env exists and containers have permission to the file system
    - Make sure there is a downloads subdirectory in media directory for qBittorrent and SABnzbd.
    - See URLs for each app in stack below and double check the items under each.
    - Username and password for all apps in the stack should be set to UI_USER and UI_PASS in .env

App access and config tips
    qBittorrent: http://$HOSTNAME:8080/

    Sonarr: http://$HOSTNAME:8989/
        Root folder: /shows
        Download client category: sonarr
        Completed Download Handling: enabled
        Set priority of Usenet indexers higher (lower number) to favor Usenet

    Radarr: http://$HOSTNAME:7878/
        Root folder: /movies
        Download client category: radarr
        Completed Download Handling: enabled
        Set priority of Usenet indexers higher (lower number) to favor Usenet

    Jellyfin: http://$HOSTNAME:8096/
        Libraries: Movies → /media/movies, Shows → /media/shows
        Hardware Transcoding: enable VAAPI and select /dev/dri/renderD128 (if using device that support VAAPI)

    Prowlarr: http://$HOSTNAME:9696/
        If using usenet, set priority low and set torrent indexers high

    sabnzbd: http://$HOSTNAME:8081/
        Setup Downloader with API key (from sabnzbd setup) in Sanarr and Radarr if Usenet will be used
EOF
