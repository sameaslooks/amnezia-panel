# Amnezia VPN Panel — server_setup.py
# Copyright (c) 2026 sameaslooks · https://lolz.team/threads/10302952/ · https://t.me/looksaboutthis
# Licensed under GPL-3.0 · https://github.com/sameaslooks/amnezia-panel
import asyncio
from typing import AsyncGenerator, Optional
from connection import SSHConnection
from logger import logger
import base64
import os
import random
import secrets


async def setup_server_stream(
    conn: SSHConnection,
    sudo_password: Optional[str] = None,
    awg_version: str = 'awg3'
) -> AsyncGenerator[dict, None]:
    """Устанавливает AmneziaWG на удалённом сервере."""
    if sudo_password:
        conn.sudo_password = sudo_password

    container_name = f"amnezia-{awg_version}"
    base_image = "amneziavpn/amneziawg-go:latest"

    try:
        yield {"type": "info", "message": f"🔄 Connecting to server (AmneziaWG {awg_version.upper()})..."}
        await conn._connect()
        yield {"type": "info", "message": "✅ Connected to server"}

        # Проверка Docker
        docker_check = await conn.run_command("test -f /usr/bin/docker && echo yes", in_container=False)
        if "yes" not in docker_check:
            yield {"type": "step", "name": "🔧 Installing Docker", "success": False, "output": "Docker not found, installing..."}
            update_out = await conn.run_command("sudo apt update", in_container=False)
            yield {"type": "info", "message": f"apt update output: {update_out}"}
            install_out = await conn.run_command("sudo apt install -y docker.io", in_container=False)
            yield {"type": "info", "message": f"apt install output: {install_out}"}
            docker_check2 = await conn.run_command("test -f /usr/bin/docker && echo yes", in_container=False)
            if "yes" in docker_check2:
                yield {"type": "step", "name": "✅ Docker installed", "success": True, "output": install_out}
                user = conn.username
                await conn.run_command(f"sudo usermod -aG docker {user}", in_container=False)
                yield {"type": "info", "message": f"User {user} added to docker group"}
            else:
                yield {"type": "error", "message": "❌ Docker installation failed", "output": install_out}
                return
        else:
            yield {"type": "step", "name": "✅ Docker already installed", "success": True}
            user = conn.username
            await conn.run_command(f"sudo usermod -aG docker {user}", in_container=False)
            yield {"type": "info", "message": f"User {user} added to docker group"}

        # Создание директории и Dockerfile
        await conn.run_command("sudo mkdir -p /opt/amnezia", in_container=False)

        dockerfile = f"""FROM {base_image}

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories || true
RUN apk update && apk add --no-cache bash curl dumb-init iptables ip6tables
RUN mkdir -p /opt/amnezia/awg /opt/amnezia/backups /opt/amnezia/client_configs
RUN echo '#!/bin/bash' > /opt/amnezia/start.sh && \\
    echo 'echo "Container startup"' >> /opt/amnezia/start.sh && \\
    echo 'awg-quick up /opt/amnezia/awg/awg0.conf' >> /opt/amnezia/start.sh && \\
    echo 'iptables -A INPUT -i awg0 -j ACCEPT' >> /opt/amnezia/start.sh && \\
    echo 'iptables -A FORWARD -i awg0 -j ACCEPT' >> /opt/amnezia/start.sh && \\
    echo 'iptables -A OUTPUT -o awg0 -j ACCEPT' >> /opt/amnezia/start.sh && \\
    echo 'iptables -A FORWARD -i awg0 -o eth0 -s 10.8.1.0/24 -j ACCEPT' >> /opt/amnezia/start.sh && \\
    echo 'iptables -A FORWARD -i awg0 -o eth1 -s 10.8.1.0/24 -j ACCEPT' >> /opt/amnezia/start.sh && \\
    echo 'iptables -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT' >> /opt/amnezia/start.sh && \\
    echo 'iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o eth0 -j MASQUERADE' >> /opt/amnezia/start.sh && \\
    echo 'iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o eth1 -j MASQUERADE' >> /opt/amnezia/start.sh && \\
    echo 'tail -f /dev/null' >> /opt/amnezia/start.sh && \\
    chmod +x /opt/amnezia/start.sh
ENTRYPOINT ["dumb-init", "/opt/amnezia/start.sh"]
"""
        await conn.write_file("/opt/amnezia/Dockerfile", dockerfile, in_container=False)

        # Сборка образа
        build_cmd = f"sudo docker build -t {container_name} -f /opt/amnezia/Dockerfile /opt/amnezia 2>&1"
        build_output = await conn.run_command(build_cmd, in_container=False)
        if "Successfully tagged" in build_output or "naming to docker.io" in build_output:
            yield {"type": "step", "name": "🔨 Docker image built", "success": True, "output": build_output}
        else:
            yield {"type": "error", "message": "❌ Docker build failed", "output": build_output}
            return

        # Остановка и удаление старого контейнера
        await conn.run_command(f"sudo docker stop {container_name} 2>/dev/null || true", in_container=False)
        await conn.run_command(f"sudo docker rm {container_name} 2>/dev/null || true", in_container=False)
        yield {"type": "step", "name": "🔄 Old container removed", "success": True}

        awg_params = generate_awg_config(awg_version)
        port = awg_params['port']

        # Запуск нового контейнера
        run_cmd = f"sudo docker run -d --name {container_name} --cap-add=NET_ADMIN --cap-add=NET_RAW --device=/dev/net/tun --restart unless-stopped -p {port}:{port}/udp {container_name}"
        run_output = await conn.run_command(run_cmd, in_container=False)
        if not run_output.strip():
            yield {"type": "error", "message": "❌ Failed to start container", "output": run_output}
            return
        yield {"type": "step", "name": "🚀 Container started", "success": True, "output": run_output}

        # Генерация ключей сервера (внутри контейнера)
        keys_script = f"""docker exec {container_name} sh -c '
mkdir -p /opt/amnezia/awg /opt/amnezia/backups /opt/amnezia/client_configs
PRIVATE_KEY=$(awg genkey)
echo "$PRIVATE_KEY" > /opt/amnezia/awg/server_private.key
echo "$PRIVATE_KEY" | awg pubkey > /opt/amnezia/awg/server_public.key
echo "Keys generated"
'"""
        keys_out = await conn.run_command(keys_script, in_container=False)
        yield {"type": "step", "name": "🔑 Server keys generated", "success": True, "output": keys_out}

        # Создание базового конфига сервера (внутри контейнера)
        config_script = f"""docker exec {container_name} sh -c '
PRIVATE_KEY=$(cat /opt/amnezia/awg/server_private.key)
cat > /opt/amnezia/awg/awg0.conf << EOF
{format_config(awg_params, awg_version)}
EOF
echo "Server config created"
'"""
        config_out = await conn.run_command(config_script, in_container=False)
        yield {"type": "step", "name": "📝 Server config created", "success": True, "output": config_out}

        # Перезапуск контейнера после внесения изменений
        await conn.run_command(f"sudo docker restart {container_name}", in_container=False)
        yield {"type": "step", "name": "🔄 Restarting container", "success": True}

        # Включение IP forwarding на хосте
        await conn.run_command("sudo sysctl -w net.ipv4.ip_forward=1", in_container=False)
        await conn.run_command("echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.conf", in_container=False)
        yield {"type": "step", "name": "🌐 IP forwarding enabled", "success": True}

        # Проверка статуса контейнера
        await asyncio.sleep(3)
        status = await conn.run_command(f"sudo docker ps --filter name={container_name} --format '{{{{.Status}}}}'", in_container=False)
        if "Up" in status:
            yield {"type": "success", "message": "✅ Server is ready!", "output": status}
        else:
            yield {"type": "error", "message": "❌ Container is not running", "output": status}

    except Exception as e:
        logger.error(f"Setup error: {e}")
        yield {"type": "error", "message": str(e)}
    finally:
        await conn.close()


