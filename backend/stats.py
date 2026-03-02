# stats.py
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import database as db
from logger import logger


async def get_dashboard_stats(server_statuses: List[Dict] = None) -> Dict[str, Any]:
    from main import get_server_public

    users = await db.get_all_users()
    clients = await db.get_all_clients_with_user_info()
    servers = await db.get_all_servers()

    traffic_data = []
    for server in servers:
        if not server.get('is_active'):
            continue
        try:
            server_instance = await get_server_public(server['id'])
            server_traffic = await server_instance.get_traffic()
            traffic_data.extend(server_traffic)
        except Exception as e:
            logger.error(f"Failed to get traffic from server {server['id']}: {e}")

    servers_stats = await _get_servers_stats(servers, server_statuses)
    clients_stats = _get_clients_stats(clients)
    traffic_stats = await _get_traffic_stats(clients)
    top_users = _get_top_users(users, limit=10)
    issues = await _get_server_issues(servers, server_statuses)

    expiring_soon = []
    now = datetime.now()
    for user in users:
        if user.get('expiry_date'):
            try:
                expiry = datetime.fromisoformat(user['expiry_date'].replace(' ', 'T'))
                days_left = (expiry - now).days
                if 0 <= days_left <= 7:
                    expiring_soon.append({
                        "id": user['id'],
                        "username": user['username'],
                        "expiry_date": user['expiry_date']
                    })
            except:
                pass

    inactive_clients = _get_inactive_clients(clients)
    active_now = _get_active_now(clients, traffic_data)
    traffic_history = await db.get_traffic_history(days=30)

    return {
        "servers": servers_stats,
        "clients": clients_stats,
        "traffic": traffic_stats,
        "active_now": active_now,
        "top_users": top_users,
        "issues": issues,
        "expiring_soon": expiring_soon,
        "inactive_clients": inactive_clients,
        "traffic_history": traffic_history
    }


async def _get_servers_stats(servers: List[Dict], server_statuses: List = None) -> Dict:
    total = len(servers)
    online = 0
    issues = 0

    status_map = {}
    if server_statuses:
        for s in server_statuses:
            if isinstance(s, dict):
                status_map[s['id']] = s.get('status', {})
            else:
                status_map[s.id] = s.status if hasattr(s, 'status') else {}

    for server in servers:
        server_id = server['id']
        if not server.get('is_active'):
            issues += 1
            continue
        if server['auth_type'] == 'local':
            online += 1
            continue
        status = status_map.get(server_id, {})
        if not isinstance(status, dict):
            status = status.dict() if hasattr(status, 'dict') else {}
        if status.get('online') and status.get('container_running'):
            online += 1
        else:
            issues += 1
    return {"total": total, "online": online, "issues": issues}


async def _get_server_issues(servers: List[Dict], server_statuses: List = None) -> List[Dict]:
    issues = []
    status_map = {}
    if server_statuses:
        for s in server_statuses:
            if isinstance(s, dict):
                status_map[s['id']] = s
            else:
                status_map[s.id] = s

    for server in servers:
        server_id = server['id']
        status_info = status_map.get(server_id, {})
        if isinstance(status_info, dict):
            status = status_info.get('status', {})
        else:
            status = status_info.status if hasattr(status_info, 'status') else {}
        if not isinstance(status, dict):
            status = status.dict() if hasattr(status, 'dict') else {}

        if not server.get('is_active'):
            issues.append({
                "server_id": server_id,
                "server": server['name'],
                "reason": "Server is disabled"
            })
            continue
        if server['auth_type'] == 'local':
            continue
        if not status.get('online'):
            issues.append({
                "server_id": server_id,
                "server": server['name'],
                "reason": "Server is offline"
            })
        elif not status.get('container_running'):
            issues.append({
                "server_id": server_id,
                "server": server['name'],
                "reason": "Container is not running"
            })
    return issues


def _get_clients_stats(clients: List[Dict]) -> Dict:
    total = len(clients)
    active = sum(1 for c in clients if c.get('is_active'))
    blocked = total - active
    return {"total": total, "active": active, "blocked": blocked}


