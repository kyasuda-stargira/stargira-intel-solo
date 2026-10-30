#!/bin/sh
# C1 sample run: the whole engine cycle on invented input, fully offline.
# A local web server on 127.0.0.1 serves feed.xml; nothing leaves the machine.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$HERE/../engine/intel.py"
STORE="$HERE/store"
PORT="$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")"
rm -rf "$STORE"
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$HERE" >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 1
I() { python3 "$ENGINE" --home "$STORE" "$@"; }
I init
I profile save "$HERE/profile.json"
I source add "http://127.0.0.1:$PORT/feed.xml" --label "Sample feed"
I fetch --days 7
I queue
I note "$HERE/notes.json"
I search "due diligence"
REPORT="$(I report --days 7)"
test -s "$REPORT"
echo "report: $REPORT"
