# awg_utils.py
import re
import json
import struct
import zlib
import base64
from typing import Dict, List, Optional


def parse_server_config(config_text: str) -> Dict[str, str]:
    """Извлекает общие параметры сервера из awg0.conf."""
    params = {}
    patterns = {
        'private_key': r'PrivateKey\s*=\s*(\S+)',
        'listen_port': r'ListenPort\s*=\s*(\d+)',
        'jc': r'Jc\s*=\s*(\d+)',
        'jmin': r'Jmin\s*=\s*(\d+)',
        'jmax': r'Jmax\s*=\s*(\d+)',
        's1': r'S1\s*=\s*(\d+)',
        's2': r'S2\s*=\s*(\d+)',
        's3': r'S3\s*=\s*(\d+)',
        's4': r'S4\s*=\s*(\d+)',
        'h1': r'H1\s*=\s*(\S+)',
        'h2': r'H2\s*=\s*(\S+)',
        'h3': r'H3\s*=\s*(\S+)',
        'h4': r'H4\s*=\s*(\S+)',
        'i1': r'I1\s*=\s*(.*)',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, config_text)
        params[key] = match.group(1).strip() if match else ''
    return params


def parse_peers(config_text: str) -> List[Dict[str, str]]:
    """Возвращает список пиров с их параметрами."""
    peers = []
    peer_blocks = config_text.split('[Peer]')[1:]
    for block in peer_blocks:
        peer = {}
        pk_match = re.search(r'PublicKey\s*=\s*(\S+)', block)
        if pk_match:
            peer['public_key'] = pk_match.group(1)
        ip_match = re.search(r'AllowedIPs\s*=\s*([\d\.]+/\d+)', block)
        if ip_match:
            peer['ip'] = ip_match.group(1)
        psk_match = re.search(r'PresharedKey\s*=\s*(\S+)', block)
        if psk_match:
            peer['psk'] = psk_match.group(1)
        if peer:
            peers.append(peer)
    return peers


def parse_traffic_output(output: str) -> List[Dict[str, str]]:
    """Парсит вывод команды 'awg show' в список словарей с ключами и статистикой."""
    traffic = []
    lines = output.split('\n')
    current_peer = None
    for line in lines:
        line = line.strip()
        if line.startswith('peer:'):
            current_peer = line.split('peer:')[1].strip()
            traffic.append({
                'public_key': current_peer,
                'transfer': '',
                'latest_handshake': 'Never'
            })
        elif 'transfer:' in line and current_peer:
            traffic[-1]['transfer'] = line.split('transfer:')[1].strip()
        elif 'latest handshake:' in line and current_peer:
            traffic[-1]['latest_handshake'] = line.split('latest handshake:')[1].strip()
    return traffic


def parse_bytes(size_str: str) -> int:
    """Конвертирует строку вида '1.23 GiB' в байты."""
    size_str = size_str.strip()
    try:
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


def generate_client_config(
    client_ip: str,
    client_private_key: str,
    server_public_key: str,
    server_endpoint: str,
    psk: str = '',
    dns: str = '172.17.0.1, 1.1.1.1',
    **obfuscation_params
) -> str:
    """Генерирует конфигурацию клиента в формате AmneziaWG."""
    lines = [
        '[Interface]',
        f'Address = {client_ip}',
        f'DNS = {dns}',
        f'PrivateKey = {client_private_key}',
    ]
    for key in ['Jc', 'Jmin', 'Jmax', 'S1', 'S2', 'S3', 'S4', 'H1', 'H2', 'H3', 'H4']:
        val = obfuscation_params.get(key.lower())
        if val:
            lines.append(f'{key} = {val}')
    if obfuscation_params.get('i1'):
        lines.append(f'I1 = {obfuscation_params["i1"]}')
    lines.append('')
    lines.extend([
        '[Peer]',
        f'PublicKey = {server_public_key}',
    ])
    if psk:
        lines.append(f'PresharedKey = {psk}')
    lines.extend([
        'AllowedIPs = 0.0.0.0/0, ::/0',
        f'Endpoint = {server_endpoint}',
        'PersistentKeepalive = 25',
    ])
    return '\n'.join(lines)


