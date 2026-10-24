#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Не найден Python 3. Установите его с https://www.python.org/downloads/ и запустите снова."
  read -r -p "Нажмите Enter..." _; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Первый запуск: готовлю программу, это займёт пару минут..."
  python3 -m venv .venv || { echo "Не получилось создать окружение."; read -r -p "Нажмите Enter..." _; exit 1; }
fi
if [ ! -f .venv/installed.flag ]; then
  .venv/bin/python -m pip install --quiet --require-hashes -r requirements.lock \
    && .venv/bin/python -m pip install --quiet --no-deps . \
    && echo ok > .venv/installed.flag \
    || { echo "Не получилось установить. Проверьте интернет и запустите снова."; read -r -p "Нажмите Enter..." _; exit 1; }
fi
.venv/bin/python -m leadscout --local