MSG_INITIATION = 148
MSG_RESPONSE = 92
MSG_COOKIE_REPLY = 64


def generate_awg_config(awg_version: str = 'awg3') -> dict:
    """Генерирует параметры обфускации для AWG v2 или v3."""
    config = {}
    config['port'] = random.randint(10000, 65000)

    # Jc
    config['jc'] = random.randint(4, 7)
    config['jmin'] = 10
    config['jmax'] = 50

    # S1 — случайный, с ограничениями
    s1 = random.randint(15, 150)
    config['s1'] = s1

    # S2 — не должно совпадать по размеру пакета init/response
    while True:
        s2 = random.randint(15, 150)
        if s2 != s1 and s1 + MSG_INITIATION != s2 + MSG_RESPONSE:
            break
    config['s2'] = s2

    # S3 — не совпадает с init и response по размеру cookie
    while True:
        s3 = random.randint(8, 55)
        if (s3 != s1 and s3 != s2
                and s1 + MSG_INITIATION != s3 + MSG_COOKIE_REPLY
                and s2 + MSG_RESPONSE != s3 + MSG_COOKIE_REPLY):
            break
    config['s3'] = s3
    config['s4'] = 12

    if awg_version == 'awg3':
        # AWG 3.x: H1-H4 фиксированы (используются Header Protection, не заголовки)
        config['h1'] = '1'
        config['h2'] = '2'
        config['h3'] = '3'
        config['h4'] = '4'
        config['i1'] = '<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>'
        # Новые параметры AWG 3.x
        config['header_protection_key'] = base64.b64encode(os.urandom(32)).decode('ascii')
        config['content_padding_addition'] = '10-100'
        config['rekey_after_time'] = '100-120'
        config['rekey_timeout'] = '3-7'
        config['reject_after_time'] = '150-180'
        config['keepalive_timeout'] = '5-15'
        config['max_handshake_attempts'] = '15-20'
        config['random_trailers'] = 'on'
        config['disable_cookies'] = 'on'
    else:
        # AWG 2.x: H1-H4 — непересекающиеся диапазоны
        h1_min = random.randint(5, 2000000000)
        h1_max = random.randint(h1_min + 1, min(h1_min + 200000000, 2147483647))
        config['h1'] = f"{h1_min}-{h1_max}"
        h2_min = h1_max + random.randint(1, 100000000)
        h2_max = random.randint(h2_min + 1, min(h2_min + 200000000, 2147483647))
        config['h2'] = f"{h2_min}-{h2_max}"
        h3_min = h2_max + random.randint(1, 100000000)
        h3_max = random.randint(h3_min + 1, min(h3_min + 200000000, 2147483647))
        config['h3'] = f"{h3_min}-{h3_max}"
        h4_min = h3_max + random.randint(1, 100000000)
        h4_max = random.randint(h4_min + 1, min(h4_min + 200000000, 2147483647))
        config['h4'] = f"{h4_min}-{h4_max}"
        random_hex = secrets.token_hex(64)
        config['i1'] = f"<b 0x{random_hex}>"

    return config