def generate_amnezia_vpn_link(
    server_params: Dict[str, str],
    client: Dict[str, str],
    obfuscation: Dict[str, str],
    server_name: str = ""
) -> str:
    """Генерирует ссылку вида vpn://... для AmneziaVPN."""
    inner_config = generate_client_config(
        client_ip=client['ip'],
        client_private_key=client['private_key'],
        server_public_key=server_params['public_key'],
        server_endpoint=f"{server_params['host']}:{server_params['port']}",
        psk=client.get('psk', ''),
        **obfuscation
    )

    last_config = {
        "H1": obfuscation.get('h1', ''),
        "H2": obfuscation.get('h2', ''),
        "H3": obfuscation.get('h3', ''),
        "H4": obfuscation.get('h4', ''),
        "I1": obfuscation.get('i1', ''),
        "I2": "",
        "I3": "",
        "I4": "",
        "I5": "",
        "Jc": obfuscation.get('jc', '5'),
        "Jmax": obfuscation.get('jmax', '50'),
        "Jmin": obfuscation.get('jmin', '10'),
        "S1": obfuscation.get('s1', '95'),
        "S2": obfuscation.get('s2', '21'),
        "S3": obfuscation.get('s3', '6'),
        "S4": obfuscation.get('s4', '10'),
        "allowed_ips": ["0.0.0.0/0", "::/0"],
        "clientId": client['public_key'],
        "client_ip": client['ip'].split('/')[0],
        "client_priv_key": client['private_key'],
        "client_pub_key": client['public_key'],
        "config": inner_config,
        "hostName": server_params['host'],
        "mtu": "1376",
        "persistent_keep_alive": "25",
        "port": int(server_params['port']),
        "psk_key": client.get('psk', ''),
        "server_pub_key": server_params['public_key'],
    }

    last_config_str = json.dumps(last_config, indent=4, separators=(',', ': '), ensure_ascii=False)

    if server_name:
        description = f"{server_name}"
    else:
        description = "Amnezia VPN Server"

    server_config = {
        "containers": [
            {
                "awg": {
                    "H1": obfuscation.get('h1', ''),
                    "H2": obfuscation.get('h2', ''),
                    "H3": obfuscation.get('h3', ''),
                    "H4": obfuscation.get('h4', ''),
                    "I1": obfuscation.get('i1', ''),
                    "I2": "",
                    "I3": "",
                    "I4": "",
                    "I5": "",
                    "Jc": obfuscation.get('jc', '5'),
                    "Jmax": obfuscation.get('jmax', '50'),
                    "Jmin": obfuscation.get('jmin', '10'),
                    "S1": obfuscation.get('s1', '95'),
                    "S2": obfuscation.get('s2', '21'),
                    "S3": obfuscation.get('s3', '6'),
                    "S4": obfuscation.get('s4', '10'),
                    "last_config": last_config_str,
                    "port": server_params['port'],
                    "protocol_version": "2",
                    "subnet_address": "10.8.1.0",
                    "transport_proto": "udp",
                },
                "container": "amnezia-awg2",
            }
        ],
        "defaultContainer": "amnezia-awg2",
        "description": description,
        "dns1": "172.17.0.1",
        "dns2": "1.1.1.1",
        "hostName": server_params['host'],
    }

    json_str = json.dumps(server_config, indent=4, ensure_ascii=False) + "\n"
    data = json_str.encode("utf-8")
    compressed = zlib.compress(data, 8)
    header = struct.pack(">I", len(data))
    qt = header + compressed
    b64 = base64.urlsafe_b64encode(qt).decode().rstrip("=")
    return f"vpn://{b64}"


def normalize_config(config: str) -> str:
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