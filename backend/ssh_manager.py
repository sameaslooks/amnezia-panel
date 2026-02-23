import asyncssh # type: ignore
import asyncio
import os
from typing import Optional, AsyncGenerator

class SSHManager:
    def __init__(self):
        self.connections = {}
        self.deploy_logs = {}
        self.log_callbacks = {}
    
    async def test_connection(self, host, port, user, ssh_key_path, passphrase=None, password=None):
        """Проверяет подключение по SSH"""
        conn = None
        try:
            if password:
                # Подключение по паролю
                conn = await asyncssh.connect(
                    host=host,
                    port=port,
                    username=user,
                    password=password,
                    known_hosts=None
                )
            elif passphrase:
                # Подключение по ключу с парольной фразой
                conn = await asyncssh.connect(
                    host=host,
                    port=port,
                    username=user,
                    client_keys=[(ssh_key_path, passphrase)],
                    known_hosts=None
                )
            else:
                # Подключение по ключу без фразы
                conn = await asyncssh.connect(
                    host=host,
                    port=port,
                    username=user,
                    client_keys=[ssh_key_path],
                    known_hosts=None
                )
            
            # Тестовая команда с sudo (проверяем, нужен ли пароль)
            result = await conn.run("sudo -n true 2>/dev/null && echo 'sudo_ok' || echo 'sudo_needs_password'")
            
            if "sudo_ok" in result.stdout:
                # sudo без пароля работает
                return {"success": True, "sudo": "passwordless"}
            else:
                # sudo требует пароль
                return {"success": True, "sudo": "needs_password"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def execute_with_logs(
        self,
        host: str,
        port: int,
        user: str,
        ssh_key_path: str,
        passphrase: Optional[str],
        command: str,
        deploy_id: str
    ) -> AsyncGenerator[str, None]:
        """Выполняет команду и генерирует логи в реальном времени"""
        conn = None
        try:
            # Подключение
            yield f"🔄 Connecting to {user}@{host}:{port}...\n"
            
            if passphrase:
                conn = await asyncssh.connect(
                    host=host, port=port, username=user,
                    client_keys=[(ssh_key_path, passphrase)],
                    known_hosts=None
                )
            else:
                conn = await asyncssh.connect(
                    host=host, port=port, username=user,
                    client_keys=[ssh_key_path],
                    known_hosts=None
                )
            
            yield f"✅ Connected successfully\n"
            yield f"📦 Executing: {command}\n"
            yield "-" * 50 + "\n"
            
            # Создаём процесс с поточным выводом
            process = await conn.create_process(command)
            
            # Читаем stdout и stderr построчно
            async for line in process.stdout:
                clean_line = line.rstrip('\n')
                yield f"{clean_line}\n"
                await asyncio.sleep(0.01)
            
            # Проверяем код возврата
            await process.wait()
            if process.returncode != 0:
                error_output = await process.stderr.read()
                yield f"❌ Command failed with code {process.returncode}\n"
                if error_output:
                    yield f"Error: {error_output}\n"
            else:
                yield f"✅ Command completed successfully\n"
            
            yield "-" * 50 + "\n"
            
        except Exception as e:
            yield f"❌ SSH Error: {str(e)}\n"
        finally:
            if conn:
                conn.close()
                await conn.wait_closed()
            yield f"🔌 Connection closed\n"
    
    async def deploy_server(self, deploy_id: str, server_config: dict):
        """Полный процесс деплоя сервера"""
        
        # Сначала определяем список команд
        commands = [
            ("📦 Checking Docker", 
            "command -v docker || echo 'Docker not installed'"),
            
            ("🔧 Checking Docker installation", 
            "command -v docker && echo 'DOCKER_ALREADY_INSTALLED' || echo 'DOCKER_NEEDS_INSTALL'"),

            ("🔧 Cleaning old Docker repository entries", 
            "sudo rm -f /etc/apt/sources.list.d/docker.list /etc/apt/sources.list.d/docker.sources"),

            ("🔧 Setting up Docker repository", 
            "if [ ! -f /etc/apt/keyrings/docker.asc ]; then "
            "sudo apt update && "
            "sudo apt install -y ca-certificates curl && "
            "sudo install -m 0755 -d /etc/apt/keyrings && "
            "sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && "
            "sudo chmod a+r /etc/apt/keyrings/docker.asc; "
            "else echo 'Docker repository already configured'; fi"),

            ("🔧 Adding Docker repository", 
            "if [ ! -f /etc/apt/sources.list.d/docker.sources ]; then "
            "CODENAME=$(lsb_release -cs) && "
            "sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null << EOF\n"
            "Types: deb\n"
            "URIs: https://download.docker.com/linux/ubuntu\n"
            "Suites: $CODENAME\n"
            "Components: stable\n"
            "Signed-By: /etc/apt/keyrings/docker.asc\n"
            "EOF\n"
            "else echo 'Docker repository already added'; fi"),

            ("🔧 Installing Docker packages", 
            "if ! command -v docker >/dev/null 2>&1; then "
            "sudo apt update && "
            "sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; "
            "else echo 'Docker packages already installed'; fi"),
            
            ("📁 Creating directories", 
            "sudo mkdir -p /opt/amnezia/awg /opt/amnezia/backups"),
            
            ("📝 Creating Dockerfile", 
            "sudo tee /opt/amnezia/Dockerfile > /dev/null << 'EOF'\n"
            "FROM amneziavpn/amneziawg-go:latest\n\n"
            "LABEL maintainer=\"AmneziaVPN\"\n\n"
            "# Исправляем репозитории Alpine и устанавливаем пакеты\n"
            "RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories || true && \\\n"
            "    apk update && \\\n"
            "    apk add --no-cache bash curl dumb-init\n\n"
            "RUN apk --update upgrade --no-cache\n\n"
            "RUN mkdir -p /opt/amnezia\n"
            "RUN echo -e \"#!/bin/bash\\ntail -f /dev/null\" > /opt/amnezia/start.sh\n"
            "RUN chmod a+x /opt/amnezia/start.sh\n\n"
            "RUN echo -e \" \\n\\\n"
            "  fs.file-max = 51200 \\n\\\n"
            "  net.core.rmem_max = 67108864 \\n\\\n"
            "  net.core.wmem_max = 67108864 \\n\\\n"
            "  net.core.netdev_max_backlog = 250000 \\n\\\n"
            "  net.core.somaxconn = 4096 \\n\\\n"
            "  net.ipv4.tcp_syncookies = 1 \\n\\\n"
            "  net.ipv4.tcp_tw_reuse = 1 \\n\\\n"
            "  net.ipv4.tcp_fin_timeout = 30 \\n\\\n"
            "  net.ipv4.tcp_keepalive_time = 1200 \\n\\\n"
            "  net.ipv4.ip_local_port_range = 10000 65000 \\n\\\n"
            "  net.ipv4.tcp_max_syn_backlog = 8192 \\n\\\n"
            "  net.ipv4.tcp_max_tw_buckets = 5000 \\n\\\n"
            "  net.ipv4.tcp_fastopen = 3 \\n\\\n"
            "  net.ipv4.tcp_mem = 25600 51200 102400 \\n\\\n"
            "  net.ipv4.tcp_rmem = 4096 87380 67108864 \\n\\\n"
            "  net.ipv4.tcp_wmem = 4096 65536 67108864 \\n\\\n"
            "  net.ipv4.tcp_mtu_probing = 1 \\n\\\n"
            "  net.ipv4.tcp_congestion_control = hybla \\n\\\n"
            "  \" | tee -a /etc/sysctl.conf && \\\n"
            "  mkdir -p /etc/security && \\\n"
            "  echo -e \" \\n\\\n"
            "  * soft nofile 51200 \\n\\\n"
            "  * hard nofile 51200 \\n\\\n"
            "  \" | tee -a /etc/security/limits.conf\n\n"
            "ENTRYPOINT [ \"dumb-init\", \"/opt/amnezia/start.sh\" ]\n"
            "CMD [ \"\" ]\n"
            "EOF"),
            
            ("🔨 Building Docker image", 
            "cd /opt/amnezia && sudo docker build -t amnezia-awg2 . 2>&1"),
            
            ("🔄 Stopping old container if exists", 
            "sudo docker stop amnezia-awg2 2>/dev/null || true && sudo docker rm amnezia-awg2 2>/dev/null || true"),
            
            ("🚀 Starting new container", 
            "sudo docker run -d --name amnezia-awg2 "
            "--cap-add=NET_ADMIN --cap-add=NET_RAW "
            "--device=/dev/net/tun "
            "-v /opt/amnezia/awg:/opt/amnezia/awg "
            "-v /opt/amnezia/backups:/opt/amnezia/backups "
            "--restart unless-stopped "
            "-p 32308:32308/udp "
            "-e AWG_SUBNET_IP=10.8.1.0 "
            "-e WIREGUARD_SUBNET_CIDR=24 "
            "amnezia-awg2"),
            
            ("✅ Checking container status", 
            "sudo docker ps --filter name=amnezia-awg2 --format 'Status: {{.Status}}'"),
            
            ("📋 Generating keys", 
            "sudo docker exec amnezia-awg2 sh -c '"
            "rm -f /opt/amnezia/awg/server_private.key /opt/amnezia/awg/server_public.key && "
            "PRIVATE_KEY=$(awg genkey) && "
            "echo \"$PRIVATE_KEY\" > /opt/amnezia/awg/server_private.key && "
            "echo \"$PRIVATE_KEY\" | awg pubkey > /opt/amnezia/awg/server_public.key && "
            "chmod 600 /opt/amnezia/awg/server_private.key /opt/amnezia/awg/server_public.key"
            "'"),

            ("📝 Creating server config", 
            "sudo docker exec amnezia-awg2 sh -c '"
            "PRIVATE_KEY=$(cat /opt/amnezia/awg/server_private.key) && "
            "cat > /opt/amnezia/awg/awg0.conf << EOF\n"
            "[Interface]\n"
            "ListenPort = 32308\n"
            "PrivateKey = $PRIVATE_KEY\n"
            "Jc = 4\n"
            "Jmin = 10\n"
            "Jmax = 50\n"
            "S1 = 95\n"
            "S2 = 21\n"
            "S3 = 6\n"
            "S4 = 10\n"
            "H1 = 1144016577-1678296790\n"
            "H2 = 2067003202-2073469039\n"
            "H3 = 2118455839-2136843295\n"
            "H4 = 2142407594-2142521231\n"
            "EOF\n"
            "'"),

            ("📝 Creating startup script", 
            "sudo docker exec amnezia-awg2 sh -c '"
            "cat > /opt/amnezia/start.sh << 'EOF'\n"
            "#!/bin/bash\n\n"
            "echo \"Container startup\"\n\n"
            "# kill daemons in case of restart\n"
            "awg-quick down /opt/amnezia/awg/awg0.conf 2>/dev/null || true\n\n"
            "# start daemons if configured\n"
            "if [ -f /opt/amnezia/awg/awg0.conf ]; then\n"
            "    awg-quick up /opt/amnezia/awg/awg0.conf\n"
            "fi\n\n"
            "# Allow traffic on the TUN interface\n"
            "iptables -A INPUT -i awg0 -j ACCEPT 2>/dev/null || true\n"
            "iptables -A FORWARD -i awg0 -j ACCEPT 2>/dev/null || true\n"
            "iptables -A OUTPUT -o awg0 -j ACCEPT 2>/dev/null || true\n\n"
            "# Allow forwarding traffic only from the VPN\n"
            "iptables -A FORWARD -i awg0 -o eth0 -s 10.8.1.0/24 -j ACCEPT 2>/dev/null || true\n"
            "iptables -A FORWARD -i awg0 -o eth1 -s 10.8.1.0/24 -j ACCEPT 2>/dev/null || true\n\n"
            "iptables -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true\n\n"
            "iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o eth0 -j MASQUERADE 2>/dev/null || true\n"
            "iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o eth1 -j MASQUERADE 2>/dev/null || true\n\n"
            "tail -f /dev/null\n"
            "EOF\n"
            "'"),

            ("📝 Setting execute permission", 
            "sudo docker exec amnezia-awg2 chmod +x /opt/amnezia/start.sh"),

            ("🔄 Restarting container to apply startup script", 
            "sudo docker restart amnezia-awg2"),
        ]
        
        conn = None
        try:
            # Подключаемся ОДИН РАЗ для всех команд
            yield f"🔌 Connecting to {server_config['user']}@{server_config['host']}:{server_config['port']}...\n"
            
            if server_config.get("passphrase"):
                conn = await asyncssh.connect(
                    host=server_config["host"],
                    port=server_config["port"],
                    username=server_config["user"],
                    client_keys=[(server_config["ssh_key_path"], server_config["passphrase"])],
                    known_hosts=None
                )
            else:
                conn = await asyncssh.connect(
                    host=server_config["host"],
                    port=server_config["port"],
                    username=server_config["user"],
                    client_keys=[server_config["ssh_key_path"]],
                    known_hosts=None
                )
            
            yield f"✅ Connected successfully\n"
            
            for i, (step_name, command) in enumerate(commands):
                print(f"🔹 [{deploy_id}] Step {i+1}: {step_name}")
                yield f"\n🔹 {step_name}\n"
                yield f"📦 Executing: {command}\n"
                yield "-" * 50 + "\n"
                
                # Выполняем команду через существующее соединение
                result = await conn.run(command)
                
                # Отправляем вывод
                if result.stdout:
                    for line in result.stdout.split('\n'):
                        if line.strip():
                            yield f"{line}\n"
                if result.stderr:
                    for line in result.stderr.split('\n'):
                        if line.strip():
                            yield f"Error: {line}\n"
                
                if result.exit_status == 0:
                    yield f"✅ Command completed successfully\n"
                else:
                    yield f"❌ Command failed with code {result.exit_status}\n"
                
                yield "-" * 50 + "\n"
            
        except Exception as e:
            error_msg = f"❌ SSH Error: {str(e)}\n"
            print(error_msg)
            yield error_msg
        finally:
            if conn:
                conn.close()
                await conn.wait_closed()
            yield f"🔌 Connection closed\n"

    async def execute_command(self, server_config: dict, command: str, input_data: str = None):
        """Выполняет команду на удалённом сервере и возвращает результат"""
        conn = None
        try:
            conn = await asyncssh.connect(
                host=server_config["host"],
                port=server_config["port"],
                username=server_config["user"],
                client_keys=[server_config["ssh_key_path"]],
                known_hosts=None
            )
            
            if input_data:
                # Для команд с входными данными (например, echo)
                process = await conn.create_process(command)
                process.stdin.write(input_data)
                process.stdin.write_eof()
                await process.stdin.drain()
                await process.wait()
                
                stdout = await process.stdout.read()
                stderr = await process.stderr.read()
                returncode = process.returncode
            else:
                result = await conn.run(command)
                stdout, stderr, returncode = result.stdout, result.stderr, result.exit_status
            
            return {
                "success": returncode == 0,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": returncode
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1
            }
        finally:
            if conn:
                conn.close()
                await conn.wait_closed()

ssh_manager = SSHManager()