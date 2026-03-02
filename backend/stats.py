# stats.py
from typing import Dict, List, Any
from datetime import datetime
import database as db
from logger import logger

async def get_dashboard_stats(server_statuses: List[Dict] = None) -> Dict[str, Any]:
    """Собирает всю статистику для дашборда администратора"""
    
    # Получаем все данные
    users = await db.get_all_users()
    clients = await db.get_all_clients_with_user_info()
    servers = await db.get_all_servers()
    
    # Статистика по серверам с учётом переданных статусов
    servers_stats = await _get_servers_stats(servers, server_statuses)
    
    # Статистика по клиентам
    clients_stats = _get_clients_stats(clients)
    
    # Трафик
    traffic_stats = await _get_traffic_stats(clients)
    
    # Топ клиентов
    top_clients = _get_top_clients(clients, limit=10)
    
    # Проблемы с серверами
    issues = await _get_server_issues(servers, server_statuses)
    
    # Истекающие подписки
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
    
    # Неактивные клиенты
    inactive_clients = []
    for client in clients:
        if client.get('is_active') and (not client.get('handshake') or client['handshake'] == 'Never'):
            inactive_clients.append({
                "public_key": client['public_key'],
                "name": client.get('name', 'Unknown'),
                "handshake": client.get('handshake', 'Never')
            })
    
    # Активные сейчас
    active_now = 0
    for client in clients:
        if client.get('handshake') and client['handshake'] != 'Never':
            handshake_str = client['handshake']
            try:
                if 'second' in handshake_str:
                    val = int(handshake_str.split()[0])
                    if val < 300:
                        active_now += 1
                elif 'minute' in handshake_str:
                    val = int(handshake_str.split()[0])
                    if val < 5:
                        active_now += 1
            except:
                pass
    
    # История трафика
    traffic_history = await db.get_traffic_history(days=30)
    
    return {
        "servers": servers_stats,
        "clients": clients_stats,
        "traffic": traffic_stats,
        "active_now": active_now,
        "top_clients": top_clients,
        "issues": issues,
        "expiring_soon": expiring_soon,
        "inactive_clients": inactive_clients,
        "traffic_history": traffic_history
    }

async def _get_servers_stats(servers: List[Dict], server_statuses: List = None) -> Dict:
    """Собирает статистику по серверам"""
    total = len(servers)
    online = 0
    issues = 0
    
    print(f"DEBUG: server_statuses received: {server_statuses}")
    
    # Создаём словарь статусов для быстрого доступа
    status_map = {}
    if server_statuses:
        for s in server_statuses:
            # Если s - словарь
            if isinstance(s, dict):
                status_map[s['id']] = s.get('status', {})
            # Если s - объект с атрибутами
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
            
        # Для удалённых используем переданный статус
        status = status_map.get(server_id, {})
        
        # Если status - объект, конвертируем в словарь
        if not isinstance(status, dict):
            status = status.dict() if hasattr(status, 'dict') else {}
        
        if status.get('online') and status.get('container_running'):
            online += 1
        else:
            issues += 1
    
    return {
        "total": total,
        "online": online,
        "issues": issues
    }

async def _get_server_issues(servers: List[Dict], server_statuses: List = None) -> List[Dict]:
    """Возвращает список серверов с проблемами"""
    issues = []
    
    # Создаём словарь статусов для быстрого доступа
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
        
        # Получаем статус
        if isinstance(status_info, dict):
            status = status_info.get('status', {})
        else:
            status = status_info.status if hasattr(status_info, 'status') else {}
        
        # Конвертируем статус в словарь если нужно
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
    """Собирает статистику по клиентам"""
    total = len(clients)
    active = sum(1 for c in clients if c.get('is_active'))
    blocked = total - active
    
    return {
        "total": total,
        "active": active,
        "blocked": blocked
    }

async def _get_traffic_stats(clients: List[Dict]) -> Dict:
    """Собирает статистику по трафику"""
    total = sum(c.get('traffic_used_bytes') or 0 for c in clients)
    today = await db.get_traffic_today()
    
    return {
        "total": total,
        "today": today
    }

def _get_top_clients(clients: List[Dict], limit: int = 10) -> List[Dict]:
    """Возвращает топ клиентов по трафику"""
    sorted_clients = sorted(
        clients,
        key=lambda c: c.get('traffic_used_bytes') or 0,
        reverse=True
    )
    
    return [
        {
            "public_key": c['public_key'],
            "name": c.get('name', 'Unknown'),
            "username": c.get('username', 'Unknown'),
            "traffic": c.get('traffic_used_bytes') or 0
        }
        for c in sorted_clients[:limit]
    ]