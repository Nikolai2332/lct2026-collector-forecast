#!/bin/sh
# Рантайм-конфиг фронтенда: переключение моки/реальный API без пересборки образа
set -eu
case "$(echo "${USE_MOCKS:-false}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) value=true ;;
  *) value=false ;;
esac
echo "window.__APP_CONFIG__ = { useMocks: ${value} };" > /usr/share/nginx/html/config.js
echo "40-app-config: useMocks=${value}"