async def _get_traffic_stats(clients: List[Dict]) -> Dict:
    total = await db.get_total_traffic_users()
    today = await db.get_traffic_today()
    return {"total": total, "today": today}


def _get_top_users(users: List[Dict], limit: int = 10) -> List[Dict]:
    sorted_users = sorted(
        users,
        key=lambda u: u.get('traffic_used_bytes') or 0,
        reverse=True
    )
    return [
        {
            "user_id": u['id'],
            "username": u['username'],
            "traffic": u.get('traffic_used_bytes') or 0
        }
        for u in sorted_users[:limit]
    ]


def _get_inactive_clients(clients: List[Dict]) -> List[Dict]:
    inactive = []
    seen_keys = set()
    now = datetime.now()
    day_ago = now - timedelta(days=1)
    for client in clients:
        if not client.get('is_active'):
            continue
        public_key = client['public_key']
        if public_key in seen_keys:
            continue
        created_at = client.get('created_at')
        if created_at:
            try:
                created = datetime.fromisoformat(created_at.replace(' ', 'T'))
                if created > day_ago:
                    continue
            except:
                pass
        handshake = client.get('handshake')
        if not handshake or handshake == 'Never':
            inactive.append({
                "public_key": public_key,
                "name": client.get('name', 'Unknown'),
                "handshake": 'Never'
            })
            seen_keys.add(public_key)
            continue
        try:
            if 'second' in handshake:
                seconds = int(handshake.split()[0])
                if seconds > 604800:
                    inactive.append({
                        "public_key": public_key,
                        "name": client.get('name', 'Unknown'),
                        "handshake": handshake
                    })
                    seen_keys.add(public_key)
            elif 'minute' in handshake:
                minutes = int(handshake.split()[0])
                if minutes > 10080:
                    inactive.append({
                        "public_key": public_key,
                        "name": client.get('name', 'Unknown'),
                        "handshake": handshake
                    })
                    seen_keys.add(public_key)
            elif 'hour' in handshake:
                hours = int(handshake.split()[0])
                if hours > 168:
                    inactive.append({
                        "public_key": public_key,
                        "name": client.get('name', 'Unknown'),
                        "handshake": handshake
                    })
                    seen_keys.add(public_key)
            elif 'day' in handshake:
                days = int(handshake.split()[0])
                if days > 7:
                    inactive.append({
                        "public_key": public_key,
                        "name": client.get('name', 'Unknown'),
                        "handshake": handshake
                    })
                    seen_keys.add(public_key)
        except:
            pass
    return inactive


def _get_active_now(clients: List[Dict], traffic_data: List[Dict] = None) -> int:
    """
    Подсчитывает количество клиентов, активных в последние 5 минут.
    :param clients: список клиентов с информацией из БД
    :param traffic_data: данные трафика с handshake (если есть)
    """
    if not traffic_data:
        return 0
    active_count = 0
    now = datetime.now()
    five_min_ago = now - timedelta(minutes=5)

    for client in clients:
        client_traffic = next((t for t in traffic_data if t.get('public_key') == client['public_key']), None)
        if not client_traffic:
            continue
        handshake_str = client_traffic.get('latest_handshake', '')
        if handshake_str == 'Never':
            continue
        handshake_time = parse_handshake(handshake_str)
        if handshake_time and handshake_time > five_min_ago:
            active_count += 1

    return active_count


def parse_handshake(handshake_str: str) -> Optional[datetime]:
    """Парсит строку вида '5 seconds ago', '2 minutes ago' и т.д. в datetime."""
    if not handshake_str or handshake_str == 'Never':
        return None
    parts = handshake_str.split()
    if len(parts) < 3:
        return None
    try:
        value = int(parts[0])
        unit = parts[1]
        now = datetime.now()
        if 'second' in unit:
            return now - timedelta(seconds=value)
        elif 'minute' in unit:
            return now - timedelta(minutes=value)
        elif 'hour' in unit:
            return now - timedelta(hours=value)
        elif 'day' in unit:
            return now - timedelta(days=value)
    except:
        return None
    return None