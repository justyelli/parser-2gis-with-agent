#!/usr/bin/env bash
# Выпуск wildcard-SSL (*.домен) и включение HTTPS для всех поддоменов.
#
# Запуск на сервере ПОСЛЕ server-setup.sh:
#   sudo bash enable-ssl.sh                 # домен возьмётся из .env
#   sudo bash enable-ssl.sh mydomain.tld    # или явно
#
# Let's Encrypt для wildcard требует DNS-проверки: certbot покажет TXT-запись,
# которую нужно добавить у регистратора домена, и подождёт её появления.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Запусти через sudo: sudo bash enable-ssl.sh"; exit 1; fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$APP_DIR/.env"

# --- домен: из аргумента или из .env ---
DOMAIN="${1:-}"
if [ -z "$DOMAIN" ] && [ -f "$ENV_FILE" ]; then
  DOMAIN="$(grep -E '^OUTREACH_BASE_DOMAIN=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
fi
if [ -z "$DOMAIN" ]; then
  echo "Не удалось определить домен. Укажи явно: sudo bash enable-ssl.sh mydomain.tld"; exit 1
fi
DOMAIN_RE="${DOMAIN//./\\.}"

SITES_DIR="/var/www/sites"
if [ -f "$ENV_FILE" ]; then
  D="$(grep -E '^OUTREACH_SITES_DIR=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
  [ -n "$D" ] && SITES_DIR="$D"
fi
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
EMAIL="admin@${DOMAIN}"

echo "==> Домен: $DOMAIN | Каталог сайтов: $SITES_DIR"

echo "==> [1/4] certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y certbot

echo "==> [2/4] Выпуск wildcard-сертификата (DNS-проверка)"
if [ -f "${CERT_DIR}/fullchain.pem" ]; then
  echo "    Сертификат уже есть — пропускаю выпуск."
else
  certbot certonly --manual --preferred-challenges dns \
    --agree-tos --no-eff-email -m "$EMAIL" \
    -d "*.${DOMAIN}" -d "${DOMAIN}"
fi

echo "==> [3/4] Пересборка Nginx-конфига с HTTPS"
cat > /etc/nginx/sites-available/parser-2gis <<NGINX
# HTTP -> HTTPS (редирект для всех имён домена)
server {
    listen 80 default_server;
    server_name ${DOMAIN} panel.${DOMAIN} ~^.+\.${DOMAIN_RE}\$;
    return 301 https://\$host\$request_uri;
}
# Панель + сам домен (заход по имени/IP)
server {
    listen 443 ssl default_server;
    server_name panel.${DOMAIN} ${DOMAIN};
    ssl_certificate ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    location / { proxy_pass http://127.0.0.1:8666; proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; }
}
# Любой поддомен -> папка сайта: cafe-almaty.${DOMAIN} -> ${SITES_DIR}/cafe-almaty/
server {
    listen 443 ssl;
    server_name ~^(?<sub>.+)\.${DOMAIN_RE}\$;
    ssl_certificate ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    root ${SITES_DIR}/\$sub;
    index index.html;
    location / { try_files \$uri \$uri/ =404; }
}
NGINX
ln -sf /etc/nginx/sites-available/parser-2gis /etc/nginx/sites-enabled/parser-2gis
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# Открыть 443, если включён ufw
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 443/tcp || true
fi

echo "==> [4/4] Включаю HTTPS-ссылки в дашборде"
if [ -f "$ENV_FILE" ]; then
  if grep -qE '^OUTREACH_USE_HTTPS=' "$ENV_FILE"; then
    sed -i 's/^OUTREACH_USE_HTTPS=.*/OUTREACH_USE_HTTPS=true/' "$ENV_FILE"
  else
    echo 'OUTREACH_USE_HTTPS=true' >> "$ENV_FILE"
  fi
  systemctl restart outreach-dashboard 2>/dev/null || true
fi

echo
echo "====================================================================="
echo " ГОТОВО: HTTPS включён. Поддомены отдаются по https://<slug>.${DOMAIN}"
echo
echo " Автопродление: wildcard-сертификат выпущен через ручную DNS-проверку,"
echo " поэтому 'certbot renew' сам не продлит. Перед истечением (90 дней)"
echo " запусти этот скрипт снова или настрой DNS-плагин certbot."
echo "====================================================================="
