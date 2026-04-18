# refresh OSCAR session cookies
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
    set -a; source .env; set +a
fi

echo "=== OSCAR Bot — Auth Refresh ==="
echo ""

# headless first
echo "Checking existing session (headless)..."
if oscar auth refresh 2>/dev/null; then
    echo "Session still valid."
else
    echo "Session expired or no profile found."
    echo "Starting headed browser for manual login..."
    echo ""
    oscar auth refresh --headed
fi

echo ""
echo "Cookie expiry:"
oscar auth status

echo ""

# vps
VPS_HOST="${VPS_HOST:-}"
if [[ -z "$VPS_HOST" ]]; then
    echo "VPS_HOST not set in .env — skipping upload."
    exit 0
fi

VPS_USER="${VPS_USER:-root}"
if [[ "$VPS_USER" == "root" ]]; then
    _vps_home="/root"
else
    _vps_home="/home/${VPS_USER}"
fi
VPS_COOKIES_PATH="${VPS_COOKIES_PATH:-${_vps_home}/oscar/session.json}"
TARGET="${VPS_USER}@${VPS_HOST}:${VPS_COOKIES_PATH}"

read -rp "Upload session.json to ${TARGET}? [y/N] " confirm
if [[ "${confirm,,}" == "y" ]]; then
    scp session.json "$TARGET"
    echo "✓ Uploaded to ${TARGET}"
else
    echo "Upload skipped."
fi