def format_config(config: dict, awg_version: str = 'awg3') -> str:
    """Форматирует секцию [Interface] конфига сервера."""
    lines = [
        "[Interface]",
        f"ListenPort = {config['port']}",
        "PrivateKey = $PRIVATE_KEY",
        "Address = 10.8.1.1/32",
        f"Jc = {config['jc']}",
        f"Jmin = {config['jmin']}",
        f"Jmax = {config['jmax']}",
        f"S1 = {config['s1']}",
        f"S2 = {config['s2']}",
        f"S3 = {config['s3']}",
        f"S4 = {config['s4']}",
        f"H1 = {config['h1']}",
        f"H2 = {config['h2']}",
        f"H3 = {config['h3']}",
        f"H4 = {config['h4']}",
        f"I1 = {config['i1']}",
    ]
    if awg_version == 'awg3':
        lines += [
            f"HeaderProtectionKey = {config['header_protection_key']}",
            f"ContentPaddingAddition = {config['content_padding_addition']}",
            f"RekeyAfterTime = {config['rekey_after_time']}",
            f"RekeyTimeout = {config['rekey_timeout']}",
            f"RejectAfterTime = {config['reject_after_time']}",
            f"KeepaliveTimeout = {config['keepalive_timeout']}",
            f"MaxHandshakeAttempts = {config['max_handshake_attempts']}",
            f"RandomTrailers = {config['random_trailers']}",
            f"DisableCookies = {config['disable_cookies']}",
        ]
    return "\n".join(lines)