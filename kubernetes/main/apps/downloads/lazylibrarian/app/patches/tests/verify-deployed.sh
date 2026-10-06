#!/usr/bin/env bash
# After a deploy: the running LazyLibrarian pod serves exactly patches/*.py from this checkout, every patched
# module imports, and the app log since the container started holds no traceback. Read-only.
set -euo pipefail
ns=downloads
here=$(cd "$(dirname "$0")/.." && pwd)
pod=$(kubectl get pods -n "$ns" -l app.kubernetes.io/name=lazylibrarian -o jsonpath='{.items[0].metadata.name}')
echo "pod $pod"
fail=0
modules=()
for f in "$here"/*.py; do
  name=$(basename "$f" .py)
  modules+=("lazylibrarian.$name")
  want=$(sha256sum "$f" | cut -d' ' -f1)
  got=$(kubectl exec -n "$ns" "$pod" -c app -- sha256sum "/app/lazylibrarian/lazylibrarian/$name.py" | cut -d' ' -f1)
  if [ "$want" = "$got" ]; then echo "sha ok   $name.py ${want:0:16}"; else echo "sha FAIL $name.py git ${want:0:16} pod ${got:0:16}"; fail=1; fi
done
kubectl exec -n "$ns" "$pod" -c app -- env PYTHONDONTWRITEBYTECODE=1 sh -c 'cd /app/lazylibrarian && python3 -c "
import importlib, sys
for m in sys.argv[1:]:
    importlib.import_module(m)
print(\"imports ok  \" + \" \".join(sys.argv[1:]))
" "$@"' sh "${modules[@]}" || fail=1
tracebacks=$(kubectl logs -n "$ns" "$pod" -c app | grep -c -E 'Traceback|Unhandled exception' || true)
echo "tracebacks since start: $tracebacks"
[ "$tracebacks" = 0 ] || fail=1
exit $fail
