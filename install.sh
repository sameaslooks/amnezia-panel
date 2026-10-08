#!/usr/bin/env bash
# Amnezia Panel — automated installer
# SSL modes: 1) Let's Encrypt (manual DNS)  2) Cloudflare API (auto)  3) Plain HTTP
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
step()  { echo -e "${BLUE}[→]${NC} $*"; }

# ── cd to the repo root (script's own directory) ─────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── root ──────────────────────────────────────────────────────────────────────
[[ "$EUID" -ne 0 ]] && error "Run as root:  sudo bash install.sh"

# ── verify required template files are present ───────────────────────────────
for _req in .env.example docker-compose.yml.example nginx.conf.example; do
    [[ -f "$_req" ]] || error "Missing: ${SCRIPT_DIR}/${_req}\nRun the installer from the amnezia-panel repo root."
done

# ── banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       Amnezia Panel — Installer          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── docker ────────────────────────────────────────────────────────────────────
_install_docker_engine() {
    step "Installing Docker Engine..."
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl gnupg lsb-release
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    DISTRO_CODENAME=$(. /etc/os-release && echo "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}")
    [[ -z "$DISTRO_CODENAME" ]] && DISTRO_CODENAME=$(lsb_release -cs 2>/dev/null || echo "jammy")
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${DISTRO_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    info "Docker Engine installed"
}

_install_compose_plugin() {
    step "Installing Docker Compose plugin..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin 2>/dev/null || true
    if ! docker compose version &>/dev/null; then
        mkdir -p /usr/local/lib/docker/cli-plugins
        curl -fsSL \
            "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
            -o /usr/local/lib/docker/cli-plugins/docker-compose
        chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    fi
    info "Docker Compose installed: $(docker compose version)"
}

COMPOSE="docker compose"
if ! command -v docker &>/dev/null; then
    _install_docker_engine
elif ! docker compose version &>/dev/null 2>&1; then
    if docker-compose version &>/dev/null 2>&1; then
        COMPOSE="docker-compose"
    else
        _install_compose_plugin
    fi
fi
info "Docker: $(docker --version)"

# ── domain ────────────────────────────────────────────────────────────────────
echo ""
read -rp "Domain or IP for the panel (e.g. panel.example.com or 1.2.3.4): " PANEL_DOMAIN
[[ -z "$PANEL_DOMAIN" ]] && error "Domain cannot be empty"

# ── ssl mode ─────────────────────────────────────────────────────────────────
echo ""
echo "SSL / certificate mode:"
echo "  1) Let's Encrypt  — manual DNS challenge; you add TXT record(s) yourself"
echo "  2) Cloudflare API — automatic wildcard cert via Cloudflare DNS API (recommended for domains on CF)"
echo "  3) Plain HTTP     — no certificate generated; nginx runs HTTP-only"
echo "                      (if you already have certs at the standard path, option 3 will still work"
echo "                       as long as you place them before starting)"
echo ""
read -rp "Choose [1/2/3]: " SSL_MODE
[[ "$SSL_MODE" =~ ^[123]$ ]] || error "Invalid choice — enter 1, 2 or 3"

# ── certbot helpers ───────────────────────────────────────────────────────────
_ensure_certbot() {
    if ! command -v certbot &>/dev/null; then
        step "Installing certbot..."
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot
        info "certbot installed"
    fi
}

_ensure_certbot_cloudflare() {
    _ensure_certbot
    if ! python3 -c "import certbot_dns_cloudflare" &>/dev/null 2>&1; then
        step "Installing certbot-dns-cloudflare..."
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-certbot-dns-cloudflare
        info "certbot-dns-cloudflare installed"
    fi
}

_create_renewal_hook() {
    local install_dir
    install_dir="$(pwd)"
    mkdir -p /etc/letsencrypt/renewal-hooks/post
    cat > /etc/letsencrypt/renewal-hooks/post/restart-amnezia-nginx.sh <<EOF
#!/bin/bash
cd "${install_dir}" 2>/dev/null || true
docker restart nginx-proxy 2>/dev/null || true
EOF
    chmod +x /etc/letsencrypt/renewal-hooks/post/restart-amnezia-nginx.sh
    info "Auto-renewal hook created (restarts nginx-proxy)"
}

CERT_DOMAIN="$PANEL_DOMAIN"  # domain used in letsencrypt cert path
USE_HTTPS=false

if [[ "$SSL_MODE" == "1" ]]; then
    # ── Let's Encrypt — manual DNS challenge ──────────────────────────────────
    # NOTE: --manual certs cannot auto-renew (require human DNS intervention each time),
    #       so no renewal hook is created here.
    _ensure_certbot
    echo ""
    warn "certbot will pause and ask you to add a DNS TXT record."
    warn "Add it in your DNS provider's control panel, wait for it to propagate, then press Enter."
    echo ""
    certbot certonly \
        --manual \
        --preferred-challenges dns \
        -d "$PANEL_DOMAIN" \
        --agree-tos \
        --register-unsafely-without-email \
        --manual-public-ip-logging-ok \
        || warn "certbot returned a non-zero exit code — verify cert status with: certbot certificates"
    USE_HTTPS=true

elif [[ "$SSL_MODE" == "2" ]]; then
    # ── Cloudflare API — automatic ─────────────────────────────────────────────
    _ensure_certbot_cloudflare
    echo ""
    echo "You need a Cloudflare API token with 'Edit zone DNS' permissions:"
    echo "  1. Visit: https://dash.cloudflare.com/profile/api-tokens"
    echo "  2. Create Token → 'Edit zone DNS' template"
    echo "  3. Zone Resources → Include → Specific zone → your domain"
    echo "  4. Finish and copy the token"
    echo ""
    read -rsp "Cloudflare API Token: " CF_TOKEN; echo ""
    [[ -z "$CF_TOKEN" ]] && error "API token cannot be empty"

    CF_INI="${SCRIPT_DIR}/cloudflare.ini"
    printf 'dns_cloudflare_api_token = %s\n' "$CF_TOKEN" > "$CF_INI"
    chmod 600 "$CF_INI"
    info "Cloudflare credentials written to cloudflare.ini"

    # Ask for base domain (wildcard cert covers *.base_domain)
    # Auto-suggest by stripping one leading label if it looks like a subdomain
    if [[ "$PANEL_DOMAIN" =~ ^[^.]+\.[^.]+\.[^.]+ ]]; then
        _SUGGESTED_BASE="${PANEL_DOMAIN#*.}"
    else
        _SUGGESTED_BASE="$PANEL_DOMAIN"
    fi
    echo ""
    echo "Cloudflare wildcard cert will be issued for <base_domain> and *.<base_domain>."
    echo "The cert covers your panel domain (${PANEL_DOMAIN}) if it ends in <base_domain>."
    read -rp "Base domain for wildcard cert [${_SUGGESTED_BASE}]: " _BASE_INPUT
    CERT_DOMAIN="${_BASE_INPUT:-${_SUGGESTED_BASE}}"
    [[ -z "$CERT_DOMAIN" ]] && error "Base domain cannot be empty"

    step "Issuing wildcard certificate for *.${CERT_DOMAIN} (takes ~2 min)..."
    certbot certonly \
        --dns-cloudflare \
        --dns-cloudflare-credentials "$CF_INI" \
        -d "$CERT_DOMAIN" \
        -d "*.${CERT_DOMAIN}" \
        --dns-cloudflare-propagation-seconds 60 \
        --non-interactive \
        --agree-tos \
        --register-unsafely-without-email \
        || error "certbot failed — check token permissions and DNS zone"
    _create_renewal_hook   # only Cloudflare can auto-renew unattended
    USE_HTTPS=true
    info "Certificate issued for ${CERT_DOMAIN} and *.${CERT_DOMAIN}"

elif [[ "$SSL_MODE" == "3" ]]; then
    # Check whether a usable certificate already exists on this machine
    _cert_check() {
        local d="$1"
        [[ -f "/etc/letsencrypt/live/${d}/fullchain.pem" ]] && echo "$d" && return 0
        return 1
    }
    if _found=$(_cert_check "$PANEL_DOMAIN"); then
        CERT_DOMAIN="$_found"
        USE_HTTPS=true
        info "Found existing certificate at /etc/letsencrypt/live/${CERT_DOMAIN}/ — will serve HTTPS on 443"
    elif [[ "$PANEL_DOMAIN" =~ ^[^.]+\.[^.]+\.[^.]+ ]] && _found=$(_cert_check "${PANEL_DOMAIN#*.}"); then
        CERT_DOMAIN="$_found"
        USE_HTTPS=true
        info "Found existing wildcard certificate at /etc/letsencrypt/live/${CERT_DOMAIN}/ — will serve HTTPS on 443"
    else
        warn "No certificate found — nginx will serve plain HTTP on port 80."
        warn "Place certs at /etc/letsencrypt/live/${PANEL_DOMAIN}/ and re-run to switch to HTTPS."
    fi
fi

# Ensure /etc/letsencrypt exists so the Docker volume mount doesn't fail
mkdir -p /etc/letsencrypt

# ── generate secrets ──────────────────────────────────────────────────────────
_gen_secret() {
    python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null \
        || LC_ALL=C tr -dc 'A-Za-z0-9!@#%^&*' </dev/urandom 2>/dev/null | head -c 48
}
_gen_pass() {
    python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null \
        || LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 32
}

# ── .env ──────────────────────────────────────────────────────────────────────
if [[ -f .env ]]; then
    warn ".env already exists — skipping generation"
    DB_PASS="$(grep '^POSTGRES_PASSWORD=' .env 2>/dev/null | cut -d= -f2- | xargs || true)"
else
    JWT_SECRET=$(_gen_secret)
    DB_PASS=$(_gen_pass)

    cp .env.example .env
    sed -i "s|your-super-secret-jwt-key-change-this|${JWT_SECRET}|g"  .env
    sed -i "s|YOUR-LONG-BIG-PASSWORD|${DB_PASS}|g"                    .env
    sed -i "s|^DOMAIN=.*|DOMAIN=${CERT_DOMAIN}|"                      .env
    sed -i "s|^SERVER_NAME=.*|SERVER_NAME=${PANEL_DOMAIN}|"           .env
    # Patch DATABASE_URL password
    sed -i "s|:postgres@|:${DB_PASS}@|g" .env

    info ".env created"
    echo ""
    echo "  ┌──────────────────────────────────────────────┐"
    echo "  │  POSTGRES_PASSWORD = ${DB_PASS}"
    echo "  └──────────────────────────────────────────────┘"
    echo ""
    warn "Save the password above — it will not be shown again."
fi

# ── docker-compose.yml ────────────────────────────────────────────────────────
if [[ -f docker-compose.yml ]]; then
    warn "docker-compose.yml already exists — skipping"
else
    cp docker-compose.yml.example docker-compose.yml
    # Substitute DB password placeholder
    _DBPASS="${DB_PASS:-}"
    [[ -z "$_DBPASS" ]] && _DBPASS="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2- | xargs)"
    sed -i "s|YOUR-LONG-BIG-BOT_PASSWORD|${_DBPASS}|g" docker-compose.yml
    sed -i "s|YOUR-LONG-BIG-PASSWORD|${_DBPASS}|g"     docker-compose.yml
    info "docker-compose.yml created"
fi

# ── nginx.conf ────────────────────────────────────────────────────────────────
if [[ -f nginx.conf ]]; then
    warn "nginx.conf already exists — skipping"
else
    if [[ "$USE_HTTPS" == "false" ]]; then
        # HTTP-only
        cat > nginx.conf <<NGINX_EOF
events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    server_tokens off;

    server {
        listen 80;
        server_name ${PANEL_DOMAIN};

        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Content-Security-Policy "default-src 'self'; connect-src 'self' ws://${PANEL_DOMAIN}; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; object-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;" always;

        location / {
            proxy_pass         http://amnezia-panel:8000;
            proxy_set_header   Host              \$host;
            proxy_set_header   X-Real-IP         \$remote_addr;
            proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header   X-Forwarded-Proto \$scheme;

            proxy_http_version 1.1;
            proxy_set_header   Upgrade    \$http_upgrade;
            proxy_set_header   Connection "upgrade";

            proxy_buffer_size       128k;
            proxy_buffers           4 256k;
            proxy_busy_buffers_size 256k;
        }
    }
}
NGINX_EOF
        info "nginx.conf written (HTTP-only)"
    else
        # HTTPS — modes 1/2, or mode 3 with existing cert detected
        cat > nginx.conf <<NGINX_EOF
