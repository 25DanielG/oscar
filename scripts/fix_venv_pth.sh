set -euo pipefail

SITE_PKG="$(dirname "$0")/../.venv/lib/python3.14/site-packages"
SITE_PKG="$(cd "$SITE_PKG" && pwd)"

echo "Clearing UF_HIDDEN from .pth files in $SITE_PKG"
find "$SITE_PKG" -maxdepth 1 -name "*.pth" -exec chflags 0 {} \;

ENTRY="$(dirname "$0")/../.venv/bin/oscar"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! grep -q "sys.path.insert" "$ENTRY" 2>/dev/null; then
    echo "Patching $ENTRY to inject project root into sys.path"
    python3 - "$ENTRY" "$ROOT" <<'PYEOF'
import sys, re
entry, root = sys.argv[1], sys.argv[2]
with open(entry) as f:
    src = f.read()
patch = f"import sys, os as _os\n_root = {root!r}\nif _root not in sys.path:\n    sys.path.insert(0, _root)\n"
src = re.sub(r'(#!.*\n)', r'\1' + patch, src, count=1)
with open(entry, 'w') as f:
    f.write(src)
PYEOF
fi

echo "Done. Run: oscar --help"
