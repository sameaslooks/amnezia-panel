import re
import subprocess
import json
from typing import List, Dict

class AWGManager:
    def __init__(self, container_name: str = "amnezia-awg2"):
        self.container_name = container_name
    
    def _exec_in_container(self, command: str) -> str:
        """Выполняет команду в контейнере Amnezia через docker exec"""
        try:
            result = subprocess.run(
                ["docker", "exec", self.container_name, "bash", "-c", command],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            return ""
    
    def get_clients(self) -> List[Dict]:
        """Получает клиентов из clientsTable с именами устройств"""
        table_json = self._exec_in_container("cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo '[]'")
        
        try:
            clients_data = json.loads(table_json)
            client_names = {item["clientId"]: item.get("userData", {}).get("clientName", "Unknown") 
                          for item in clients_data if "clientId" in item}
        except:
            client_names = {}
        
        config = self._exec_in_container("cat /opt/amnezia/awg/awg0.conf")
        clients = []
        peers = config.split('[Peer]')[1:]
        
        for i, peer in enumerate(peers, 1):
            key_match = re.search(r'PublicKey\s*=\s*(\S+)', peer)
            ip_match = re.search(r'AllowedIPs\s*=\s*([\d\.]+/\d+)', peer)
            
            if key_match:
                pub_key = key_match.group(1)
                name = client_names.get(pub_key, f"Client {i}")
                
                clients.append({
                    "name": name,
                    "public_key": pub_key,
                    "ip": ip_match.group(1) if ip_match else "",
                })
        
        return clients
    
    def get_traffic(self) -> List[Dict]:
        """Получает статистику трафика из awg show"""
        self.collect_traffic_stats()  # ← добавляем эту строку
        
        output = self._exec_in_container("awg show")
        if not output:
            return []
        
        traffic = []
        lines = output.split('\n')
        current_peer = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('peer:'):
                current_peer = line.split('peer:')[1].strip()
                traffic.append({
                    "public_key": current_peer,
                    "transfer": "0 B",
                    "latest_handshake": "Never"
                })
            elif 'transfer:' in line and current_peer:
                traffic[-1]["transfer"] = line.split('transfer:')[1].strip()
            elif 'latest handshake:' in line and current_peer:
                traffic[-1]["latest_handshake"] = line.split('latest handshake:')[1].strip()
        
        return traffic
    
    def _get_next_ip(self, config: str) -> str:
        """Определяет следующий свободный IP"""
        used_ips = []
        ip_matches = re.findall(r'AllowedIPs\s*=\s*([\d\.]+/\d+)', config)
        for ip in ip_matches:
            used_ips.append(ip.split('/')[0])
        
        # Определяем подсеть из Address интерфейса
        address_match = re.search(r'Address\s*=\s*([\d\.]+/\d+)', config)
        if address_match:
            base_ip = address_match.group(1).split('/')[0].rsplit('.', 1)[0]
        else:
            base_ip = "10.8.1"
        
        # Ищем свободный IP
        for i in range(2, 255):
            candidate = f"{base_ip}.{i}"
            if candidate not in used_ips:
                return f"{candidate}/32"
        
        return f"{base_ip}.254/32"
    
    def _get_server_ip(self) -> str:
        """Получает внешний IP сервера"""
        try:
            result = subprocess.run(['curl', '-s', 'ifconfig.me'], 
                                  capture_output=True, timeout=5)
            return result.stdout.decode().strip()
        except:
            return "YOUR_SERVER_IP"
    
    def add_client(self, name: str) -> Dict:
        """Добавляет нового клиента"""
        # Генерируем ключи
        private_key = self._exec_in_container("awg genkey").strip()
        public_key = self._exec_in_container(f"echo '{private_key}' | awg pubkey").strip()
        
        # Генерируем Pre-shared ключ для этого пира
        psk = self._exec_in_container("wg genpsk").strip()
        
        # Получаем текущий конфиг
        config = self._exec_in_container("cat /opt/amnezia/awg/awg0.conf")
        
        # Определяем следующий IP
        next_ip = self._get_next_ip(config)
        
        # Добавляем секцию пира в конфиг (с PSK)
        peer_section = f"""
[Peer]
PublicKey = {public_key}
PresharedKey = {psk}
AllowedIPs = {next_ip}
"""
        new_config = config.rstrip() + "\n" + peer_section
        
        # Записываем новый конфиг в файл
        self._exec_in_container(f"cat > /opt/amnezia/awg/awg0.conf << 'EOF'\n{new_config}\nEOF")
        
        # Синхронизируем интерфейс с файлом
        self._exec_in_container("awg syncconf awg0 <(cat /opt/amnezia/awg/awg0.conf)")
        
        # Добавляем запись в clientsTable
        table_json = self._exec_in_container("cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo '[]'")
        try:
            clients_table = json.loads(table_json)
        except:
            clients_table = []
        
        from datetime import datetime
        new_entry = {
            "clientId": public_key,
            "userData": {
                "clientName": name,
                "creationDate": datetime.now().strftime("%a %b %d %H:%M:%S %Y"),
                "allowedIps": next_ip
            }
        }
        clients_table.append(new_entry)
        
        # Записываем обновленную таблицу
        import json as json_lib
        self._exec_in_container(f"cat > /opt/amnezia/awg/clientsTable << 'EOF'\n{json_lib.dumps(clients_table, indent=4)}\nEOF")
        
        # Получаем параметры сервера для конфига клиента
        client_config = self.get_client_config(public_key)
        
        from database import create_client
        create_client(public_key, name, next_ip, private_key)
        # Save client config file inside the AWG container for later recovery
        safe_path = f"/opt/amnezia/awg/client_configs"
        # create directory and write file
        write_cmd = (
            f"mkdir -p {safe_path} && cat > {safe_path}/{public_key}.conf << 'EOF'\n{client_config}\nEOF"
        )
        self._exec_in_container(write_cmd)
        return {
            "name": name,
            "ip": next_ip,
            "public_key": public_key,
            "config": client_config
        }
    
    def delete_client(self, public_key: str):
        """Удаляет клиента"""
        # Читаем текущий конфиг
        config = self._exec_in_container("cat /opt/amnezia/awg/awg0.conf")
        
        # Разбиваем на строки
        lines = config.split('\n')
        new_lines = []
        skip = False
        in_peer_section = False
        
        for line in lines:
            if line.startswith('[Peer]'):
                # Начинаем новую секцию пира
                in_peer_section = True
                # Проверяем, не наш ли это пир (посмотрим следующие строки)
                # Пока не знаем, поэтому добавляем временно
                new_lines.append(line)
            elif in_peer_section:
                if f"PublicKey = {public_key}" in line:
                    # Это наш пир - удаляем всю секцию (откатываем)
                    # Удаляем последнюю добавленную строку [Peer]
                    while new_lines and new_lines[-1] != '[Peer]':
                        new_lines.pop()
                    if new_lines and new_lines[-1] == '[Peer]':
                        new_lines.pop()
                    skip = True
                elif not skip:
                    # Не наш пир, оставляем
                    new_lines.append(line)
                # Если дошли до пустой строки или следующего [Peer] - сбрасываем флаги
                if line.strip() == '' or line.startswith('['):
                    in_peer_section = False
                    skip = False
            else:
                # Вне секции пира - просто добавляем
                new_lines.append(line)
        
        new_config = '\n'.join(new_lines)
        
        # Записываем новый конфиг
        self._exec_in_container(f"cat > /opt/amnezia/awg/awg0.conf << 'EOF'\n{new_config}\nEOF")
        
        # Синхронизируем интерфейс с файлом
        self._exec_in_container("awg syncconf awg0 <(cat /opt/amnezia/awg/awg0.conf)")
        
        # Удаляем из clientsTable
        table_json = self._exec_in_container("cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo '[]'")
        try:
            clients_table = json.loads(table_json)
            clients_table = [item for item in clients_table if item.get("clientId") != public_key]
            
            import json as json_lib
            self._exec_in_container(f"cat > /opt/amnezia/awg/clientsTable << 'EOF'\n{json_lib.dumps(clients_table, indent=4)}\nEOF")
        except:
            pass

    def get_client_config(self, public_key: str) -> str:
        """Генерирует конфиг для клиента по его публичному ключу"""
        config = self._exec_in_container("cat /opt/amnezia/awg/awg0.conf")
        
        # Ищем приватный ключ сервера
        priv_match = re.search(r'PrivateKey\s*=\s*(\S+)', config)
        if not priv_match:
            return ""
        
        server_private = priv_match.group(1)
        server_public = self._exec_in_container(f"echo '{server_private}' | awg pubkey").strip()
        
        # Ищем IP клиента
        peers = config.split('[Peer]')[1:]
        client_ip = None
        peer_text = None
        for peer in peers:
            if public_key in peer:
                peer_text = peer
                ip_match = re.search(r'AllowedIPs\s*=\s*([\d\.]+/\d+)', peer)
                if ip_match:
                    client_ip = ip_match.group(1)
                break
        
        if not client_ip:
            return ""
        
        # Получаем приватный ключ из БД
        from database import get_client
        client_data = get_client(public_key)
        private_key = client_data.get('private_key', '') if client_data else ''
        # Если в БД нет приватного ключа — пробуем загрузить из сохранённого конфига
        if not private_key:
            try:
                cfg = self._exec_in_container(f"cat /opt/amnezia/awg/client_configs/{public_key}.conf 2>/dev/null || true")
                if cfg:
                    m = re.search(r'PrivateKey\s*=\s*(\S+)', cfg)
                    if m:
                        private_key = m.group(1)
                        # Сохраняем в БД, не перезаписывая существующее не-пустое значение
                        from database import create_client
                        # get existing name/ip from DB or peers
                        name = client_data.get('name') if client_data else ''
                        ip = client_ip
                        create_client(public_key, name, ip, private_key)
            except Exception:
                pass
        # Если всё ещё нет приватного ключа — ищем по всем файлам в /opt/amnezia/awg
        if not private_key:
            try:
                # Получаем список файлов, где встречается эта PublicKey
                files_list = self._exec_in_container(f"grep -R -l \"PublicKey = {public_key}\" /opt/amnezia/awg 2>/dev/null || true")
                for fp in files_list.splitlines():
                    fp = fp.strip()
                    if not fp:
                        continue
                    file_contents = self._exec_in_container(f"cat {fp} 2>/dev/null || true")
                    m2 = re.search(r'PrivateKey\s*=\s*(\S+)', file_contents)
                    if m2:
                        private_key = m2.group(1)
                        from database import create_client
                        name = client_data.get('name') if client_data else ''
                        ip = client_ip
                        create_client(public_key, name, ip, private_key)
                        break
            except Exception:
                pass
        # Параметры obfuscation и прочие поля (S/H/I)
        def find(pattern, default=""):
            m = re.search(pattern, config)
            return m.group(1) if m else default

        jc = find(r'Jc\s*=\s*(\d+)', '5')
        jmin = find(r'Jmin\s*=\s*(\d+)', '50')
        jmax = find(r'Jmax\s*=\s*(\d+)', '1000')

        s1 = find(r'S1\s*=\s*(\d+)', '')
        s2 = find(r'S2\s*=\s*(\d+)', '')
        s3 = find(r'S3\s*=\s*(\d+)', '')
        s4 = find(r'S4\s*=\s*(\d+)', '')

        h1 = find(r'H1\s*=\s*(\S+)', '')
        h2 = find(r'H2\s*=\s*(\S+)', '')
        h3 = find(r'H3\s*=\s*(\S+)', '')
        h4 = find(r'H4\s*=\s*(\S+)', '')

        # I1..I5 - preserve raw (may be long blob or empty)
        i1 = find(r'I1\s*=\s*(.*)', '')

        # ListenPort (use actual value if present)
        listen_port = find(r'ListenPort\s*=\s*(\d+)', '')

        # DNS from server params (fallback to common defaults)
        dns = '1.1.1.1, 1.0.0.1'

        server_host = self._get_server_ip()

        # PresharedKey for this peer (if present in peer block)
        psk = ''
        if peer_text:
            pm = re.search(r'PresharedKey\s*=\s*(\S+)', peer_text)
            if pm:
                psk = pm.group(1)

        # Формируем строгий конфиг в требуемом формате
        endpoint = f"{server_host}:{listen_port}" if listen_port else f"{server_host}:32308"

        psk_line = f"PresharedKey = {psk}\n" if psk else ""

            # Build config lines, omitting empty I1..I5 lines
        iface_lines = [
            "[Interface]",
            f"Address = {client_ip}",
            f"DNS = {dns}",
            f"PrivateKey = {private_key}",
            f"Jc = {jc}",
            f"Jmin = {jmin}",
            f"Jmax = {jmax}",
            f"S1 = {s1}",
            f"S2 = {s2}",
            f"S3 = {s3}",
            f"S4 = {s4}",
            f"H1 = {h1}",
            f"H2 = {h2}",
            f"H3 = {h3}",
            f"H4 = {h4}",
        ]

        # Only include I1..I5 if non-empty
        if i1 and i1.strip():
            iface_lines.append(f"I1 = {i1}")

        iface_lines.append("")
        iface = "\n".join(iface_lines)

        peer_lines = [
            "[Peer]",
            f"PublicKey = {server_public}",
        ]

        if psk:
            peer_lines.append(f"PresharedKey = {psk}")

        peer_lines.extend([
            "AllowedIPs = 0.0.0.0/0, ::/0",
            f"Endpoint = {endpoint}",
            "PersistentKeepalive = 25",
        ])

        return iface + "\n" + "\n".join(peer_lines)

    def get_traffic_bytes(self) -> Dict[str, Dict]:
        """Получает трафик в байтах для каждого клиента"""
        output = self._exec_in_container("awg show")
        if not output:
            return {}
        
        traffic = {}
        lines = output.split('\n')
        current_peer = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('peer:'):
                current_peer = line.split('peer:')[1].strip()
            elif 'transfer:' in line and current_peer:
                # Парсим строку вида "1.23 GiB received, 4.56 GiB sent"
                transfer = line.split('transfer:')[1].strip()
                received_str, sent_str = transfer.split(', received:')[1].strip(), transfer.split('sent:')[1].strip()
                
                # Конвертируем в байты
                received = self._parse_bytes(received_str)
                sent = self._parse_bytes(sent_str)
                
                traffic[current_peer] = {
                    "received": received,
                    "sent": sent,
                    "total": received + sent
                }
        
        return traffic

    def _parse_bytes(self, size_str: str) -> int:
        """Конвертирует строку вида '1.23 GiB' в байты"""
        try:
            size_str = size_str.strip()
            if 'GiB' in size_str:
                return int(float(size_str.replace('GiB', '').strip()) * 1024**3)
            elif 'MiB' in size_str:
                return int(float(size_str.replace('MiB', '').strip()) * 1024**2)
            elif 'KiB' in size_str:
                return int(float(size_str.replace('KiB', '').strip()) * 1024)
            elif 'B' in size_str:
                return int(float(size_str.replace('B', '').strip()))
        except:
            return 0
        return 0
    
    def _parse_bytes(self, size_str: str) -> int:
        """Convert human-readable sizes like '1.23 GiB' to bytes."""
        try:
            s = size_str.strip()
            if 'GiB' in s:
                return int(float(s.replace('GiB', '').strip()) * 1024**3)
            if 'MiB' in s:
                return int(float(s.replace('MiB', '').strip()) * 1024**2)
            if 'KiB' in s:
                return int(float(s.replace('KiB', '').strip()) * 1024)
            if 'B' in s:
                return int(float(s.replace('B', '').strip()))
        except Exception:
            return 0
        return 0

    def collect_traffic_stats(self):
        """Collect traffic from `awg show`, parse and update DB."""
        output = self._exec_in_container("awg show")
        if not output:
            return

        lines = output.split('\n')
        current_peer = None

        for line in lines:
            line = line.strip()
            if line.startswith('peer:'):
                current_peer = line.split('peer:')[1].strip()
            elif 'transfer:' in line and current_peer:
                transfer = line.split('transfer:')[1].strip()
                try:
                    # Parse "1.23 GiB received, 4.56 GiB sent"
                    parts = transfer.split(',')
                    received_str = parts[0].replace('received', '').strip()
                    sent_str = parts[1].replace('sent', '').strip()

                    received = self._parse_bytes(received_str)
                    sent = self._parse_bytes(sent_str)

                    from database import update_traffic_usage
                    update_traffic_usage(current_peer, received, sent, self)
                except Exception:
                    continue
                    
    def block_client(self, public_key: str):
        """Блокирует клиента через iptables (по IP)"""
        # Находим IP клиента
        config = self._exec_in_container("cat /opt/amnezia/awg/awg0.conf")
        peers = config.split('[Peer]')[1:]
        
        client_ip = None
        for peer in peers:
            if public_key in peer:
                ip_match = re.search(r'AllowedIPs\s*=\s*([\d\.]+/\d+)', peer)
                if ip_match:
                    client_ip = ip_match.group(1)
                    break
        
        if not client_ip:
            return False
        
        ip = client_ip.split('/')[0]
        
        # Блокируем в FORWARD (оба направления)
        self._exec_in_container(f"iptables -I FORWARD 1 -s {ip} -j DROP")
        self._exec_in_container(f"iptables -I FORWARD 1 -d {ip} -j DROP")
        
        print(f"Blocked {ip} ({public_key[:20]}...)")
        return True

    def unblock_client(self, public_key: str):
        """Разблокирует клиента"""
        config = self._exec_in_container("cat /opt/amnezia/awg/awg0.conf")
        peers = config.split('[Peer]')[1:]
        
        client_ip = None
        for peer in peers:
            if public_key in peer:
                ip_match = re.search(r'AllowedIPs\s*=\s*([\d\.]+/\d+)', peer)
                if ip_match:
                    client_ip = ip_match.group(1)
                    break
        
        if not client_ip:
            return False
        
        ip = client_ip.split('/')[0]
        
        # Удаляем правила
        self._exec_in_container(f"iptables -D FORWARD -s {ip} -j DROP 2>/dev/null || true")
        self._exec_in_container(f"iptables -D FORWARD -d {ip} -j DROP 2>/dev/null || true")
        
        print(f"Unblocked {ip} ({public_key[:20]}...)")
        return True

    def sync_iptables_with_db(self):
        """Синхронизирует iptables с базой данных"""
 
        # client["ip"] уже содержит /32, но нам нужно это проверить       from database import get_all_clients
        clients = get_all_clients()
        
        for client in clients:
            if not client['is_active']:
                self.block_client(client['public_key'])
            else:
                self.unblock_client(client['public_key'])
    
    def generate_amnezia_vpn_link(
        self,
        client_ip: str,
        client_private_key: str,
        client_public_key: str
    ) -> str:
        import json
        import struct
        import base64
        import zlib
        import re
        from collections import OrderedDict

        server_ip = self._get_server_ip()

        config = self._exec_in_container("cat /opt/amnezia/awg/awg0.conf")

        # Получаем порт из конфига
        port_match = re.search(r'ListenPort\s*=\s*(\d+)', config)
        server_port = port_match.group(1)
        # Публичный ключ сервера из конфига
        priv_match = re.search(r'PrivateKey\s*=\s*(\S+)', config)
        server_public = ""
        if priv_match:
            private_key = priv_match.group(1)
            server_public = self._exec_in_container(f"echo '{private_key}' | awg pubkey").strip()

        def get(pattern, default=""):
            m = re.search(pattern, config)
            return m.group(1).strip() if m else default

        jc   = get(r'Jc\s*=\s*(\d+)', "5")
        jmin = get(r'Jmin\s*=\s*(\d+)', "10")
        jmax = get(r'Jmax\s*=\s*(\d+)', "50")
        s1   = get(r'S1\s*=\s*(\d+)', "55")
        s2   = get(r'S2\s*=\s*(\d+)', "34")
        s3   = get(r'S3\s*=\s*(\d+)', "53")
        s4   = get(r'S4\s*=\s*(\d+)', "9")
        h1   = get(r'H1\s*=\s*(\S+)', "559719344-1124378331")
        h2   = get(r'H2\s*=\s*(\S+)', "1356339249-1458644588")
        h3   = get(r'H3\s*=\s*(\S+)', "2136624118-2143715549")
        h4   = get(r'H4\s*=\s*(\S+)', "2146343172-2146597914")
        i1   = get(r'I1\s*=\s*(.*)', "")
        # В generate_amnezia_vpn_link() замените получение PSK:

        # Ищем PSK для этого конкретного клиента
        psk = ""
        peers = config.split('[Peer]')[1:]
        for peer in peers:
            if client_public_key in peer:
                psk_match = re.search(r'PresharedKey\s*=\s*(\S+)', peer)
                if psk_match:
                    psk = psk_match.group(1)
                break

        inner_config = (
            "[Interface]\n"
            f"Address = {client_ip}\n"
            "DNS = 1.1.1.1, 1.0.0.1\n"
            f"PrivateKey = {client_private_key}\n"
            f"Jc = {jc}\n"
            f"Jmin = {jmin}\n"
            f"Jmax = {jmax}\n"
            f"S1 = {s1}\n"
            f"S2 = {s2}\n"
            f"S3 = {s3}\n"
            f"S4 = {s4}\n"
            f"H1 = {h1}\n"
            f"H2 = {h2}\n"
            f"H3 = {h3}\n"
            f"H4 = {h4}\n"
            f"I1 = {i1}\n"
            "I2 = \n"
            "I3 = \n"
            "I4 = \n"
            "I5 = \n"
            "\n"
            "[Peer]\n"
            f"PublicKey = {server_public}\n"
            f"PresharedKey = {psk}\n"
            "AllowedIPs = 0.0.0.0/0, ::/0\n"
            f"Endpoint = {server_ip}:{server_port}\n"
            "PersistentKeepalive = 25\n"
        )

        # 🔥 Жёсткий порядок ключей
        last_config = OrderedDict([
            ("H1", h1),
            ("H2", h2),
            ("H3", h3),
            ("H4", h4),
            ("I1", i1),
            ("I2", ""),
            ("I3", ""),
            ("I4", ""),
            ("I5", ""),
            ("Jc", jc),
            ("Jmax", jmax),
            ("Jmin", jmin),
            ("S1", s1),
            ("S2", s2),
            ("S3", s3),
            ("S4", s4),
            ("allowed_ips", ["0.0.0.0/0", "::/0"]),
            ("clientId", client_public_key),
            ("client_ip", client_ip.split("/")[0]),
            ("client_priv_key", client_private_key),
            ("client_pub_key", client_public_key),
            ("config", inner_config),
            ("hostName", server_ip),
            ("mtu", "1376"),
            ("persistent_keep_alive", "25"),
            ("port", int(server_port)),
            ("psk_key", psk),
            ("server_pub_key", server_public),
        ])

        last_config_str = json.dumps(
            last_config,
            indent=4,
            separators=(',', ': '),
            ensure_ascii=False
        )

        server_config = OrderedDict([
            ("containers", [
                OrderedDict([
                    ("awg", OrderedDict([
                        ("H1", h1),
                        ("H2", h2),
                        ("H3", h3),
                        ("H4", h4),
                        ("I1", i1),
                        ("I2", ""),
                        ("I3", ""),
                        ("I4", ""),
                        ("I5", ""),
                        ("Jc", jc),
                        ("Jmax", jmax),
                        ("Jmin", jmin),
                        ("S1", s1),
                        ("S2", s2),
                        ("S3", s3),
                        ("S4", s4),
                        ("last_config", last_config_str),
                        ("port", server_port),
                        ("protocol_version", "2"),
                        ("subnet_address", "10.8.1.0"),
                        ("transport_proto", "udp"),
                    ])),
                    ("container", "amnezia-awg2"),
                ])
            ]),
            ("defaultContainer", "amnezia-awg2"),
            ("description", "Amnezia VPN Server"),
            ("dns1", "1.1.1.1"),
            ("dns2", "1.0.0.1"),
            ("hostName", server_ip),
        ])

        # 🔥 ВАЖНО: indent=4 на всём JSON
        json_str = json.dumps(server_config, indent=4, ensure_ascii=False)

        # ДОБАВИТЬ ОБЯЗАТЕЛЬНО
        json_str += "\n"
        
        data = json_str.encode("utf-8")
        compressed = zlib.compress(data, 8)
        header = struct.pack(">I", len(data))
        qt = header + compressed

        b64 = base64.urlsafe_b64encode(qt).decode().rstrip("=")

        return f"vpn://{b64}"