#!/usr/bin/env bash
# shellcheck disable=SC2250,SC2292,SC2248,SC2312  # style/info only; embedded python+json make brace-everything churny
# Standalone test of the Google Drive MCP PoC through the IndyKite gateway.
# Usage:
#   ./test-drive.sh                       # search 'canbank' as millicent
#   ./test-drive.sh <user>                # different logged-in user
#   DRIVE_QUERY=budget ./test-drive.sh    # different search query
# Prereq: the user is logged in to the chatbot (http://localhost:3000).
set -euo pipefail
cd "$(dirname "$0")"

USER_SUB="${1:-millicent}"
DRIVE_QUERY="${DRIVE_QUERY:-canbank}"
DRIVE="http://localhost:8887/mcp"

echo "- token for '$USER_SUB'"
TOKEN=$(docker compose exec -T chatbot python -c "
import glob, pickle, io, base64, json, time
class _R(pickle.Unpickler):
    # Restrict deserialization to builtin types only — the Flask session that
    # holds the token is a plain dict of strings, so no class loading is needed.
    # Blocking find_class prevents arbitrary code execution from a tampered file.
    def find_class(self, m, n):
        raise pickle.UnpicklingError('blocked global: ' + m + '.' + n)
cands = []
for f in glob.glob('/tmp/flask_session/*'):
    raw = open(f, 'rb').read()
    for off in (4, 8, 0):
        try:
            d = _R(io.BytesIO(raw[off:])).load()
            if isinstance(d, dict) and d.get('access_token'):
                t = d['access_token']
                pad = lambda s: s + '=' * (-len(s) % 4)
                c = json.loads(base64.urlsafe_b64decode(pad(t.split('.')[1])))
                if c.get('sub') == '$USER_SUB' and c.get('exp', 0) > time.time():
                    cands.append((c['exp'], t))
                break
        except Exception: pass
print(max(cands)[1] if cands else '')")
[ -z "$TOKEN" ] && {
    echo "✗ no live token — log in at http://localhost:3000 and rerun"
    exit 1
}
echo "✓ acquired (${#TOKEN} chars)"

H=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")

parse() { python3 -c "
import sys, json, re
raw = sys.stdin.read()
m = re.search(r'data:\s*(\{.*\})', raw, re.S)
d = json.loads(m.group(1)) if m else (json.loads(raw) if raw.strip() else {})
$1"; }

echo "- initialize"
SID=$(curl -s -D - -o /dev/null "${H[@]}" -X POST "$DRIVE" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test-drive","version":"1.0"}}}' |
    grep -i mcp-session-id | tr -d '\r' | awk '{print $2}')
[ -z "$SID" ] && {
    echo "✗ initialize failed — docker compose logs drive-mcp-iag drive-mcp"
    exit 1
}
echo "✓ session $SID"
curl -s -o /dev/null "${H[@]}" -H "Mcp-Session-Id: $SID" -X POST "$DRIVE" \
    -d '{"jsonrpc":"2.0","method":"initialized","params":{}}'

echo "- tools"
curl -s -m 25 "${H[@]}" -H "Mcp-Session-Id: $SID" -X POST "$DRIVE" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' |
    parse "print('✓', ', '.join(t['name'] for t in d['result']['tools']))"

echo "- first Drive files exposed as MCP resources"
curl -s -m 25 "${H[@]}" -H "Mcp-Session-Id: $SID" -X POST "$DRIVE" \
    -d '{"jsonrpc":"2.0","id":3,"method":"resources/list","params":{}}' |
    parse "
res = d.get('result', {}).get('resources', [])
for r in res[:5]: print(' ', r.get('uri'), '-', r.get('name'))
print('✓ %d resources listed' % len(res)) if res else print('✗', json.dumps(d)[:200])"

echo "- search: '$DRIVE_QUERY'"
curl -s -m 45 "${H[@]}" -H "Mcp-Session-Id: $SID" -X POST "$DRIVE" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"search\",\"arguments\":{\"query\":\"$DRIVE_QUERY\"}}}" |
    parse "
if 'error' in d: print('✗', json.dumps(d['error']))
else:
    for c in d['result'].get('content', []): print(c.get('text', ''))"
