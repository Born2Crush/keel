from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def main():
    src = ROOT / 'framegen' / 'dlss.py'
    text = src.read_text(encoding='utf-8')
    # The full patch is restored from the local file in this same commit's companion if this is short.
    raise SystemExit('incomplete')

if __name__ == '__main__':
    main()
