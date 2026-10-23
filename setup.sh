#!/usr/bin/env bash
set -euo pipefail
STACK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$STACK_ROOT"

# Validation/check modes never install packages or recreate containers.
case "${1:-}" in
  --validate|--validate-public)
    [[ -x .venv/bin/python ]] || { echo 'Run setup once to install its Python environment.' >&2; exit 1; }
    if [[ "$1" == --validate-public ]]; then
      exec .venv/bin/python scripts/stack_env.py --public-only
    fi
    exec .venv/bin/python scripts/stack_env.py
    ;;
  --check)
    [[ -x .venv/bin/python ]] || { echo 'Run setup once to install its Python environment.' >&2; exit 1; }
    exec .venv/bin/python scripts/config_stack.py "$@"
    ;;
  --help|-h)
    cat <<'EOF'
Usage: ./setup.sh [--transcoding auto|intel|amd|nvidia|software] [--gpu-device PATH_OR_UUID]
                  [--reprobe-hardware] [--non-interactive] [--skip-external-tests]
       ./setup.sh --configure-only [--skip-external-tests]
       ./setup.sh --validate | --validate-public
       ./setup.sh --check [--skip-external-tests]

Default: install prerequisites, create required directories, build/start the
pinned stack, back up application settings, reconcile config, and verify it.
--configure-only: apply/verify settings using the saved hardware profile.
First run: detect GPUs and prompt if interactive; otherwise auto-select.
--reprobe-hardware: select/test hardware again after a hardware/driver change.
Passwords/API keys: exported vault variables or ignored .env.secrets.
EOF
    exit 0
    ;;
esac

[[ "$(uname -s)" == Linux ]] || { echo 'This Docker deployment requires Linux.' >&2; exit 1; }
SUDO=()
if [[ "$EUID" -ne 0 ]]; then SUDO=(sudo); fi
if ! command -v python3 >/dev/null || ! python3 -m venv --help >/dev/null 2>&1; then
  command -v apt-get >/dev/null || { echo 'Install Python >=3.10 and its venv package, then rerun setup.' >&2; exit 1; }
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y python3 python3-venv
fi
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python >=3.10 required"'
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  if ! python3 -m venv .venv; then
    command -v apt-get >/dev/null || { echo 'Install Python venv/ensurepip, then rerun setup.' >&2; exit 1; }
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" apt-get install -y python3-venv
    python3 -m venv .venv
  fi
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
# This parses dotenv as data; secrets containing &, $, ^, quotes etc. are not executed.
.venv/bin/python scripts/stack_env.py

if ! command -v docker >/dev/null; then
  # Docker's official Debian/Ubuntu repository, with a scoped signing key.
  command -v apt-get >/dev/null || { echo 'Install Docker Engine + Compose v2, then rerun setup.' >&2; exit 1; }
  . /etc/os-release
  case "$ID" in ubuntu|debian) ;; *) echo 'Install Docker Engine + Compose v2 for this distro.' >&2; exit 1;; esac
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y ca-certificates curl gnupg
  "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
  docker_key_tmp="$(mktemp)"
  trap 'rm -f "$docker_key_tmp"' EXIT
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" | gpg --dearmor > "$docker_key_tmp"
  "${SUDO[@]}" install -m 0644 "$docker_key_tmp" /etc/apt/keyrings/docker.gpg
  docker_repo="deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$ID ${VERSION_CODENAME} stable"
  echo "$docker_repo" | "${SUDO[@]}" tee /etc/apt/sources.list.d/docker.list >/dev/null
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  "${SUDO[@]}" systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  echo 'Install Docker Compose v2 >=2.17 (depends_on.restart is required), then rerun setup.' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Docker is unavailable to this user. Start Docker and grant access (e.g. docker group + re-login), then rerun setup.' >&2
  exit 1
fi
exec .venv/bin/python scripts/setup_stack.py "$@"
