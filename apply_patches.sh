#!/usr/bin/env bash
# apply_patches.sh -- apply all pending patches to zo_sentinel_builder.py
# Run once after zm go or after any builder restart

BUILDER=/home/workspace/zo_mesh/zo_sentinel_builder.py
BAKDIR=/home/workspace/logs

echo "Patching zo_sentinel_builder.py..."

# 1. Fix MiniMax to use native endpoint (not Anthropic-compat)
python3 - << 'PYEOF'
import re
path = "/home/workspace/zo_mesh/zo_sentinel_builder.py"
content = open(path).read()

# Replace minimax_generate function entirely
old = re.search(r'def minimax_generate.*?^def ', content, re.DOTALL | re.MULTILINE)
if old:
    new_fn = open("/home/workspace/zo_sentinel/minimax_patch.py").read()
    content = content[:old.start()] + new_fn + "\n\ndef " + content[old.end()-4:]
    open(path, "w").write(content)
    print("  [OK] MiniMax native endpoint patched")
else:
    print("  [!!] Could not locate minimax_generate -- manual patch needed")
PYEOF

# 2. Syntax check
python3 -c "import ast; ast.parse(open('$BUILDER').read()); print('  [OK] Syntax OK')"

# 3. Restart builder to pick up changes
pkill -f zo_sentinel_builder.py 2>/dev/null; sleep 2
nohup python3 $BUILDER >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &
echo "  [OK] Builder restarted PID $!"

# 4. Start sentinel_director if not running
if ! pgrep -f sentinel_director.py > /dev/null; then
    nohup python3 /home/workspace/zo_sentinel/sentinel_director.py \
        >> /home/workspace/logs/sentinel_director.log 2>&1 &
    echo "  [OK] Sentinel Director started PID $!"
else
    echo "  [OK] Sentinel Director already running"
fi

echo "Done."