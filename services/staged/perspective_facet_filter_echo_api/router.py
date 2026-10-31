from pathlib import Path

base = Path("services/_exemplar/router.py")
print(base.read_text())
print("--- TARGET SIBLINGS ---")
for p in Path("services/staged").glob("*perspective*/*"):
    print(p)
print("--- EXEMPLAR LOGIC ---")
print(Path("services/_exemplar/logic.py").read_text())