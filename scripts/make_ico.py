"""Значок для ярлыка Windows: PNG внутри контейнера .ico (Windows читает такие файлы с Vista)."""

import struct
from pathlib import Path

icons = Path(__file__).resolve().parent.parent / "leadscout" / "web" / "static" / "icons"
png = (icons / "icon-192.png").read_bytes()
header = struct.pack("<HHH", 0, 1, 1)
entry = struct.pack("<BBBBHHII", 192, 192, 0, 0, 1, 32, len(png), 6 + 16)
(icons / "scout.ico").write_bytes(header + entry + png)
print("scout.ico готов")
