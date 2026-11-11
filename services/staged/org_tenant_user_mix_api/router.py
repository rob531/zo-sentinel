import sys
from pathlib import Path
p=Path('/workspace')
print(p.exists())
print(list(p.glob('services/_exemplar/router.py'))[:1])
print(Path('/workspace/services/_exemplar/router.py').read_text() if Path('/workspace/services/_exemplar/router.py').exists() else 'no')