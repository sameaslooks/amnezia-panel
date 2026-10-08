<h1 align="center">Amnezia VPN Panel</h1>

<p align="center">
  <strong>Self-hosted веб-панель управления AmneziaWG VPN серверами</strong>
</p>

<p align="center">
  <img width="2554" height="823" alt="dashboard screenshot" src="https://github.com/user-attachments/assets/2675858c-6b78-4682-bf46-13457a646800" />
</p>

<p align="center">
  <a href="README.en.md">English</a> &nbsp;·&nbsp;
  <a href="https://github.com/sameaslooks/amnezia-panel/blob/master/LICENSE">GPL-3.0</a> &nbsp;·&nbsp;
  <img src="https://img.shields.io/badge/AmneziaWG-v2%20%2F%20v3-green" alt="AmneziaWG v2/v3" />
</p>

---

> [!IMPORTANT]
> ### Нужно больше возможностей?
>
> **[NX-Panel](https://noxum.online/panel)** — коммерческая платформа на базе этого проекта.
>
> Включает: биллинг и подписки · Telegram-бот · RBAC и мультироль · платёжные шлюзы · white-label · API · техподдержку.
>
> [https://noxum.online/panel](https://noxum.online/panel) &nbsp;·&nbsp; Telegram: [@looksaboutthis](https://t.me/looksaboutthis)

> [!NOTE]
> **Данный проект заморожен** — разработка перешла в [NX-Panel](https://noxum.online/panel): биллинг и подписки, Telegram-бот для клиентов, RBAC и мультироль, платёжные шлюзы, white-label, API, техподдержка. Сюда попадают только мелкие фиксы.

---

## Обзор

**Amnezia VPN Panel** — open-source веб-панель для управления одним или несколькими [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module) VPN серверами по SSH. Автоматическая установка сервера, учёт трафика по пользователям, автоблокировка при превышении лимита/срока — всё через браузерный UI.

Поддерживает контейнеры **AmneziaWG v2** и **AmneziaWG v3** (по умолчанию — v3).

### Что изменилось в этой версии

- Поддержка AmneziaWG v3 — новые параметры обфускации (`HeaderProtectionKey`, тайминги `RekeyAfterTime`, `KeepaliveTimeout` и др.), серверный и клиентский конфиги генерируются правильно
- Исправлена генерация конфига сервера: `Address = 10.8.1.1/32` вместо некорректного `/24`
- Установщик (`install.sh`) переписан: три режима SSL (Let's Encrypt · Cloudflare API · plain HTTP), автодетект существующих сертификатов в режиме без генерации
- Исправлен хардкод имени контейнера в SSH-соединении — теперь берётся `amnezia-awg2` или `amnezia-awg3` по настройке конкретного сервера

---

## Возможности

### Управление серверами
- Неограниченное количество удалённых серверов по SSH (пароль или приватный ключ)
- Автоматическая установка AmneziaWG + AdGuardHome в один клик на любой Ubuntu/Debian сервер
- Стриминг лога установки в реальном времени через WebSocket
- Мониторинг статуса контейнеров (online / offline / ошибки)
- Запуск, остановка, перезапуск контейнеров из UI
- Поддержка AmneziaWG v2 и v3 — настраивается на каждый сервер отдельно (дефолт: v3)

### Управление пользователями
- Роли администратора и обычного пользователя
- Лимит трафика, срок подписки и максимальное количество устройств на пользователя
- Автоблокировка при превышении лимитов; автовосстановление при продлении
- Мгновенное включение / отключение аккаунтов

### VPN клиенты
- Генерация ключей, конфиги устройств хранятся в базе
- QR-коды AmneziaWG и deep-ссылки `amnezia://` для мгновенного импорта
- Статистика трафика и время последнего handshake в реальном времени
- Массовая синхронизация — перенести существующий AmneziaWG сервер в панель без потери пиров

### Мониторинг
- Дашборд: серверы, клиенты, трафик, активные соединения
- График трафика за 30 дней, топ пользователей, предупреждения об истечении срока
- Интеграция с Prometheus: CPU, RAM, графики speedtest

### Безопасность
- JWT аутентификация с настраиваемым секретом
- Хеширование паролей через bcrypt
- Полный набор параметров обфускации AmneziaWG (Jc, Jmin, Jmax, S1–S4, H1–H4, I1)
- Блокировка через маршруты (не удаление ключей)

---

## Быстрый старт

### Требования

| Компонент | Минимум |
|-----------|---------|
| ОС | Ubuntu 20.04 / 22.04 / 24.04 или Debian 11/12 |
| CPU | 1 vCPU |
| RAM | 1 ГБ (рекомендуется 2 ГБ) |
| Диск | 10 ГБ свободно |
| Docker | 20.10+ (устанавливается автоматически) |

### Установщик (рекомендуется)

```bash
git clone https://github.com/sameaslooks/amnezia-panel
cd amnezia-panel
sudo bash install.sh
```

Установщик предложит три режима SSL:

| Режим | Описание |
|-------|----------|
| **Let's Encrypt** | Ручной DNS-challenge — вы добавляете TXT-запись сами |
| **Cloudflare API** | Автоматический wildcard-сертификат через Cloudflare DNS API |
| **Plain HTTP** | Без генерации сертификата; nginx работает на порту 80 |

После завершения установщик выведет адрес панели в консоль. Логин по умолчанию: **admin / admin** — смените сразу.

### Ручная установка

```bash
git clone https://github.com/sameaslooks/amnezia-panel
cd amnezia-panel

cp .env.example .env
cp docker-compose.yml.example docker-compose.yml
cp nginx.conf.example nginx.conf

# Отредактируйте .env: JWT_SECRET, POSTGRES_PASSWORD, DOMAIN, SERVER_NAME
# Тот же POSTGRES_PASSWORD должен быть и в docker-compose.yml
nano .env

docker compose up -d
docker compose logs -f
```

> **Примечание:** Избегайте встроенного типа подключения «local». Для сервера на той же машине что и панель — добавьте его через SSH-подключение к `127.0.0.1`.

---

## Конфигурация

Все настройки — в файле `.env`:

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `JWT_SECRET` | ✅ | Случайная строка для подписи токенов — `openssl rand -base64 48` |
| `DATABASE_URL` | ✅ | `postgresql://amnezia:<пароль>@postgres:5432/apanel_db` |
| `DOMAIN` | ✅ (HTTPS) | Базовый домен для пути к сертификату Let's Encrypt |
| `SERVER_NAME` | ✅ (HTTPS) | Хостнейм в `server_name` nginx (обычно совпадает с `DOMAIN`) |
| `DEBUG` | — | `true` включает подробное логирование |

**Бэкапы:** база данных хранится в `./postgres/data/` — делайте резервные копии регулярно.

---

## Архитектура

```
Backend:   FastAPI · asyncpg (PostgreSQL) · asyncssh · bcrypt · python-jose · WebSockets
Frontend:  Alpine.js · TailwindCSS · Chart.js · QRCode.js
Infra:     Docker Compose · Nginx · Prometheus + exporters
```

```
Браузер (Alpine.js)
      │  REST / WebSocket
      ▼
FastAPI backend ──── PostgreSQL
      │
      │  asyncssh
      ▼
VPN сервер #1 … #N
  └─ docker exec amnezia-awg2 / amnezia-awg3
       └─ awg · iptables
```

---

## Обновление

```bash
git pull
docker compose pull
docker compose up -d
```

Изменения схемы БД применяются автоматически при старте (`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`).

---

## Обратная связь

- **Нашли баг?** Откройте [Issue](https://github.com/sameaslooks/amnezia-panel/issues)
- **Есть идея?** Напишите в [Discussions](https://github.com/sameaslooks/amnezia-panel/discussions)

---

## Нужно больше?

Если нужны биллинг, полноценный Telegram-бот, RBAC, платёжные шлюзы или white-label — смотрите **NX-Panel**.

[https://noxum.online/panel](https://noxum.online/panel) &nbsp;·&nbsp; Telegram: [@looksaboutthis](https://t.me/looksaboutthis)

---

## Лицензия

GPL-3.0. Свободно использовать, изменять и распространять — производные работы должны оставаться открытыми.

Подробнее: [LICENSE](LICENSE)

---

<p align="center"><sub>© 2026 sameaslooks</sub></p>
