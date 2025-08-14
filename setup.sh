#!/usr/bin/env bash
set -euo pipefail

have() { command -v "$1" >/dev/null 2>&1; }
is_wsl() { grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null || return 1; }

upsert_env() {
  local key="$1" val="$2"
  # portable awk update-or-append
  awk -v k="$key" -v v="$val" '
    BEGIN{found=0}
    $0 ~ "^"k"=" {print k"="v; found=1; next}
    {print}
    END{if(!found) print k"="v}
  ' .env > .env.tmp && mv .env.tmp .env
}

ensure_dir_rw() {
  local d="$1"
  sudo install -d -m 775 -o "${OWNER_UID}" -g "${OWNER_GID}" "$d"
}

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

[[ -f .env ]] || { echo "Error: .env not found" >&2; exit 1; }
set -a; source .env; set +a

: "${CONTAINER_HOME:?Missing CONTAINER_HOME}"
: "${MEDIA_DIR:?Missing MEDIA_DIR}"
: "${MOVIES_DIR:?Missing MOVIES_DIR}"
: "${SHOWS_DIR:?Missing SHOWS_DIR}"
: "${DOWNLOADS_DIR:?Missing DOWNLOADS_DIR}"

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
if [[ "$CONTAINER_HOME" != "$SCRIPT_DIR" ]]; then
  echo "CONTAINER_HOME should be in the same directory as this script: $SCRIPT_HOME"
  exit 1
fi

OWNER_UID="$(id -u)"
OWNER_GID="$(id -g)"
RENDER_GID="$(stat -c '%g' /dev/dri/renderD128 2>/dev/null || echo "")"
VIDEO_GID="$(getent group video 2>/dev/null | cut -d: -f3 || echo "")"
OS="$(uname -s)"
require_sudo

upsert_env OWNER_UID "$OWNER_UID"
upsert_env OWNER_GID "$OWNER_GID"

[[ -n "$RENDER_GID" ]] && upsert_env RENDER_GID "$RENDER_GID"
[[ -n "$VIDEO_GID"  ]] && upsert_env VIDEO_GID  "$VIDEO_GID"

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
echo
echo "Creating config directories..."
source .env
mkdir -p $CONF_HOME/{gluetun,qbittorrent,jellyfin/config,jellyfin/cache,sonarr,radarr,prowlarr}
sudo mkdir -p -- "$MEDIA_DIR" "$MOVIES_DIR" "$SHOWS_DIR" "$DOWNLOADS_DIR" && sudo chown -R "$OWNER_UID:$OWNER_GID" -- "$MEDIA_DIR" "$MOVIES_DIR" "$SHOWS_DIR" "$DOWNLOADS_DIR"
ensure_dir_rw "${CONF_HOME}/jellyfin/config"
ensure_dir_rw "${CONF_HOME}/jellyfin/cache"
echo "Building and starting containers..."
echo
docker compose up -d
echo
echo "Configuring stack..."
source .venv/bin/activate
python scripts/config_stack.py
echo
echo
echo "✅ Setup complete."
echo
echo "Be sure to checkout README.md for additional setup info!"
echo "URLs:"
echo "  - Radarr: http://$HOSTNAME:7878"
echo "  - Sonarr: http://$HOSTNAME:8989"
echo "  - Jellyfin: http://$HOSTNAME:8096"
echo "  - Prowlarr: http://$HOSTNAME:9696"
echo "  - qBittorrent: http://$HOSTNAME:8080"
echo "  - SABnzbd: http://$HOSTNAME:8081"
