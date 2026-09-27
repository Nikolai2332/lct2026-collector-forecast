"""Выгружает контракт API в backend/openapi.json без запуска сервера и без БД.

    python scripts/export_openapi.py            # записать файл
    python scripts/export_openapi.py --check    # проверить, что файл актуален (для CI)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402  (engine создаётся лениво — к БД не подключаемся)

TARGET = ROOT / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    content = render()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != content:
            print("openapi.json устарел: запустите python scripts/export_openapi.py")
            return 1
        print("openapi.json актуален")
        return 0
    TARGET.write_text(content, encoding="utf-8", newline="\n")
    print(f"Записано: {TARGET} ({len(app.openapi()['paths'])} путей)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
