"""Скачивает шрифты с Google Fonts и кладёт их в приложение, чтобы оно не зависело от внешних сайтов.

Запуск: python scripts/vendor_fonts.py
"""
import re
import sys
import urllib.request
from pathlib import Path

CSS_URL = ("https://fonts.googleapis.com/css2?family=Onest:wght@200..600"
           "&family=Cormorant+Garamond:ital,wght@1,500&family=JetBrains+Mono:wght@400;500&display=swap")
KEEP = {"cyrillic", "latin"}
OUT = Path(__file__).resolve().parent.parent / "leadscout" / "web" / "static" / "fonts"


def fetch(url: str, ua: bool = False) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"} if ua else {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    css = fetch(CSS_URL, ua=True).decode()
    blocks = re.findall(r"/\* ([\w-]+) \*/\s*(@font-face \{.*?\})", css, re.S)
    out_css = ["/* Шрифты Onest, Cormorant Garamond, JetBrains Mono (SIL OFL). Скачаны scripts/vendor_fonts.py */"]
    for subset, block in blocks:
        if subset not in KEEP:
            continue
        family = re.search(r"font-family: '([^']+)'", block).group(1)
        style = re.search(r"font-style: (\w+)", block).group(1)
        weight = re.search(r"font-weight: ([\d ]+)", block).group(1).strip()
        url = re.search(r"url\((https://[^)]+)\)", block).group(1)
        rng = re.search(r"unicode-range: ([^;]+);", block).group(1)
        name = f"{family.replace(' ', '')}-{style}-{weight.replace(' ', '-')}-{subset}.woff2"
        (OUT / name).write_bytes(fetch(url))
        out_css.append(
            f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};font-display:swap;"
            f"src:url(/fonts/{name}) format('woff2');unicode-range:{rng}}}"
        )
        print("ok", name)
    (OUT / "fonts.css").write_text("\n".join(out_css) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
