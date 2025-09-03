import zipfile
from io import StringIO
import sys
import re

# Přesně zkopírovaná funkce z app.py pro parsování
def parse_gcode_header_params(gcode_content_stream):
    params = {}
    try:
        lines = gcode_content_stream.readlines()
        for line in lines[:2000]: # Omezíme se na prvních 2000 řádků
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
                elif ":" in line:
                    parts = line.lstrip('; ').split(':', 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip()
                        params[key] = value
    except Exception as e:
        print(f"Chyba při parsování G-kódu: {e}")
    return params

# Hlavní část skriptu
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Chyba: Zadejte cestu k .3mf souboru jako argument.")
        print("Příklad: python3 test_parser.py /cesta/k/souboru.3mf")
        sys.exit(1)

    file_path = sys.argv[1]
    print(f"--- Analyzuji soubor: {file_path} ---")

    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            gcode_files = sorted(
                [name for name in zf.namelist() if name.startswith('Metadata/plate_') and name.lower().endswith('.gcode')],
                key=lambda x: int(re.search(r'plate_(\d+)', x).group(1)) if re.search(r'plate_(\d+)', x) else 0
            )

            if not gcode_files:
                print("V souboru nebyly nalezeny žádné G-kód soubory.")
                sys.exit(1)

            print(f"Nalezeno {len(gcode_files)} G-kód souborů.")

            for i, gcode_filename in enumerate(gcode_files, 1):
                print(f"\n--- Zpracovávám plát {i} ({gcode_filename}) ---")
                gcode_content = zf.read(gcode_filename).decode('utf-8', 'ignore')
                params = parse_gcode_header_params(StringIO(gcode_content))

                if not params:
                    print("V hlavičce nebyly nalezeny žádné parametry.")
                else:
                    print("Nalezené parametry v hlavičce G-kódu:")
                    for key, value in params.items():
                        print(f"  '{key}': '{value}'")

    except FileNotFoundError:
        print(f"Chyba: Soubor nebyl nalezen na cestě: {file_path}")
    except zipfile.BadZipFile:
        print(f"Chyba: Soubor '{file_path}' není platný ZIP/.3mf archiv.")
    except Exception as e:
        print(f"Nastala neočekávaná chyba: {e}")
