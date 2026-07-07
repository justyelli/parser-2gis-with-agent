# Платформа рассылки — установка и настройка

Пошаговый запуск платформы: **парсинг 2ГИС → бизнесы без сайта → ИИ-сайт → поддомен → рассылка в WhatsApp**.

Платформа состоит из двух сервисов:

| Сервис | Что делает | Порт |
|---|---|---|
| **Дашборд** (Python/Flask) | мастер, парсинг, генерация сайтов, деплой, кампании | 8666 |
| **WhatsApp-шлюз** (Node/Baileys) | вход по QR, отправка сообщений | 8667 |

---

## 1. Что понадобится

- **VPS** (Ubuntu 22.04+), 1–2 ГБ RAM.
- **Домен** с возможностью поставить wildcard A-запись (`*.домен`).
- **Ключ Anthropic** (Claude API) — [console.anthropic.com](https://console.anthropic.com).
- **Отдельный номер WhatsApp** (лучше не личный — при массовых рассылках номер могут забанить).
- Установленные на VPS: Python 3.10–3.13, Node.js 18+, Google Chrome (для парсера), Nginx.

---

## 2. Установка

```bash
git clone <репозиторий> parser-2gis-new
cd parser-2gis-new

# Python-часть (дашборд + парсер + генератор)
pip install -e .
pip install anthropic          # для ИИ-генерации сайтов

# Node-часть (WhatsApp-шлюз)
cd whatsapp-gateway && npm install && cd ..
```

Установите Chrome (для парсинга на сервере):
```bash
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb
```

---

## 3. Переменные окружения

Задайте перед запуском дашборда (например в `/etc/environment`, systemd-юните или `.env`):

| Переменная | Назначение | Пример |
|---|---|---|
| `ANTHROPIC_API_KEY` | ключ Claude API (генерация сайтов) | `sk-ant-...` |
| `OUTREACH_BASE_DOMAIN` | ваш wildcard-домен | `mysites.kz` |
| `OUTREACH_SITES_DIR` | куда Nginx отдаёт поддомены | `/var/www/sites` |
| `WA_GATEWAY_URL` | адрес WhatsApp-шлюза | `http://127.0.0.1:8667` |
| `OUTREACH_MODEL` | модель Claude (по умолчанию opus) | `claude-opus-4-8` или `claude-sonnet-5` (дешевле) |

> Секреты (ключ Anthropic) держите **только** в env, не в конфиге.

---

## 4. Запуск

**WhatsApp-шлюз** (один раз отсканировать QR):
```bash
cd whatsapp-gateway
npm start
# открыть http://<VPS>:8667 → отсканировать QR в WhatsApp
# (Настройки → Связанные устройства → Привязка устройства)
```

**Дашборд:**
```bash
python -c "from parser_2gis.web.server import create_app; create_app().run(host='0.0.0.0', port=8666)"
# либо: parser-2gis-new   (откроет веб-интерфейс)
```

Для постоянной работы — оформите оба как systemd-сервисы (пример в конце).

---

## 5. Настройка поддоменов на VPS (одноразово)

Именно это делает деплой сайтов автоматическим: сгенерировали сайт → нажали «Опубликовать» → он появляется на `slug.вашдомен` **без перезагрузки Nginx**.

### 5.1. DNS
Добавьте **wildcard A-запись**: `*.mysites.kz  →  <IP вашего VPS>`
(и `panel.mysites.kz → <IP>` — для самого дашборда, по желанию).

### 5.2. Каталог сайтов
```bash
sudo mkdir -p /var/www/sites
sudo chown -R $USER:www-data /var/www/sites   # чтобы дашборд мог писать сюда
```

### 5.3. Nginx — один wildcard-конфиг на все поддомены
`/etc/nginx/sites-available/sites`:
```nginx
server {
    listen 80;
    # поддомен → папка: cafe-almaty.mysites.kz → /var/www/sites/cafe-almaty/
    server_name ~^(?<sub>.+)\.mysites\.kz$;
    root /var/www/sites/$sub;
    index index.html;
    location / { try_files $uri $uri/ =404; }
}

# (опционально) сам дашборд на panel.mysites.kz
server {
    listen 80;
    server_name panel.mysites.kz;   # точное имя имеет приоритет над regex
    location / { proxy_pass http://127.0.0.1:8666; proxy_set_header Host $host; }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/sites /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Теперь любая папка `/var/www/sites/<slug>/` уже доступна на `<slug>.mysites.kz`. Деплой = просто копирование папки — платформа делает это сама.

### 5.4. Wildcard SSL (HTTPS для всех поддоменов)
Один сертификат на `*.mysites.kz` через DNS-проверку:
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot certonly --manual --preferred-challenges dns \
     -d "*.mysites.kz" -d "mysites.kz"
# добавьте TXT-запись, которую попросит certbot, затем допишите ssl в конфиг Nginx
```
После выпуска сертификата добавьте в оба server-блока `listen 443 ssl;` и пути к сертификату `/etc/letsencrypt/live/mysites.kz/`.

---

## 6. Как пользоваться (мастер, шаги 1–6)

1. **Ниша и город** — вводите, напр. «Стоматология» + «Алматы».
2. **Найти бизнесы без сайта** — парсит 2ГИС, оставляет тех, у кого есть телефон, но нет сайта (лиды сохраняются в БД).
3. **Создать сайт** — Claude генерит шаблонный сайт под нишу (нужен `ANTHROPIC_API_KEY`).
4. **Опубликовать** — сайт заливается на поддомен, вы получаете ссылку.
5. **Подключить WhatsApp** — QR прямо в дашборде (номер подключается в любой момент).
6. **Разослать** — ссылка уходит всем лидам с задержками. Есть «тестовый прогон» (dry-run) без реальной отправки.

Старый интерфейс парсера — под кнопкой «Расширенный режим».

---

## 7. Антибан и ответственность

- Рассылка идёт с **случайными задержками 40–120 сек**, **дневным лимитом** и только в **рабочие часы** — настройки в `OutreachOptions`.
- Массовые рассылки нарушают правила WhatsApp — используйте **отдельный номер**, небольшие объёмы, прогретый аккаунт.
- Рассылка по холодным контактам регулируется законами о рекламе и перс. данных — ответственность на вас.

---

## Приложение: systemd-сервисы

`/etc/systemd/system/wa-gateway.service`:
```ini
[Unit]
Description=WhatsApp gateway
After=network.target
[Service]
WorkingDirectory=/opt/parser-2gis-new/whatsapp-gateway
ExecStart=/usr/bin/npm start
Environment=PORT=8667
Restart=always
[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/outreach-dashboard.service`:
```ini
[Unit]
Description=Outreach dashboard
After=network.target
[Service]
WorkingDirectory=/opt/parser-2gis-new
Environment=ANTHROPIC_API_KEY=sk-ant-...
Environment=OUTREACH_BASE_DOMAIN=mysites.kz
Environment=OUTREACH_SITES_DIR=/var/www/sites
Environment=WA_GATEWAY_URL=http://127.0.0.1:8667
ExecStart=/usr/bin/python3 -c "from parser_2gis.web.server import create_app; create_app().run(host='127.0.0.1', port=8666)"
Restart=always
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now wa-gateway outreach-dashboard
```
