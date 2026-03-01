import asyncio
from typing import AsyncGenerator, Optional
from connection import SSHConnection
from logger import logger
import random
import secrets
import struct

async def setup_server_stream(
    conn: SSHConnection,
    sudo_password: Optional[str] = None
) -> AsyncGenerator[dict, None]:
    """
    Устанавливает AmneziaWG на удалённом сервере.
    Генерирует словари с прогрессом для WebSocket.
    """
    if sudo_password:
        conn.sudo_password = sudo_password

    try:
        yield {"type": "info", "message": "🔄 Connecting to server..."}
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
            # Повторная проверка
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

        # ---- 2. Создание директории и Dockerfile ----
        await conn.run_command("sudo mkdir -p /opt/amnezia", in_container=False)

        dockerfile = """FROM amneziavpn/amneziawg-go:latest

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
        # Записываем Dockerfile через sudo tee
        await conn.write_file("/opt/amnezia/Dockerfile", dockerfile, in_container=False)

        # ---- 3. Сборка образа ----
        build_cmd = "sudo docker build -t amnezia-awg2 -f /opt/amnezia/Dockerfile /opt/amnezia 2>&1"
        build_output = await conn.run_command(build_cmd, in_container=False)
        if "Successfully tagged" in build_output:
            yield {"type": "step", "name": "🔨 Docker image built", "success": True, "output": build_output}
        else:
            yield {"type": "error", "message": "❌ Docker build failed", "output": build_output}
            return

        # ---- 4. Остановка и удаление старого контейнера ----
        await conn.run_command("sudo docker stop amnezia-awg2 2>/dev/null || true", in_container=False)
        await conn.run_command("sudo docker rm amnezia-awg2 2>/dev/null || true", in_container=False)
        yield {"type": "step", "name": "🔄 Old container removed", "success": True}

        # ---- 5. Запуск нового контейнера ----
        run_cmd = "sudo docker run -d --name amnezia-awg2 --cap-add=NET_ADMIN --cap-add=NET_RAW --device=/dev/net/tun --restart unless-stopped -p 32308:32308/udp amnezia-awg2"
        run_output = await conn.run_command(run_cmd, in_container=False)
        if not run_output.strip():
            yield {"type": "error", "message": "❌ Failed to start container", "output": run_output}
            return
        yield {"type": "step", "name": "🚀 Container started", "success": True, "output": run_output}

        # ---- 6. Генерация ключей сервера (внутри контейнера) ----
        keys_script = """docker exec amnezia-awg2 sh -c '
mkdir -p /opt/amnezia/awg /opt/amnezia/backups /opt/amnezia/client_configs
PRIVATE_KEY=$(awg genkey)
echo "$PRIVATE_KEY" > /opt/amnezia/awg/server_private.key
echo "$PRIVATE_KEY" | awg pubkey > /opt/amnezia/awg/server_public.key
echo "Keys generated"
'"""
        keys_out = await conn.run_command(keys_script, in_container=False)  # команда сама содержит docker exec
        yield {"type": "step", "name": "🔑 Server keys generated", "success": True, "output": keys_out}

        # ---- 7. Создание базового конфига сервера (внутри контейнера) ----
        awg_params = generate_awg_config()
        include_i1 = True
        config_script = f"""docker exec amnezia-awg2 sh -c '
        PRIVATE_KEY=$(cat /opt/amnezia/awg/server_private.key)
        cat > /opt/amnezia/awg/awg0.conf << EOF
        {format_config(awg_params, include_i1)}
        EOF
        echo "Server config created"
        '"""
        config_out = await conn.run_command(config_script, in_container=False)
        yield {"type": "step", "name": "📝 Server config created", "success": True, "output": config_out}

        # ---- 8. Включение IP forwarding на хосте ----
        await conn.run_command("sudo docker restart amnezia-awg2", in_container=False)
        yield {"type": "step", "name": "🔄 Restarting container", "success": True}

        # ---- 8. Включение IP forwarding на хосте ----
        await conn.run_command("sudo sysctl -w net.ipv4.ip_forward=1", in_container=False)
        await conn.run_command("echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.conf", in_container=False)
        yield {"type": "step", "name": "🌐 IP forwarding enabled", "success": True}

        # ---- 9. Проверка статуса контейнера ----
        await asyncio.sleep(3)
        status = await conn.run_command("sudo docker ps --filter name=amnezia-awg2 --format '{{.Status}}'", in_container=False)
        if "Up" in status:
            yield {"type": "success", "message": "✅ Server is ready!", "output": status}
        else:
            yield {"type": "error", "message": "❌ Container is not running", "output": status}

    except Exception as e:
        logger.error(f"Setup error: {e}")
        yield {"type": "error", "message": str(e)}
    finally:
        await conn.close()

def generate_awg_config():
    config = {}
    config['port'] = random.randint(10000, 65000)
    config['jc'] = random.randint(3, 8)
    config['jmin'] = random.randint(5, 20)
    config['jmax'] = random.randint(30, 70)
    if config['jmin'] >= config['jmax']:
        config['jmax'] = config['jmin'] + random.randint(10, 30)
    config['s1'] = random.randint(30, 150)     # 40 в примере
    config['s2'] = random.randint(20, 150)     # 138 в примере
    config['s3'] = random.randint(1, 50)       # 42 в примере
    config['s4'] = random.randint(5, 30)       # 15 в примере
    config['h1'] = f"{random.randint(10**9, 2*10**9)}-{random.randint(10**9, 2*10**9)}"
    config['h2'] = f"{random.randint(1_900_000_000, 2_100_000_000)}-{random.randint(2_000_000_000, 2_200_000_000)}"
    config['h3'] = f"{random.randint(2_100_000_000, 2_200_000_000)}-{random.randint(2_130_000_000, 2_150_000_000)}"
    config['h4'] = f"{random.randint(2_140_000_000, 2_200_000_000)}-{random.randint(2_140_000_000, 2_200_000_000)}"
    i1_hex = secrets.token_hex(64)
    config['i1'] = f"<b 0x{i1_hex}>"
    return config

def format_config(config, include_i1=False):
    """Форматирует конфиг с возможностью включить/выключить I1"""
    lines = [
        "[Interface]",
        f"ListenPort = {config['port']}",
        "PrivateKey = $PRIVATE_KEY",
        "Address = 10.8.1.0/24",
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
    ]
    if include_i1:
        lines.append(f"# I1 = {config['i1']}")
    return "\n".join(lines)