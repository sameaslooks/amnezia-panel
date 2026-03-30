# awg_manager.py
from typing import List, Dict, Optional
import re
from datetime import datetime
import asyncio

from connection import Connection, LocalConnection, SSHConnection
import awg_utils
import database
from logger import logger
_stats_collect_lock = asyncio.Lock()

class AmneziaWGServer:
    """Основной класс для управления сервером AmneziaWG."""

    def __init__(self, conn: Connection, server_id: int = 1):
        self.conn = conn
        self.server_id = server_id
        self.container_name = "amnezia-awg2"
        logger.debug(f"AmneziaWGServer initialized for server ID {server_id}")

    async def _read_config(self) -> str:
        config = await self.conn.run_command("cat /opt/amnezia/awg/awg0.conf 2>/dev/null || echo ''")
        logger.debug(f"Read config, length {len(config)}")
        return config

    async def _write_config(self, config: str) -> bool:
        filtered_lines = []
        for line in config.splitlines():
            stripped = line.strip()
            if stripped.startswith('Address') or stripped.startswith('# Address'):
                continue
            filtered_lines.append(line)
        filtered_config = '\n'.join(filtered_lines)
        if filtered_config and not filtered_config.endswith('\n'):
            filtered_config += '\n'
        success = await self.conn.write_file("/opt/amnezia/awg/awg0.conf", filtered_config)
        if success:
            logger.debug("Config written successfully")
        else:
            logger.error("Failed to write config")
        return success

    async def update_config(self, new_config: str) -> bool:
        """Обновляет конфигурационный файл сервера и синхронизирует его."""
        if not await self._write_config(new_config):
            return False
        await self._syncconf()
        return True

    async def _syncconf(self):
        await self.conn.run_command("awg syncconf awg0 <(cat /opt/amnezia/awg/awg0.conf)")
        logger.debug("awg syncconf executed")

    async def get_clients(self, user_id: Optional[int] = None) -> List[Dict]:
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
            client_info = await database.get_client_by_public_key(pub_key)
            if user_id is not None and (not client_info or client_info['user_id'] != user_id):
                continue
            if client_info and client_info.get('name'):
                name = client_info['name']
            else:
                name = names.get(pub_key, f"Client {i}")
            ip = peer.get('ip', '')
            client_data = {
                'id': client_info['id'] if client_info else None,
                'name': name,
                'public_key': pub_key,
                'ip': ip,
                'server_id': self.server_id,
                'server_name': await self._get_server_name(),
                'user_id': client_info['user_id'] if client_info else None,
                'is_active': client_info['is_active'] if client_info else True,
            }
            result.append(client_data)
            if not client_info:
                private_key = ""
                safe_name = pub_key.replace('/', '_')
                saved = await self.conn.run_command(f"cat /opt/amnezia/client_configs/{safe_name}.conf 2>/dev/null || true")
                priv_match = re.search(r'PrivateKey\s*=\s*(\S+)', saved)
                if priv_match:
                    private_key = priv_match.group(1)
                    logger.debug(f"Recovered private key from file for {pub_key[:8]}...")
                await database.create_client_for_user(
                    user_id=1,
                    public_key=pub_key,
                    name=name,
                    ip=ip,
                    private_key=private_key,
                    server_id=self.server_id
                )
        logger.debug(f"get_clients returned {len(result)} clients (filtered by user_id={user_id})")
        return result

    async def _get_server_name(self) -> str:
        server_data = await database.get_server(self.server_id)
        return server_data['name'] if server_data else f"Server {self.server_id}"

    async def add_client(self, name: str, user_id: int) -> Dict:
        logger.info(f"Adding new client with name '{name}' for user {user_id} on server {self.server_id}")
        
        if not await database.can_create_config(user_id):
            raise ValueError("Config limit reached for this user")
        
        limits_ok, _ = await database.check_user_limits(user_id)
        initial_active = limits_ok

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
        normalized = awg_utils.normalize_config(new_config)

        if not await self._write_config(normalized):
            raise Exception("Failed to write config file")
        
        await self._syncconf()

        client_id = await database.create_client_for_user(
            user_id=user_id,
            public_key=public_key,
            name=name,
            ip=next_ip,
            private_key=private_key,
            server_id=self.server_id
        )

        if initial_active:
            ip = next_ip.split('/')[0]  # убираем /32
            await self._add_route(ip)
            logger.info(f"Added route for new client {public_key[:8]}... (IP {ip})")
        else:
            logger.info(f"Client {name} created but deactivated due to user limits")

        client_config = await self.get_client_config(public_key)
        if client_config:
            safe_name = public_key.replace('/', '_')
            await self.conn.run_command("mkdir -p /opt/amnezia/client_configs", in_container=True)
            await self.conn.write_file(f"/opt/amnezia/client_configs/{safe_name}.conf", client_config)

        logger.info(f"Client {name} ({public_key[:8]}...) added with IP {next_ip}, active={initial_active}")
        return {
            "name": name,
            "ip": next_ip,
            "public_key": public_key,
            "config": client_config,
            "active": initial_active
        }

    async def delete_client(self, public_key: str):
        logger.info(f"Deleting client {public_key[:8]}...")
        
        ip = await self._get_client_ip(public_key)
        if ip:
            logger.debug(f"Found IP {ip} for client {public_key[:8]}...")
        else:
            logger.warning(f"Could not find IP for client {public_key[:8]}...")
        
        client = await database.get_client_by_public_key(public_key)
        if client:
            client_id = client['id']
            await database.soft_delete_client(client_id)
        else:
            logger.warning(f"Client {public_key[:8]} not found in DB, will remove only from config and clientsTable")
        
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
        
        normalized = awg_utils.normalize_config(new_config)
        if not await self._write_config(normalized):
            raise Exception("Failed to write config")
        
        await self._syncconf()
        
        if ip:
            await self._del_route(ip)
            logger.info(f"Removed route for deleted client {public_key[:8]}... (IP {ip})")
        
        await self._remove_from_clients_table(public_key)
        await self.conn.run_command(f"rm -f /opt/amnezia/client_configs/{public_key}.conf")
        logger.info(f"Client {public_key[:8]}... deleted")

    async def get_client_info(self, public_key: str) -> Optional[Dict]:
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

        client_data = await database.get_client_by_public_key(public_key)
        if not client_data or not client_data.get('private_key'):
            safe_name = public_key.replace('/', '_')
            saved = await self.conn.run_command(f"cat /opt/amnezia/client_configs/{safe_name}.conf 2>/dev/null || true")
            priv_match = re.search(r'PrivateKey\s*=\s*(\S+)', saved)
            if priv_match:
                private_key = priv_match.group(1)
                if client_data:
                    await database.update_client_private_key(client_data['id'], private_key)
                else:
                    await database.create_client_for_user(
                        user_id=1,
                        public_key=public_key,
                        name='',
                        ip=client_ip,
                        private_key=private_key,
                        server_id=self.server_id
                    )
                logger.debug(f"Recovered private key from file for {public_key[:8]}...")
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
        output = await self.conn.run_command("awg show")
        traffic = awg_utils.parse_traffic_output(output)
        logger.debug(f"get_traffic returned {len(traffic)} entries")
        return traffic

    async def get_traffic_bytes(self) -> Dict[str, Dict]:
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
        async with _stats_collect_lock:
            stats = await self.get_traffic_bytes()
            if not stats:
                return
            for pub_key, data in stats.items():
                try:
                    received = data["received"]
                    sent = data["sent"]
                    await database.update_traffic(pub_key, received, sent, self)
                except Exception:
                    logger.exception(
                        f"Failed to collect traffic for {pub_key[:8]} on server {self.server_id}"
                    )

    async def block_client(self, public_key: str) -> bool:
        ip = await self._get_client_ip(public_key)
        if not ip:
            logger.warning(f"Cannot block {public_key[:8]}... IP not found")
            return False
        await self._del_route(ip)
        logger.info(f"Blocked client {public_key[:8]}... (IP {ip}) – route removed")
        return True

    async def unblock_client(self, public_key: str) -> bool:
        ip = await self._get_client_ip(public_key)
        if not ip:
            logger.warning(f"Cannot unblock {public_key[:8]}... IP not found")
            return False
        await self._add_route(ip)
        logger.info(f"Unblocked client {public_key[:8]}... (IP {ip}) – route added")
        return True
        
    async def sync_routes_with_db(self):
        """Синхронизирует маршруты с полем is_active клиентов в БД для текущего сервера."""
        clients = await database.get_server_clients(self.server_id)
        for client in clients:
            if client['is_active']:
                await self.unblock_client(client['public_key'])
            else:
                await self.block_client(client['public_key'])
        logger.info(f"Routes synchronized for server {self.server_id}")

    async def generate_amnezia_vpn_link(self, public_key: str) -> str:
        logger.debug(f"Generating Amnezia link for {public_key[:8]}...")
        client_data = await database.get_client_by_public_key(public_key)
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

        server_info = await database.get_server(self.server_id)
        server_name = server_info.get('name', '') if server_info else ''

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
            obfuscation=obfuscation,
            server_name=server_name
        )
        logger.debug(f"Amnezia link generated for {public_key[:8]}...")
        return link

    async def setup_server_stream(self, sudo_password: Optional[str] = None):
        from server_setup import setup_server_stream as run_setup
        if isinstance(self.conn, LocalConnection):
            yield {"type": "error", "message": "Setup is only for remote servers"}
            return
        if not isinstance(self.conn, SSHConnection):
            yield {"type": "error", "message": "Invalid connection type"}
            return
        async for update in run_setup(self.conn, sudo_password):
            yield update

    async def _get_server_ip(self) -> str:
        if isinstance(self.conn, SSHConnection):
            return self.conn.host
        else:
            # Для локального подключения выполняем curl на хосте, а не в контейнере
            result = await self.conn.run_command("curl -s ifconfig.me", in_container=False)
            ip = result.strip()
            if ip:
                logger.debug(f"Detected server IP: {ip}")
                return ip
            
            # Если не сработало, пробуем альтернативный сервис
            result = await self.conn.run_command("curl -s icanhazip.com", in_container=False)
            ip = result.strip()
            if ip:
                return ip
                
            # Если всё ещё нет IP, возвращаем fallback, но логируем ошибку
            logger.error("Could not detect server IP using curl")
            return "YOUR_SERVER_IP"

    async def _get_client_ip(self, public_key: str) -> Optional[str]:
        config = await self._read_config()
        peers = awg_utils.parse_peers(config)
        for peer in peers:
            if peer.get('public_key') == public_key and 'ip' in peer:
                return peer['ip'].split('/')[0]
        return None

    def _get_next_ip(self, config: str) -> str:
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
        table_json = await self.conn.run_command("cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo '[]'")
        try:
            import json
            table = json.loads(table_json)
            table = [item for item in table if item.get("clientId") != public_key]
            await self.conn.write_file("/opt/amnezia/awg/clientsTable", json.dumps(table, indent=4))
            logger.debug(f"Removed {public_key[:8]}... from clientsTable")
        except Exception as e:
            logger.error(f"Failed to update clientsTable: {e}")

    async def get_full_status(self) -> Dict:
        status = {
            "online": False,
            "container_running": False,
            "version": None,
            "clients_count": 0,
            "errors": []
        }
        try:
            await self.conn.run_command("echo 'ping'", in_container=False)
            status["online"] = True
            container_check = await self.conn.run_command(
                "docker ps --filter name=amnezia-awg2 --format '{{.Status}}'",
                in_container=False
            )
            if 'Up' in container_check:
                status["container_running"] = True
                version = await self.conn.run_command(
                    "docker exec amnezia-awg2 awg version 2>/dev/null || echo 'unknown'",
                    in_container=False
                )
                status["version"] = version.strip()
                try:
                    clients = await self.get_clients()
                    status["clients_count"] = len(clients)
                except Exception as e:
                    status["errors"].append(f"Failed to get clients: {str(e)}")
        except Exception as e:
            status["errors"].append(str(e))
            logger.error(f"Failed to get full status for server {self.server_id}: {e}")
        return status

    async def stop_container(self) -> bool:
        try:
            await self.conn.run_command("docker stop amnezia-awg2 2>/dev/null || true", in_container=False)
            logger.info(f"Container stopped on server {self.server_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to stop container: {e}")
            return False

    async def start_container(self) -> bool:
        try:
            await self.conn.run_command("docker start amnezia-awg2 2>/dev/null || true", in_container=False)
            await asyncio.sleep(2)
            await self.sync_routes_with_db()
            logger.info(f"Container started on server {self.server_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to start container: {e}")
            return False

    async def restart_container(self) -> bool:
        try:
            await self.stop_container()
            await self.start_container()
            return True
        except Exception as e:
            logger.error(f"Failed to restart container: {e}")
            return False
        
    async def _add_route(self, ip: str):
        """Добавляет маршрут до клиента через интерфейс awg0 (в контейнере)."""
        cmd = f"ip route add {ip}/32 dev awg0"
        await self.conn.run_command(cmd)

    async def _del_route(self, ip: str):
        """Удаляет маршрут до клиента (если существует)."""
        cmd = f"ip route del {ip}/32 dev awg0 2>/dev/null || true"
        await self.conn.run_command(cmd)