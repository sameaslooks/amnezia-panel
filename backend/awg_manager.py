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
    
    def _get_server_params(self, config: str) -> Dict:
        """Извлекает параметры сервера из конфига"""
        params = {
            "public_key": "",
            "endpoint": self._get_server_ip(),
            "port": "32308",
            "jc": "5",
            "jmin": "50",
            "jmax": "1000",
            "dns": "1.1.1.1, 1.0.0.1"
        }
        
        # Публичный ключ сервера
        priv_match = re.search(r'PrivateKey\s*=\s*(\S+)', config)
        if priv_match:
            private_key = priv_match.group(1)
            pubkey = self._exec_in_container(f"echo '{private_key}' | awg pubkey").strip()
            params["public_key"] = pubkey
        
        # Параметры обфускации
        for param in ['Jc', 'Jmin', 'Jmax']:
            match = re.search(rf'{param}\s*=\s*(\d+)', config)
            if match:
                params[param.lower()] = match.group(1)
        
        return params
    
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
        
        # Получаем текущий конфиг
        config = self._exec_in_container("cat /opt/amnezia/awg/awg0.conf")
        
        # Определяем следующий IP
        next_ip = self._get_next_ip(config)
        
        # Добавляем пира через awg set
        add_command = f"awg set awg0 peer {public_key} allowed-ips {next_ip}"
        self._exec_in_container(add_command)
        
        # Сохраняем конфиг
        self._exec_in_container("awg showconf awg0 > /opt/amnezia/awg/awg0.conf")
        
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
        server_params = self._get_server_params(config)
        
        # Формируем конфиг клиента
        client_config = f"""[Interface]
PrivateKey = {private_key}
Address = {next_ip}
DNS = {server_params['dns']}
Jc = {server_params['jc']}
Jmin = {server_params['jmin']}
Jmax = {server_params['jmax']}

[Peer]
PublicKey = {server_params['public_key']}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = {server_params['endpoint']}:{server_params['port']}
PersistentKeepalive = 25
"""
        from database import create_client
        create_client(public_key, name, next_ip)
        return {
            "name": name,
            "ip": next_ip,
            "public_key": public_key,
            "config": client_config
        }
    
    def delete_client(self, public_key: str):
        """Удаляет клиента"""
        # Удаляем пир через awg set
        self._exec_in_container(f"awg set awg0 peer {public_key} remove")
        
        # Сохраняем конфиг
        self._exec_in_container("awg showconf awg0 > /opt/amnezia/awg/awg0.conf")
        
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
        for peer in peers:
            if public_key in peer:
                ip_match = re.search(r'AllowedIPs\s*=\s*([\d\.]+/\d+)', peer)
                if ip_match:
                    client_ip = ip_match.group(1)
                    break
        
        if not client_ip:
            return ""
        
        # Параметры обфускации
        jc = re.search(r'Jc\s*=\s*(\d+)', config)
        jmin = re.search(r'Jmin\s*=\s*(\d+)', config)
        jmax = re.search(r'Jmax\s*=\s*(\d+)', config)
        
        server_ip = self._get_server_ip()
        
        return f"""[Interface]
    # PrivateKey will need to be provided by the client
    Address = {client_ip}
    DNS = 1.1.1.1, 1.0.0.1
    Jc = {jc.group(1) if jc else 5}
    Jmin = {jmin.group(1) if jmin else 50}
    Jmax = {jmax.group(1) if jmax else 1000}

    [Peer]
    PublicKey = {server_public}
    AllowedIPs = 0.0.0.0/0, ::/0
    Endpoint = {server_ip}:32308
    PersistentKeepalive = 25
    """
    
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
    def parse_bytes(self, size_str: str) -> int:
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

    def collect_traffic_stats(self):
        """Собирает статистику трафика и обновляет БД"""
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
                    parts = transfer.split(',')
                    received_str = parts[0].replace('received', '').strip()
                    sent_str = parts[1].replace('sent', '').strip()
                    
                    received = self.parse_bytes(received_str)
                    sent = self.parse_bytes(sent_str)
                    
                    # Передаём self для возможности блокировки
                    from database import update_traffic_usage
                    update_traffic_usage(current_peer, received, sent, self)
                except Exception as e:
                    print(f"Error parsing traffic: {e}")
                    continue
                    
    def collect_traffic_stats(self):
        """Собирает статистику трафика и обновляет БД"""
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
                    # Парсим "1.23 GiB received, 4.56 GiB sent"
                    parts = transfer.split(',')
                    received_str = parts[0].replace('received', '').strip()
                    sent_str = parts[1].replace('sent', '').strip()
                    
                    received = self.parse_bytes(received_str)
                    sent = self.parse_bytes(sent_str)
                    
                    # Обновляем в БД
                    from database import update_traffic_usage
                    update_traffic_usage(current_peer, received, sent, self)
                except Exception as e:
                    print(f"Error parsing traffic: {e}")
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
        from database import get_all_clients
        clients = get_all_clients()
        
        for client in clients:
            if not client['is_active']:
                self.block_client(client['public_key'])
            else:
                self.unblock_client(client['public_key'])