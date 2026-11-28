#!/bin/bash
# Copy three (the exact version pinned in package.json) from the npm registry into
# patienttrace/static/vendor/three-<version>/ after checking npm's published sha512 integrity.
# No Node needed. OrbitControls' bare `from 'three'` import is pointed at the local file,
# so the page needs no import map and no CDN.
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(python3 -c "import json; print(json.load(open('package.json'))['dependencies']['three'])")
META=$(curl -fsS "https://registry.npmjs.org/three/$VER")
URL=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['dist']['tarball'])" "$META")
SRI=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['dist']['integrity'])" "$META")
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
curl -fsS "$URL" -o "$TMP/three.tgz"
GOT="sha512-$(openssl dgst -sha512 -binary "$TMP/three.tgz" | base64 -w0)"
[ "$GOT" = "$SRI" ] || { echo "integrity mismatch: $GOT != $SRI" >&2; exit 1; }
tar -xzf "$TMP/three.tgz" -C "$TMP" package/build/three.module.min.js package/examples/jsm/controls/OrbitControls.js package/LICENSE
OUT=patienttrace/static/vendor/three-$VER
rm -rf "$OUT"; mkdir -p "$OUT"
cp "$TMP/package/build/three.module.min.js" "$TMP/package/LICENSE" "$OUT/"
sed "s#from 'three';#from './three.module.min.js';#" "$TMP/package/examples/jsm/controls/OrbitControls.js" > "$OUT/OrbitControls.js"
grep -q "from './three.module.min.js'" "$OUT/OrbitControls.js" || { echo "OrbitControls import not rewritten" >&2; exit 1; }
echo "three $VER ($SRI)" > "$OUT/VERSION"
echo "vendored three $VER into $OUT (integrity OK)"
