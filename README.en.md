<h1 align="center">Amnezia VPN Panel</h1>

<p align="center">
  <strong>Self-hosted web panel for managing AmneziaWG VPN servers</strong>
</p>

<p align="center">
  <img width="2554" height="823" alt="dashboard screenshot" src="https://github.com/user-attachments/assets/2675858c-6b78-4682-bf46-13457a646800" />
</p>

<p align="center">
  <a href="README.md">Русский</a> &nbsp;·&nbsp;
  <a href="https://github.com/sameaslooks/amnezia-panel/blob/master/LICENSE">GPL-3.0</a> &nbsp;·&nbsp;
  <img src="https://img.shields.io/badge/AmneziaWG-v2%20%2F%20v3-green" alt="AmneziaWG v2/v3" />
</p>

---

> [!NOTE]
> **This project is frozen** — development has moved to [NX-Panel](https://noxum.online/panel): billing and subscriptions, Telegram bot for clients, RBAC and multi-role support, payment gateways, white-label, API, dedicated support. Only bug fixes land here.

---

## Overview

**Amnezia VPN Panel** is an open-source web panel for managing one or more [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module) VPN servers over SSH. One-click server provisioning, per-user traffic accounting, automatic blocking on limit/expiry — all from the browser.

Supports both **AmneziaWG v2** and **AmneziaWG v3** containers (v3 by default).

### What's new in this release

- AmneziaWG v3 support — new obfuscation parameters (`HeaderProtectionKey`, timing fields `RekeyAfterTime`, `KeepaliveTimeout`, etc.), server and client configs generated correctly
- Fixed server config: `Address = 10.8.1.1/32` instead of the incorrect `/24`
- `install.sh` rewritten: three SSL modes (Let's Encrypt · Cloudflare API · plain HTTP), auto-detects existing certs when skipping generation
- Fixed hardcoded container name in SSH connections — now picks `amnezia-awg2` or `amnezia-awg3` from per-server settings

---

## Features

### Server management
- Unlimited remote servers via SSH (password or private key)
- Automated AmneziaWG + AdGuardHome installation in one click on any Ubuntu/Debian server
- Real-time install log streaming over WebSocket
- Container status monitoring (online / offline / errors)
- Start, stop, restart containers from the UI
- AmneziaWG v2 and v3 support — configurable per server

### User management
- Admin and regular user roles
- Per-user traffic limit, subscription expiry and device count
- Auto-blocking on limit/expiry; auto-restore on renewal
- Instant account enable / disable

### VPN clients
- Key generation; device configs stored in the database
- AmneziaWG QR codes and `amnezia://` deep links for instant import
- Real-time traffic stats and last handshake time
- Bulk sync — import an existing AmneziaWG server into the panel without losing peers

### Monitoring
- Dashboard: servers, clients, traffic, active connections
- 30-day traffic chart, top users, expiry warnings
- Prometheus integration: CPU, RAM, speedtest graphs

### Security
- JWT authentication with configurable secret
- Password hashing via bcrypt
- Full AmneziaWG obfuscation parameter set (Jc, Jmin, Jmax, S1–S4, H1–H4, I1)
- Blocking via routing (no key deletion)

---

## Quick Start

### Requirements

| Component | Minimum |
|-----------|---------|
| OS | Ubuntu 20.04 / 22.04 / 24.04 or Debian 11/12 |
| CPU | 1 vCPU |
| RAM | 1 GB (2 GB recommended) |
| Disk | 10 GB free |
| Docker | 20.10+ (installed automatically) |

### Installer (recommended)

```bash
git clone https://github.com/sameaslooks/amnezia-panel
cd amnezia-panel
sudo bash install.sh
```

The installer prompts for one of three SSL modes:

| Mode | Description |
|------|-------------|
| **Let's Encrypt** | Manual DNS challenge — you add the TXT record yourself |
| **Cloudflare API** | Automatic wildcard cert via Cloudflare DNS API |
| **Plain HTTP** | No certificate; nginx serves on port 80 |

When done, the installer prints the panel address. Default login: **admin / admin** — change it immediately.

### Manual installation

```bash
git clone https://github.com/sameaslooks/amnezia-panel
cd amnezia-panel

cp .env.example .env
cp docker-compose.yml.example docker-compose.yml
cp nginx.conf.example nginx.conf

# Edit .env: JWT_SECRET, POSTGRES_PASSWORD, DOMAIN, SERVER_NAME
# The same POSTGRES_PASSWORD must appear in docker-compose.yml as well
nano .env

docker compose up -d
docker compose logs -f
```

> **Note:** Avoid the built-in "local" connection type. For a VPN server on the same machine as the panel, add it as an SSH connection to `127.0.0.1`.

---

## Configuration

All settings live in `.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET` | ✅ | Random string for signing tokens — `openssl rand -base64 48` |
| `DATABASE_URL` | ✅ | `postgresql://amnezia:<password>@postgres:5432/apanel_db` |
| `DOMAIN` | ✅ (HTTPS) | Base domain for the Let's Encrypt cert path |
| `SERVER_NAME` | ✅ (HTTPS) | Nginx `server_name` hostname (usually same as `DOMAIN`) |
| `DEBUG` | — | `true` enables verbose logging |

**Backups:** the database lives in `./postgres/data/` — back it up regularly.

---

## Architecture

```
Backend:   FastAPI · asyncpg (PostgreSQL) · asyncssh · bcrypt · python-jose · WebSockets
Frontend:  Alpine.js · TailwindCSS · Chart.js · QRCode.js
Infra:     Docker Compose · Nginx · Prometheus + exporters
```

```
Browser (Alpine.js)
      │  REST / WebSocket
      ▼
FastAPI backend ──── PostgreSQL
      │
      │  asyncssh
      ▼
VPN server #1 … #N
  └─ docker exec amnezia-awg2 / amnezia-awg3
       └─ awg · iptables
```

---

## Updating

```bash
git pull
docker compose pull
docker compose up -d
```

Schema changes are applied automatically on startup (`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`).

---

## Feedback

- **Found a bug?** Open an [Issue](https://github.com/sameaslooks/amnezia-panel/issues)
- **Have an idea?** Start a [Discussion](https://github.com/sameaslooks/amnezia-panel/discussions)

---

## Need more?

If you need billing, a full Telegram bot, RBAC, payment gateways, or white-label — see **NX-Panel**.

[https://noxum.online/panel](https://noxum.online/panel) &nbsp;·&nbsp; Telegram: [@looksaboutthis](https://t.me/looksaboutthis)

---

## License

GPL-3.0. Free to use, modify and distribute — derivative works must remain open.

Details: [LICENSE](LICENSE)

---

<p align="center"><sub>© 2026 sameaslooks</sub></p>