events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    server_tokens off;

    server {
        listen 80;
        server_name ${PANEL_DOMAIN};
        return 301 https://\$host\$request_uri;
    }

    server {
        listen 443 ssl;
        http2 on;
        server_name ${PANEL_DOMAIN};
        client_max_body_size 0;

        ssl_certificate     /etc/letsencrypt/live/${CERT_DOMAIN}/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/${CERT_DOMAIN}/privkey.pem;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;

        add_header X-Content-Type-Options  "nosniff" always;
        add_header X-Frame-Options         "DENY" always;
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
        add_header Content-Security-Policy "default-src 'self'; connect-src 'self' wss://${PANEL_DOMAIN}; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; object-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;" always;

        location / {
            proxy_pass         http://amnezia-panel:8000;
            proxy_set_header   Host               \$host;
            proxy_set_header   X-Real-IP          \$remote_addr;
            proxy_set_header   X-Forwarded-For    \$proxy_add_x_forwarded_for;
            proxy_set_header   X-Forwarded-Proto  \$scheme;
            proxy_set_header   X-Forwarded-Host   \$host;
            proxy_set_header   X-Forwarded-Port   \$server_port;

            proxy_http_version 1.1;
            proxy_set_header   Upgrade    \$http_upgrade;
            proxy_set_header   Connection "upgrade";

            proxy_buffer_size       128k;
            proxy_buffers           4 256k;
            proxy_busy_buffers_size 256k;
        }
    }
}
NGINX_EOF
        info "nginx.conf written (HTTPS, cert path: /etc/letsencrypt/live/${CERT_DOMAIN}/)"
    fi
fi

# ── launch ────────────────────────────────────────────────────────────────────
echo ""
read -rp "Start Amnezia Panel now? [Y/n] " _START
_START="${_START:-Y}"
if [[ "$_START" =~ ^[Yy]$ ]]; then
    step "Pulling images and starting containers..."
    $COMPOSE up -d --pull always
    echo ""
    if [[ "$USE_HTTPS" == "true" ]]; then
        info "Panel: https://${PANEL_DOMAIN}"
    else
        info "Panel: http://${PANEL_DOMAIN}"
        # IP fallback only makes sense for HTTP (cert is domain-bound for HTTPS).
        # Only show when the domain is not already an IP address.
        if ! [[ "$PANEL_DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            _IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
            [[ -n "$_IP" ]] && info "       http://${_IP}  (direct IP — only if DNS not set yet)"
        fi
    fi
    info "Default login: admin / admin — change it after first login!"
    echo ""
    info "Logs: $COMPOSE logs -f"
else
    info "Run  '$COMPOSE up -d'  when ready."
fi
