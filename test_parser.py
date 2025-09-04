import zipfile
from io import StringIO
import sys
import re

# Stále stejná funkce pro parsování
def parse_gcode_header_params(gcode_content_stream):
    params = {}
    try:
        lines = gcode_content_stream.readlines()
        for line in lines[:2000]:
            if isinstance(line, bytes):
                line = line.decode('utf-8', 'ignore')
            line = line.strip()
            if line.startswith(';'):
                if "=" in line:
                    parts = line.lstrip('; ').split('=', 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip().strip('"')
                        params[key] = value
    except Exception as e:
        print(f"Chyba při parsování G-kódu: {e}")
    return params

# Hlavní část skriptu
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Chyba: Zadejte cestu k .3mf souboru jako argument.")
        sys.exit(1)

    file_path = sys.argv[1]
    print(f"--- Analyzuji soubor: {file_path} ---")

    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            # Najdeme G-kód prvního plátu
            gcode_files = sorted([name for name in zf.namelist() if name.startswith('Metadata/plate_') and name.lower().endswith('.gcode')])

            if not gcode_files:
                print("V souboru nebyl nalezen žádný G-kód.")
                sys.exit(1)

            gcode_filename = gcode_files[0]
            print(f"--- Zpracovávám první plát ({gcode_filename}) ---")

            gcode_content = zf.read(gcode_filename).decode('utf-8', 'ignore')
            params = parse_gcode_header_params(StringIO(gcode_content))

            if not params:
                print("V hlavičce nebyly nalezeny žádné parametry.")
            else:
                print("Nalezené parametry v hlavičce G-kódu:")
                # Seřadíme klíče pro lepší přehlednost
                for key in sorted(params.keys()):
                    print(f"  '{key}': '{params[key]}'")

    except Exception as e:
        print(f"Nastala neočekávaná chyba: {e}")
