#!/usr/bin/env bash
# Автонастройка платформы рассылки на сервере (Ubuntu 22.04 / 24.04).
#
# Запуск на сервере:
#   git clone https://github.com/justyelli/parser-2gis-with-agent.git /opt/parser-2gis
#   cd /opt/parser-2gis
#   sudo bash server-setup.sh            # домен по умолчанию justmysite.site
#   sudo bash server-setup.sh mydomain.tld   # свой домен
#
set -euo pipefail

DOMAIN="${1:-justmysite.site}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITES_DIR="/var/www/sites"
ENV_FILE="$APP_DIR/.env"   # единый .env в каталоге проекта (git-ignored)
RUN_USER="${SUDO_USER:-root}"
DOMAIN_RE="${DOMAIN//./\\.}"   # экранируем точки для nginx-regex

if [ "$(id -u)" -ne 0 ]; then echo "Запусти через sudo: sudo bash server-setup.sh"; exit 1; fi
echo "==> Домен: $DOMAIN | Каталог: $APP_DIR | Пользователь: $RUN_USER"

echo "==> [1/8] Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx git curl wget unzip ca-certificates gnupg

echo "==> [2/8] Node.js 20"
NODE_MAJOR="$(node -v 2>/dev/null | sed 's/v//' | cut -d. -f1 || true)"
if [ -z "${NODE_MAJOR:-}" ] || [ "${NODE_MAJOR:-0}" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

echo "==> [3/8] Google Chrome (для парсинга)"
if ! command -v google-chrome >/dev/null 2>&1; then
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
  apt-get install -y /tmp/chrome.deb || { dpkg -i /tmp/chrome.deb || true; apt-get -f install -y; }
fi

echo "==> [4/8] Swap 2 ГБ (Chrome на малом RAM)"
if ! swapon --show 2>/dev/null | grep -q '/swapfile'; then
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile; mkswap /swapfile; swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> [5/8] Python venv + зависимости"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"
"$APP_DIR/.venv/bin/pip" install openai

echo "==> [6/8] WhatsApp-шлюз (npm install)"
( cd "$APP_DIR/whatsapp-gateway" && npm install )

echo "==> [7/8] Каталог сайтов + Nginx"
mkdir -p "$SITES_DIR"
chown -R "$RUN_USER":www-data "$SITES_DIR" 2>/dev/null || chown -R "$RUN_USER" "$SITES_DIR"
chmod 755 "$SITES_DIR"

cat > /etc/nginx/sites-available/parser-2gis <<NGINX
# Панель управления + сам домен (default_server — сюда попадает и заход по IP)
server {
    listen 80 default_server;
    server_name panel.${DOMAIN} ${DOMAIN};
    location / {
        proxy_pass http://127.0.0.1:8666;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_connect_timeout 30s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
# Любой поддомен -> папка сайта:  cafe-almaty.${DOMAIN} -> ${SITES_DIR}/cafe-almaty/
server {
    listen 80;
    server_name ~^(?<sub>.+)\.${DOMAIN_RE}\$;
    root ${SITES_DIR}/\$sub;
    index index.html;
    location / { try_files \$uri \$uri/ =404; }
}
NGINX
ln -sf /etc/nginx/sites-available/parser-2gis /etc/nginx/sites-enabled/parser-2gis
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# Открыть порт 80, если включён ufw
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp || true
fi

echo "==> [8/8] Env-файл + systemd-сервисы"
# Случайный пароль панели по умолчанию (панель закрыта сразу после установки).
PANEL_PASS="$(openssl rand -hex 8 2>/dev/null)"; [ -z "$PANEL_PASS" ] && PANEL_PASS="parser$(date +%s)"
ENV_CREATED=0
if [ ! -f "$ENV_FILE" ]; then
ENV_CREATED=1
cat > "$ENV_FILE" <<ENV
GLM_API_KEY=ВСТАВЬ_КЛЮЧ_СЮДА
# Необязательно: свой OpenAI-совместимый эндпоинт GLM
# (Zhipu CN: https://open.bigmodel.cn/api/paas/v4/)
GLM_BASE_URL=https://api.z.ai/api/paas/v4/
OUTREACH_BASE_DOMAIN=${DOMAIN}
OUTREACH_SITES_DIR=${SITES_DIR}
WA_GATEWAY_URL=http://127.0.0.1:8667
OUTREACH_MODEL=glm-5
# http — из коробки (Nginx на 80). enable-ssl.sh выпустит wildcard-SSL
# и переключит это в true.
OUTREACH_USE_HTTPS=false
# Вход в панель. Пусто = без пароля. Смени PANEL_PASSWORD на свой.
PANEL_USER=admin
PANEL_PASSWORD=${PANEL_PASS}
ENV
chmod 600 "$ENV_FILE"
chown "$RUN_USER" "$ENV_FILE" 2>/dev/null || true
fi

cat > /etc/systemd/system/wa-gateway.service <<UNIT
[Unit]
Description=WhatsApp gateway (Baileys)
After=network.target
[Service]
WorkingDirectory=${APP_DIR}/whatsapp-gateway
Environment=PORT=8667
ExecStart=/usr/bin/node index.js
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/outreach-dashboard.service <<UNIT
[Unit]
Description=Outreach dashboard (Flask)
After=network.target
[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/python -c "from parser_2gis.web.server import create_app; create_app().run(host='127.0.0.1', port=8666)"
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now wa-gateway outreach-dashboard

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "====================================================================="
echo " ГОТОВО. Осталось 3 шага:"
echo
echo " 1) DNS у регистратора домена ${DOMAIN} (A-записи -> ${IP:-5.183.253.70}):"
echo "      A   *.${DOMAIN}     -> ${IP:-5.183.253.70}"
echo "      A   ${DOMAIN}       -> ${IP:-5.183.253.70}"
echo "      A   panel.${DOMAIN} -> ${IP:-5.183.253.70}"
echo
echo " 2) Ключ GLM / Z.ai:"
echo "      nano ${ENV_FILE}          # впиши GLM_API_KEY"
echo "      systemctl restart outreach-dashboard"
echo
echo " 3) Открой панель:  http://panel.${DOMAIN}   (после того как DNS обновится)"
echo "      Шаг 5 покажет QR — отсканируй телефоном."
if [ "$ENV_CREATED" = "1" ]; then
echo
echo " Вход в панель:  логин admin  /  пароль:  ${PANEL_PASS}"
echo "   (сменить: PANEL_PASSWORD в ${ENV_FILE} → systemctl restart outreach-dashboard)"
fi
echo
echo " Логи:   journalctl -u outreach-dashboard -f"
echo "         journalctl -u wa-gateway -f"
echo " HTTPS (wildcard, опционально):  sudo bash enable-ssl.sh ${DOMAIN}"
echo "====================================================================="
