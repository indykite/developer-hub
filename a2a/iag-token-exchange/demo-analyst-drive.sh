#!/usr/bin/env bash
# shellcheck disable=SC2250,SC2292,SC2248,SC2312  # style/info only; embedded python+json make brace-everything churny
# Demo: analyst + Google Drive through the IndyKite Agent Gateway.
# Usage: log in to the chatbot (http://localhost:3000) as the demo user in a
# fresh incognito window, then immediately run:
#   ./demo-analyst-drive.sh [username]     (default username: millicent)
# The whole run takes ~20 seconds, well inside even a 5-minute token TTL.
set -euo pipefail
cd "$(dirname "$0")"

USER_SUB="${1:-millicent}"
DRIVE_QUERY="${DRIVE_QUERY:-canbank}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

bold "1. Extracting a live token for '$USER_SUB' from the chatbot session"
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
if [ -z "$TOKEN" ]; then
    echo "✗ No live token for '$USER_SUB'. Log in at http://localhost:3000 (incognito) and rerun."
    exit 1
fi
echo "✓ token acquired (${#TOKEN} chars)"

bold "2. ANALYST: message through analyst-iag (:8885, workflow wf3)"
SEND=$(curl -s -m 60 -X POST http://localhost:8885/ \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"messageId":"demo-analyst-'$RANDOM'","role":"ROLE_USER","parts":[{"text":"Hello analyst, introduce yourself in one sentence."}]}}}')
TASK_ID=$(echo "$SEND" | python3 -c "import sys,json; print((json.load(sys.stdin).get('result') or {}).get('id',''))" 2>/dev/null || true)
if [ -z "$TASK_ID" ]; then
    echo "✗ analyst call failed: $(echo "$SEND" | head -c 300)"
else
    echo "✓ task accepted: $TASK_ID — waiting for the LLM..."
    for i in $(seq 1 15); do
        sleep 2
        OUT=$(curl -s -X POST http://localhost:8885/ \
            -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
            -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tasks/get\",\"params\":{\"id\":\"$TASK_ID\"}}" |
            python3 -c "
import sys, json
r = json.load(sys.stdin).get('result') or {}
state = (r.get('status') or {}).get('state', '')
texts = [p['text'] for a in r.get('artifacts') or [] for p in a.get('parts') or [] if p.get('text')]
print(state + '|' + (texts[0] if texts else ''))" 2>/dev/null || echo "|")
        STATE="${OUT%%|*}"
        TEXT="${OUT#*|}"
        if [ "$STATE" = "TASK_STATE_COMPLETED" ]; then
            echo "✓ ANALYST SAYS: $TEXT"
            break
        fi
        [ "$i" = "15" ] && echo "… task still $STATE (LLM slow?) — rerun tasks/get later"
    done
fi

bold "3. DRIVE: full-text search through drive-mcp-iag (:8887, workflow wf-drive)"
DRIVE="http://localhost:8887/mcp"
H=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")
SID=$(curl -s -D - -o /dev/null "${H[@]}" -X POST "$DRIVE" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"demo-script","version":"1.0"}}}' |
    grep -i mcp-session-id | tr -d '\r' | awk '{print $2}')
if [ -z "$SID" ]; then
    echo "✗ MCP initialize failed — check: docker compose logs drive-mcp-iag drive-mcp"
    exit 1
fi
echo "✓ MCP session: $SID"
curl -s -o /dev/null "${H[@]}" -H "Mcp-Session-Id: $SID" -X POST "$DRIVE" \
    -d '{"jsonrpc":"2.0","method":"initialized","params":{}}'
echo "✓ searching Google Drive for '$DRIVE_QUERY'..."
curl -s -m 45 "${H[@]}" -H "Mcp-Session-Id: $SID" -X POST "$DRIVE" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"search\",\"arguments\":{\"query\":\"$DRIVE_QUERY\"}}}" |
    python3 -c "
import sys, json, re
raw = sys.stdin.read()
m = re.search(r'data:\s*(\{.*\})', raw, re.S)
d = json.loads(m.group(1)) if m else json.loads(raw)
if 'error' in d:
    print('✗ ERROR:', json.dumps(d['error']))
else:
    for c in d.get('result', {}).get('content', []):
        print(c.get('text', ''))"

bold " done. Gateway decisions: docker compose logs drive-mcp-iag analyst-iag"
