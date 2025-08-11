#!/usr/bin/env bash
set -euo pipefail

# ---------------------------
# Helpers
have() { command -v "$1" >/dev/null 2>&1; }
is_wsl() { grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null || return 1; }

require_sudo() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    if ! have sudo; then
      echo "This script needs root; please install sudo or re-run as root." >&2
      exit 1
    fi
    SUDO="sudo"
  else
    SUDO=""
  fi
}

# ---------------------------
# Python install (idempotent)
install_python_linux() {
  . /etc/os-release || true
  case "${ID:-}" in
    ubuntu|debian)
      $SUDO apt-get update -y
      $SUDO apt-get install -y python3 python3-pip python3-venv
      ;;
    fedora)
      $SUDO dnf install -y python3 python3-pip
      ;;
    rhel|centos|rocky|almalinux)
      $SUDO dnf install -y python3 python3-pip || $SUDO yum install -y python3 python3-pip
      ;;
    arch|manjaro)
      $SUDO pacman -Sy --noconfirm python python-pip
      ;;
    *)
      echo "Unrecognized Linux distro for Python; please install python3 manually." >&2
      ;;
  esac
}

install_python_macos() {
  if ! have brew; then
    echo "Homebrew not found. Install from https://brew.sh first, then re-run." >&2
    exit 1
  fi
  brew install python@3 || true
}

# ---------------------------
# Docker install (Engine + Compose v2)
install_docker_linux() {
  . /etc/os-release || true
  case "${ID:-}" in
    ubuntu|debian)
      $SUDO apt-get update -y
      $SUDO apt-get install -y ca-certificates curl gnupg lsb-release
      $SUDO install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/${ID}/gpg" | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
      echo \
"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${ID} $(lsb_release -cs) stable" | \
      $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
      $SUDO apt-get update -y
      $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      $SUDO systemctl enable --now docker
      ;;
    fedora)
      $SUDO dnf -y install dnf-plugins-core
      $SUDO dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
      $SUDO dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
      $SUDO systemctl enable --now docker
      ;;
    rhel|centos|rocky|almalinux)
      $SUDO dnf -y install dnf-plugins-core || true
      $SUDO dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo || true
      $SUDO dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin || \
      $SUDO yum -y install docker-ce docker-ce-cli containerd.io
      $SUDO systemctl enable --now docker
      ;;
    arch|manjaro)
      $SUDO pacman -Sy --noconfirm docker docker-compose
      $SUDO systemctl enable --now docker
      ;;
    *)
      echo "Unrecognized Linux distro for Docker; see https://docs.docker.com/engine/install/" >&2
      ;;
  esac

  # Add current user to docker group (won't take effect until re-login)
  if getent group docker >/dev/null 2>&1; then
    $SUDO usermod -aG docker "$USER" || true
    echo "Added $USER to 'docker' group. Log out/in (or run: newgrp docker) for it to take effect."
  fi
}

install_docker_macos() {
  if ! have brew; then
    echo "Homebrew not found. Install from https://brew.sh first, then re-run." >&2
    exit 1
  fi

  echo "Choose Docker runtime for macOS:"
  echo "  1) Docker Desktop (GUI, easiest)  2) Colima (lightweight, CLI-only)"
  choice="${SETUP_DOCKER_MAC_CHOICE:-}"
  if [[ -z "${choice}" ]]; then
    read -rp "Enter 1 or 2 [1]: " choice
    choice="${choice:-1}"
  fi

  if [[ "$choice" == "1" ]]; then
    brew install --cask docker
    echo "Launching Docker Desktop..."
    open -a Docker || true
    echo "Wait for Docker Desktop to finish starting (whale icon), then 'docker ps' should work."
  else
    brew install docker docker-compose colima
    # Reasonable defaults; adjust if needed
    colima start --cpu 2 --memory 4 --disk 20 || colima start
    echo "Colima started. 'docker ps' should work now."
  fi
}

# ---------------------------
# Optional: create venv + pip install
bootstrap_python_venv() {
  if have python3; then
    if [[ -f requirements.txt || -f ./bootstrap.py ]]; then
      echo "Creating Python venv (.venv) and installing requirements..."
      python3 -m venv .venv
      . .venv/bin/activate
      python -m pip install -U pip
      if [[ -f requirements.txt ]]; then
        pip install -r requirements.txt
      else
        # Minimal deps your bootstrap script uses
        pip install requests python-dotenv
      fi
      deactivate || true
    fi
  fi
}

# ---------------------------
# Main
OS="$(uname -s)"
require_sudo

# Warn about WSL
if [[ "$OS" == "Linux" ]] && is_wsl; then
  echo "WSL detected. Best practice: install Docker Desktop for Windows and enable WSL integration."
  echo "If you still want native Docker inside WSL, proceed at your own risk."
fi

# Python
if have python3; then
  echo "Python3 found: $(python3 --version)"
else
  echo "Python3 not found. Installing..."
  if [[ "$OS" == "Darwin" ]]; then
    install_python_macos
  elif [[ "$OS" == "Linux" ]]; then
    install_python_linux
  else
    echo "Unsupported OS for automatic Python install." >&2
  fi
fi

# Docker + Compose v2
if have docker; then
  echo "Docker found: $(docker --version)"
else
  echo "Docker not found. Installing..."
  if [[ "$OS" == "Darwin" ]]; then
    install_docker_macos
  elif [[ "$OS" == "Linux" ]]; then
    install_docker_linux
  else
    echo "Unsupported OS for automatic Docker install." >&2
  fi
fi

# Compose plugin check (docker compose v2)
if docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 available."
else
  echo "Docker Compose v2 not detected. Installing plugin..."
  if [[ "$OS" == "Linux" ]]; then
    # Try installing the plugin package if not present
    . /etc/os-release || true
    case "${ID:-}" in
      ubuntu|debian) $SUDO apt-get install -y docker-compose-plugin || true ;;
      fedora|rhel|centos|rocky|almalinux) $SUDO dnf install -y docker-compose-plugin || $SUDO yum install -y docker-compose-plugin || true ;;
      arch|manjaro)  $SUDO pacman -Sy --noconfirm docker-compose || true ;;
    esac
  elif [[ "$OS" == "Darwin" ]]; then
    if have brew; then brew install docker-compose || true; fi
  fi
fi

bootstrap_python_venv

echo "Creating config directories..."
mkdir -p ./conf/{gluetun,qbittorrent,jellyfin,sonarr,radarr,prowlarr}

docker compose up -d

python scripts/config_stack.py

echo
echo "✅ Setup complete."
echo
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
