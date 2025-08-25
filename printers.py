import json
from pathlib import Path

DB_FILE = Path(__file__).parent / "printers.json"

def load_printers():
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_printers(printers):
    with open(DB_FILE, "w") as f:
        json.dump(printers, f, indent=2, ensure_ascii=False)
