from typing import List, Dict, Optional, AsyncGenerator
import uuid
import re
from datetime import datetime

from connection import Connection, LocalConnection, SSHConnection
import awg_utils
from database import get_client, create_client, update_traffic_usage, get_all_clients
import database
from logger import logger


class AmneziaWGServer:
    """Основной класс для управления сервером AmneziaWG."""

    def __init__(self, conn: Connection, server_id: int = 1):
        self.conn = conn
        self.server_id = server_id
        self.container_name = "amnezia-awg2"
        logger.debug(f"AmneziaWGServer initialized for server ID {server_id}")

    async def _read_config(self) -> str:
        """Читает конфигурационный файл сервера."""
        config = await self.conn.run_command("cat /opt/amnezia/awg/awg0.conf 2>/dev/null || echo ''")
        logger.debug(f"Read config, length {len(config)}")
        return config

    async def _write_config(self, config: str) -> bool:
        """Записывает конфигурационный файл сервера."""
        filtered_lines = []
        for line in config.splitlines():
            stripped = line.strip()
            if stripped.startswith('Address') or stripped.startswith('# Address'):
                continue
            filtered_lines.append(line)
        filtered_config = '\n'.join(filtered_lines)
        success = await self.conn.write_file("/opt/amnezia/awg/awg0.conf", filtered_config)
        if success:
            logger.debug("Config written successfully")
        else:
            logger.error("Failed to write config")
        return success

    async def _syncconf(self):
        """Синхронизирует интерфейс с файлом конфигурации."""
        await self.conn.run_command("awg syncconf awg0 <(cat /opt/amnezia/awg/awg0.conf)")
        logger.debug("awg syncconf executed")

    async def get_clients(self) -> List[Dict]:
        """Возвращает список клиентов с именами (из clientsTable)."""
        config = await self._read_config()
        peers = awg_utils.parse_peers(config)

        table_json = await self.conn.run_command("cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo '[]'")
        try:
            import json
            clients_data = json.loads(table_json)
            names = {item['clientId']: item.get('userData', {}).get('clientName', 'Unknown')
                     for item in clients_data if 'clientId' in item}
        except Exception as e:
            logger.warning(f"Failed to parse clientsTable: {e}")
            names = {}

        result = []
        for i, peer in enumerate(peers, 1):
            pub_key = peer.get('public_key', '')
            name = names.get(pub_key, f"Client {i}")
            result.append({
                'name': name,
                'public_key': pub_key,
                'ip': peer.get('ip', '')
            })
        logger.debug(f"get_clients returned {len(result)} clients")
        return result

    async def add_client(self, name: str) -> Dict:
        """Добавляет нового клиента с указанным именем."""
        logger.info(f"Adding new client with name '{name}' on server {self.server_id}")
        private_key = (await self.conn.run_command("awg genkey")).strip()
        public_key = (await self.conn.run_command(f"echo '{private_key}' | awg pubkey")).strip()
        if not public_key:
            logger.error("Failed to generate public key")
            raise Exception("Failed to generate public key")
        psk = (await self.conn.run_command("wg genpsk")).strip()

        config = await self._read_config()
        next_ip = self._get_next_ip(config)

        peer_section = f"""
[Peer]
PublicKey = {public_key}
PresharedKey = {psk}
AllowedIPs = {next_ip}
"""
        new_config = config.rstrip() + "\n" + peer_section
        if not await self._write_config(new_config):
            raise Exception("Failed to write config file")
        await self._syncconf()

        await self._update_clients_table(public_key, name, next_ip)
        await create_client(public_key, name, next_ip, private_key, self.server_id)

        client_config = await self.get_client_config(public_key)
        normalized = self.normalize_config(new_config)
        if not await self._write_config(normalized):
            raise Exception("Failed to write config file")

        logger.info(f"Client {name} ({public_key[:8]}...) added with IP {next_ip}")
        return {
            "name": name,
            "ip": next_ip,
            "public_key": public_key,
            "config": client_config
        }

    async def delete_client(self, public_key: str):
        """Удаляет клиента по публичному ключу."""
        logger.info(f"Deleting client {public_key[:8]}...")
        config = await self._read_config()
        peers = awg_utils.parse_peers(config)
        new_peers = [p for p in peers if p.get('public_key') != public_key]
        if len(new_peers) == len(peers):
            logger.warning(f"Client {public_key[:8]}... not found, nothing to delete")
            return

        interface_part = config.split('[Peer]')[0]
        new_config = interface_part
        for peer in new_peers:
            peer_block = f"\n[Peer]\nPublicKey = {peer['public_key']}\n"
            if 'psk' in peer:
                peer_block += f"PresharedKey = {peer['psk']}\n"
            if 'ip' in peer:
                peer_block += f"AllowedIPs = {peer['ip']}\n"
            new_config += peer_block

        normalized = self.normalize_config(new_config)
        if not await self._write_config(normalized):
            raise Exception("Failed to write config")
        await self._syncconf()

        await self._remove_from_clients_table(public_key)
        await self.conn.run_command(f"rm -f /opt/amnezia/client_configs/{public_key}.conf")
        logger.info(f"Client {public_key[:8]}... deleted")

    def normalize_config(self, config: str) -> str:
        """Убирает множественные пустые строки, оставляя не более одной между секциями."""
        lines = config.splitlines()
        result = []
        prev_empty = False
        for line in lines:
            if line.strip() == '':
                if not prev_empty:
                    result.append('')
                    prev_empty = True
            else:
                result.append(line)
                prev_empty = False
        if result and result[-1] == '':
            result.pop()
        return '\n'.join(result)

    async def get_client_info(self, public_key: str) -> Optional[Dict]:
        """Возвращает имя и IP клиента по публичному ключу."""
        config = await self._read_config()
        peers = awg_utils.parse_peers(config)
        peer = next((p for p in peers if p.get('public_key') == public_key), None)
        if not peer:
            return None

        table_json = await self.conn.run_command("cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo '[]'")
        try:
            import json
            clients_data = json.loads(table_json)
            name = next((item['userData'].get('clientName', 'Unknown') 
                        for item in clients_data if item.get('clientId') == public_key), None)
        except:
            name = None

        return {
            'name': name or 'Unknown',
            'ip': peer.get('ip', '')
        }

    async def get_client_config(self, public_key: str) -> str:
        """Генерирует конфигурацию для указанного клиента."""
        logger.debug(f"Generating config for client {public_key[:8]}...")
        config = await self._read_config()
        if not config:
            logger.warning("Empty server config")
            return ""

        server_params = awg_utils.parse_server_config(config)
        peers = awg_utils.parse_peers(config)

        peer = next((p for p in peers if p.get('public_key') == public_key), None)
        if not peer:
            logger.warning(f"Peer {public_key[:8]}... not found in config")
            return ""

        client_ip = peer['ip']
        psk = peer.get('psk', '')

        client_data = await get_client(public_key)
        if not client_data or not client_data.get('private_key'):
            saved = await self.conn.run_command(f"cat /opt/amnezia/client_configs/{public_key}.conf 2>/dev/null || true")
            priv_match = re.search(r'PrivateKey\s*=\s*(\S+)', saved)
            if priv_match:
                private_key = priv_match.group(1)
                create_client(public_key, '', client_ip, private_key, self.server_id)
                logger.debug(f"Recovered private key from saved config for {public_key[:8]}...")
            else:
                logger.error(f"Private key not found for client {public_key[:8]}...")
                return ""
        else:
            private_key = client_data['private_key']

        server_public = (await self.conn.run_command("cat /opt/amnezia/awg/server_public.key 2>/dev/null || true")).strip()
        if not server_public and server_params.get('private_key'):
            server_public = (await self.conn.run_command(f"echo '{server_params['private_key']}' | awg pubkey")).strip()
        if not server_public:
            logger.error("Server public key not found")
            return ""

        host = await self._get_server_ip()
        port = server_params.get('listen_port', '32308')
        endpoint = f"{host}:{port}"

        return awg_utils.generate_client_config(
            client_ip=client_ip,
            client_private_key=private_key,
            server_public_key=server_public,
            server_endpoint=endpoint,
            psk=psk,
            dns="1.1.1.1, 1.0.0.1",
            **{k.lower(): v for k, v in server_params.items()}
        )

    async def get_traffic(self) -> List[Dict]:
        """Возвращает текущую статистику трафика (парсинг awg show)."""
        output = await self.conn.run_command("awg show")
        traffic = awg_utils.parse_traffic_output(output)
        logger.debug(f"get_traffic returned {len(traffic)} entries")
        return traffic

    async def get_traffic_bytes(self) -> Dict[str, Dict]:
        """Возвращает трафик в байтах для каждого клиента."""
        traffic_list = await self.get_traffic()
        result = {}
        for item in traffic_list:
            transfer = item.get('transfer', '')
            received_str, sent_str = '', ''
            if 'received' in transfer and 'sent' in transfer:
                parts = transfer.split(',')
                if len(parts) == 2:
                    received_str = parts[0].replace('received', '').strip()
                    sent_str = parts[1].replace('sent', '').strip()
            received = awg_utils.parse_bytes(received_str)
            sent = awg_utils.parse_bytes(sent_str)
            result[item['public_key']] = {
                'received': received,
                'sent': sent,
                'total': received + sent
            }
        logger.debug(f"get_traffic_bytes computed for {len(result)} clients")
        return result

    async def collect_traffic_stats(self):
        logger.debug(f"Collecting traffic stats for server {self.server_id}")
        traffic_bytes = await self.get_traffic_bytes()
        logger.debug(f"Got traffic_bytes: {traffic_bytes}")
        for pub_key, data in traffic_bytes.items():
            logger.info(f"Updating {pub_key[:8]}: recv={data['received']}, sent={data['sent']}")
            await update_traffic_usage(pub_key, data['received'], data['sent'], self)
        logger.debug("Traffic stats collected")

    async def block_client(self, public_key: str) -> bool:
        ip = await self._get_client_ip(public_key)
        if not ip:
            logger.warning(f"Cannot block {public_key[:8]}... IP not found")
            return False
        out1 = await self.conn.run_command(f"iptables -I FORWARD 1 -s {ip} -j DROP 2>&1")
        out2 = await self.conn.run_command(f"iptables -I FORWARD 1 -d {ip} -j DROP 2>&1")
        logger.info(f"Blocked client {public_key[:8]}... (IP {ip}). Output: {out1.strip()} / {out2.strip()}")
        return True

    async def unblock_client(self, public_key: str) -> bool:
        ip = await self._get_client_ip(public_key)
        if not ip:
            logger.warning(f"Cannot unblock {public_key[:8]}... IP not found")
            return False
        out1 = await self.conn.run_command(f"iptables -D FORWARD -s {ip} -j DROP 2>&1 || true")
        out2 = await self.conn.run_command(f"iptables -D FORWARD -d {ip} -j DROP 2>&1 || true")
        logger.info(f"Unblocked client {public_key[:8]}... (IP {ip}). Output: {out1.strip()} / {out2.strip()}")
        return True

    async def sync_iptables_with_db(self):
        """Синхронизирует правила iptables с состоянием is_active в БД."""
        clients = await get_all_clients(server_id=self.server_id)
        for client in clients:
            if client['is_active']:
                await self.unblock_client(client['public_key'])
            else:
                await self.block_client(client['public_key'])
        logger.info(f"iptables synchronized for server {self.server_id}")

    async def generate_amnezia_vpn_link(self, public_key: str) -> str:
        """Генерирует AmneziaVPN-ссылку для клиента."""
        logger.debug(f"Generating Amnezia link for {public_key[:8]}...")
        client_data = await get_client(public_key)
        if not client_data:
            raise Exception("Client not found")

        config = await self._read_config()
        server_params = awg_utils.parse_server_config(config)
        peers = awg_utils.parse_peers(config)
        peer = next((p for p in peers if p.get('public_key') == public_key), None)
        if not peer:
            raise Exception("Peer not found in config")

        server_public = (await self.conn.run_command("cat /opt/amnezia/awg/server_public.key 2>/dev/null || true")).strip()
        if not server_public and server_params.get('private_key'):
            server_public = (await self.conn.run_command(f"echo '{server_params['private_key']}' | awg pubkey")).strip()
        if not server_public:
            raise Exception("Server public key not found")

        host = await self._get_server_ip()
        port = server_params.get('listen_port', '32308')

        client_dict = {
            'public_key': public_key,
            'private_key': client_data['private_key'],
            'ip': client_data['ip'],
            'psk': peer.get('psk', '')
        }
        obfuscation = {k.lower(): v for k, v in server_params.items() if k.lower() in ['jc','jmin','jmax','s1','s2','s3','s4','h1','h2','h3','h4','i1']}
        link = awg_utils.generate_amnezia_vpn_link(
            server_params={'host': host, 'port': port, 'public_key': server_public},
            client=client_dict,
            obfuscation=obfuscation
        )
        logger.debug(f"Amnezia link generated for {public_key[:8]}...")
        return link
    
    async def setup_server_stream(self, sudo_password: Optional[str] = None):
        """Обёртка для вызова функции установки из отдельного модуля."""
        from server_setup import setup_server_stream as run_setup
        if isinstance(self.conn, LocalConnection):
            yield {"type": "error", "message": "Setup is only for remote servers"}
            return
        if not isinstance(self.conn, SSHConnection):
            yield {"type": "error", "message": "Invalid connection type"}
            return
        async for update in run_setup(self.conn, sudo_password):
            yield update

    # ---------- Вспомогательные методы ----------
    async def _get_server_ip(self) -> str:
        """Определяет внешний IP сервера."""
        if isinstance(self.conn, SSHConnection):
            return self.conn.host
        else:
            result = await self.conn.run_command("curl -s ifconfig.me 2>/dev/null || echo 'YOUR_SERVER_IP'")
            ip = result.strip()
            logger.debug(f"Detected server IP: {ip}")
            return ip

    async def _get_client_ip(self, public_key: str) -> Optional[str]:
        """Возвращает IP адрес клиента (без маски)."""
        config = await self._read_config()
        peers = awg_utils.parse_peers(config)
        for peer in peers:
            if peer.get('public_key') == public_key and 'ip' in peer:
                return peer['ip'].split('/')[0]
        return None

    def _get_next_ip(self, config: str) -> str:
        """Определяет следующий свободный IP в подсети."""
        used_ips = []
        peers = awg_utils.parse_peers(config)
        for peer in peers:
            if 'ip' in peer:
                used_ips.append(peer['ip'].split('/')[0])

        addr_match = re.search(r'Address\s*=\s*([\d\.]+/\d+)', config)
        if addr_match:
            base = addr_match.group(1).split('/')[0].rsplit('.', 1)[0]
        else:
            base = "10.8.1"

        for i in range(2, 255):
            candidate = f"{base}.{i}"
            if candidate not in used_ips:
                logger.debug(f"Next free IP: {candidate}/32")
                return f"{candidate}/32"
        logger.warning("No free IP found, using fallback")
        return f"{base}.254/32"

    async def _update_clients_table(self, public_key: str, name: str, ip: str):
        """Добавляет запись в clientsTable."""
        table_json = await self.conn.run_command("cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo '[]'")
        try:
            import json
            table = json.loads(table_json)
        except:
            table = []
        new_entry = {
            "clientId": public_key,
            "userData": {
                "clientName": name,
                "creationDate": datetime.now().strftime("%a %b %d %H:%M:%S %Y"),
                "allowedIps": ip
            }
        }
        table.append(new_entry)
        await self.conn.write_file("/opt/amnezia/awg/clientsTable", json.dumps(table, indent=4))
        logger.debug(f"Updated clientsTable for {public_key[:8]}...")

    async def _remove_from_clients_table(self, public_key: str):
        """Удаляет запись из clientsTable."""
        table_json = await self.conn.run_command("cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo '[]'")
        try:
            import json
            table = json.loads(table_json)
            table = [item for item in table if item.get("clientId") != public_key]
            await self.conn.write_file("/opt/amnezia/awg/clientsTable", json.dumps(table, indent=4))
            logger.debug(f"Removed {public_key[:8]}... from clientsTable")
        except Exception as e:
            logger.error(f"Failed to update clientsTable: {e}")