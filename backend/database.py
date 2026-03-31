# database.py
import asyncpg # type: ignore
import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import bcrypt
from logger import logger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from awg_manager import AmneziaWGServer

# Глобальный пул соединений
_pool: Optional[asyncpg.Pool] = None


async def init_pool(dsn: str) -> None:
    """Инициализирует пул соединений с PostgreSQL."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        logger.info("Database pool created")
    else:
        logger.warning("Database pool already exists")


async def get_pool() -> asyncpg.Pool:
    """Возвращает глобальный пул (должен быть уже инициализирован)."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool first.")
    return _pool


async def init_db():
    """Создаёт таблицы, если их нет."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Таблица пользователей
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                traffic_limit_bytes BIGINT,
                traffic_used_bytes BIGINT DEFAULT 0,
                expiry_date TIMESTAMP,
                config_limit INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_disabled BOOLEAN DEFAULT FALSE
            )
        ''')

        # Таблица клиентов
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                public_key TEXT UNIQUE NOT NULL,
                name TEXT,
                ip TEXT,
                private_key TEXT,
                last_received BIGINT DEFAULT 0,
                last_sent BIGINT DEFAULT 0,
                server_id INTEGER DEFAULT 1,
                server_name TEXT DEFAULT 'local',
                is_active BOOLEAN DEFAULT TRUE,
                is_deleted BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_ip TEXT
            )
        ''')

        # Таблица истории трафика
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS traffic_history (
                id SERIAL PRIMARY KEY,
                client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                bytes_received BIGINT,
                bytes_sent BIGINT,
                total_bytes BIGINT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица истории IP клиентов
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS client_ip_history (
                id SERIAL PRIMARY KEY,
                client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                ip TEXT NOT NULL,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                count INTEGER DEFAULT 1,
                UNIQUE(client_id, ip)
            )
        ''')

        # Таблица серверов
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS servers (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                host TEXT,
                port INTEGER DEFAULT 22,
                username TEXT,
                auth_type TEXT DEFAULT 'password',
                password TEXT,
                private_key TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Добавляем колонку last_ip, если её нет (в PostgreSQL проверка через информацию о схеме)
        # Для упрощения просто выполняем ALTER, но игнорируем ошибку, если колонка уже есть.
        try:
            await conn.execute('ALTER TABLE clients ADD COLUMN IF NOT EXISTS last_ip TEXT')
        except Exception as e:
            logger.warning(f"Adding last_ip column: {e}")

        try:
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS is_disabled BOOLEAN DEFAULT FALSE')
        except Exception as e:
            logger.warning(f"Adding is_disabled column: {e}")

        # Добавляем сервер по умолчанию (локальный), если нет
        row = await conn.fetchval("SELECT COUNT(*) FROM servers")
        if row == 0:
            await conn.execute('''
                INSERT INTO servers (name, host, username, auth_type, is_active)
                VALUES ($1, $2, $3, $4, $5)
            ''', 'local', 'localhost', 'local', 'local', False)
            logger.info("Added default local server")

        # Пользователь admin/admin
        row = await conn.fetchval("SELECT COUNT(*) FROM users")
        if row == 0:
            password_hash = bcrypt.hashpw(b'admin', bcrypt.gensalt()).decode()
            await conn.execute('''
                INSERT INTO users (username, password_hash, role)
                VALUES ($1, $2, $3)
            ''', 'admin', password_hash, 'admin')
            logger.info("Created default admin user")


# ---------- Пользователи ----------
async def get_user_by_username(username: str) -> Optional[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT id, username, password_hash, role, traffic_limit_bytes,
                   traffic_used_bytes, expiry_date, config_limit, created_at, is_disabled
            FROM users WHERE username = $1
        ''', username)
        if row:
            return dict(row)
        return None


async def create_user(username: str, password: str, role: str = 'user') -> bool:
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute('''
                INSERT INTO users (username, password_hash, role)
                VALUES ($1, $2, $3)
            ''', username, password_hash, role)
            logger.info(f"Created user {username} with role {role}")
            return True
        except asyncpg.UniqueViolationError:
            logger.warning(f"Attempt to create duplicate user {username}")
            return False


async def get_all_users() -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id, username, role, traffic_limit_bytes, traffic_used_bytes,
                   expiry_date, config_limit, created_at, is_disabled
            FROM users ORDER BY created_at DESC
        ''')
        return [dict(row) for row in rows]


async def update_user(user_id: int, username: str = None, password: str = None,
                      role: str = None, config_limit: int = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if username:
            await conn.execute('UPDATE users SET username = $1 WHERE id = $2', username, user_id)
        if password:
            password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            await conn.execute('UPDATE users SET password_hash = $1 WHERE id = $2', password_hash, user_id)
        if role:
            await conn.execute('UPDATE users SET role = $1 WHERE id = $2', role, user_id)
        if config_limit is not None:
            await conn.execute('UPDATE users SET config_limit = $1 WHERE id = $2', config_limit, user_id)
        await conn.execute('UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = $1', user_id)
        logger.info(f"Updated user {user_id}")


async def set_user_disabled(user_id: int, disabled: bool):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE users SET is_disabled = $1 WHERE id = $2', disabled, user_id)


async def delete_user(user_id: int, server_instances: dict = None):
    """Удаляет пользователя и всех его клиентов."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем список клиентов пользователя
        clients = await conn.fetch('SELECT id, public_key, server_id FROM clients WHERE user_id = $1', user_id)
        # Удаляем клиентов с серверов, если предоставлены экземпляры
        if server_instances:
            for client_id, public_key, server_id in clients:
                server = server_instances.get(server_id)
                if server:
                    try:
                        await server.delete_client(public_key)
                        logger.info(f"Клиент {public_key[:8]}... удалён с сервера {server_id}")
                    except Exception as e:
                        logger.error(f"Ошибка удаления клиента {public_key[:8]}... с сервера: {e}")
        # Удаляем историю трафика клиентов
        for client_id, _, _ in clients:
            await conn.execute('DELETE FROM traffic_history WHERE client_id = $1', client_id)
        # Удаляем клиентов
        await conn.execute('DELETE FROM clients WHERE user_id = $1', user_id)
        # Удаляем пользователя
        await conn.execute('DELETE FROM users WHERE id = $1', user_id)
        logger.info(f"Удалён пользователь {user_id} и {len(clients)} его клиентов")


async def get_user_by_id(user_id: int) -> Optional[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT id, username, role, traffic_limit_bytes, traffic_used_bytes,
                expiry_date, config_limit, created_at, is_disabled
            FROM users WHERE id = $1
        ''', user_id)
        if row:
            logger.debug(f"Found user by ID {user_id}")
            return dict(row)
        logger.debug(f"User ID {user_id} not found")
        return None


async def update_user_traffic_used(user_id: int, bytes_used: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            UPDATE users SET traffic_used_bytes = traffic_used_bytes + $1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $2
        ''', bytes_used, user_id)
        logger.debug(f"Updated traffic for user {user_id}: +{bytes_used} bytes")


async def check_user_limits(user_id: int) -> tuple[bool, str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT traffic_limit_bytes, traffic_used_bytes, expiry_date
            FROM users WHERE id = $1
        ''', user_id)
        if not row:
            return False, "User not found"
        limit_bytes, used_bytes, expiry_date = row
        if limit_bytes and used_bytes > limit_bytes:
            return False, "Traffic limit exceeded"
        if expiry_date:
            # PostgreSQL возвращает datetime объект
            if datetime.now() > expiry_date:
                return False, "Subscription expired"
        return True, "OK"


async def can_create_config(user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT role, config_limit FROM users WHERE id = $1', user_id)
        if not row:
            return False
        role, config_limit = row
        if role == 'admin':
            return True
        count = await conn.fetchval('''
            SELECT COUNT(*) FROM clients
            WHERE user_id = $1 AND is_deleted = FALSE
        ''', user_id)
        return count < config_limit


async def get_user_traffic_stats(user_id: int, days: int = 30) -> Dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT id FROM clients WHERE user_id = $1', user_id)
        client_ids = [r['id'] for r in rows]
        if not client_ids:
            return {"total_received": 0, "total_sent": 0, "total": 0, "by_client": []}
        # Суммарный трафик за период
        total_recv = 0
        total_sent = 0
        detailed = []
        for cid in client_ids:
            # Получаем имя клиента
            name_row = await conn.fetchrow('SELECT name FROM clients WHERE id = $1', cid)
            name = name_row['name'] if name_row else 'Unknown'
            # Суммируем трафик для клиента за последние days дней
            row = await conn.fetchrow('''
                SELECT COALESCE(SUM(bytes_received), 0), COALESCE(SUM(bytes_sent), 0)
                FROM traffic_history
                WHERE client_id = $1 AND recorded_at >= NOW() - $2::INTERVAL
            ''', cid, f'{days} days')
            recv, sent = row[0], row[1]
            total_recv += recv
            total_sent += sent
            detailed.append({
                'client_id': cid,
                'name': name,
                'received': recv,
                'sent': sent,
                'total': recv + sent
            })
        return {
            'total_received': total_recv,
            'total_sent': total_sent,
            'total': total_recv + total_sent,
            'by_client': detailed
        }


# ---------- Клиенты ----------
async def update_traffic(public_key: str, received: int, sent: int, endpoint: Optional[str] = None,
                         server_instance=None):
    client = await get_client_by_public_key(public_key)
    if not client:
        return

    client_id = client['id']
    user_id = client['user_id']

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем последние значения
        row = await conn.fetchrow('SELECT last_received, last_sent, last_ip FROM clients WHERE id = $1', client_id)
        last_received = row['last_received'] if row else 0
        last_sent = row['last_sent'] if row else 0
        last_ip = row['last_ip'] if row else None

        if received < last_received or sent < last_sent:
            await conn.execute('UPDATE clients SET last_received = $1, last_sent = $2 WHERE id = $3',
                               received, sent, client_id)
            return

        delta_received = received - last_received
        delta_sent = sent - last_sent

        if delta_received > 0 or delta_sent > 0:
            await conn.execute('''
                INSERT INTO traffic_history (client_id, bytes_received, bytes_sent, total_bytes)
                VALUES ($1, $2, $3, $4)
            ''', client_id, delta_received, delta_sent, delta_received + delta_sent)
            await conn.execute('''
                UPDATE users SET traffic_used_bytes = traffic_used_bytes + $1 WHERE id = $2
            ''', delta_received + delta_sent, user_id)

        await conn.execute('UPDATE clients SET last_received = $1, last_sent = $2 WHERE id = $3',
                           received, sent, client_id)

        if endpoint:
            if endpoint != last_ip:
                await conn.execute('UPDATE clients SET last_ip = $1 WHERE id = $2', endpoint, client_id)
                await update_client_ip_history(client_id, endpoint)

    ok, reason = await check_user_limits(user_id)
    if not ok:
        logger.warning(f"User {user_id} limit exceeded: {reason}, deactivating all clients")
        await deactivate_user_clients(user_id, server_instance)


async def get_all_clients(server_id: Optional[int] = None) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if server_id:
            rows = await conn.fetch('''
                SELECT public_key, name, ip, traffic_limit_bytes, traffic_used_bytes,
                       expiry_date, is_active, server_id, server_name
                FROM clients WHERE server_id = $1 ORDER BY created_at DESC
            ''', server_id)
        else:
            rows = await conn.fetch('''
                SELECT public_key, name, ip, traffic_limit_bytes, traffic_used_bytes,
                       expiry_date, is_active, server_id, server_name
                FROM clients ORDER BY created_at DESC
            ''')
        return [dict(row) for row in rows]


async def get_client_id_by_public_key(public_key: str) -> Optional[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval('SELECT id FROM clients WHERE public_key = $1', public_key)
        return row


async def deactivate_user_clients(user_id: int, server_instance=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT public_key FROM clients WHERE user_id = $1', user_id)
        for (pub_key,) in rows:
            await conn.execute('UPDATE clients SET is_active = FALSE WHERE public_key = $1', pub_key)
            if server_instance:
                await server_instance.block_client(pub_key)
        logger.info(f"Deactivated all clients for user {user_id}")


async def activate_client(public_key: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE clients SET is_active = TRUE WHERE public_key = $1', public_key)
        logger.info(f"Activated client {public_key[:8]}...")


async def deactivate_client(client_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE clients SET is_active = FALSE WHERE id = $1', client_id)
        logger.info(f"Client {client_id} deactivated")


async def reset_traffic(public_key: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE clients SET traffic_used_bytes = 0 WHERE public_key = $1', public_key)
        logger.info(f"Reset traffic for client {public_key[:8]}...")


async def delete_traffic_history_by_client(client_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM traffic_history WHERE client_id = $1', client_id)
        logger.debug(f"Deleted traffic history for client {client_id}")


async def delete_client_by_id(client_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM traffic_history WHERE client_id = $1', client_id)
        await conn.execute('DELETE FROM clients WHERE id = $1', client_id)
        logger.info(f"Deleted client {client_id} and its traffic history")


async def create_client_for_user(
    user_id: int,
    public_key: str,
    name: str,
    ip: str,
    private_key: str = "",
    server_id: int = 1
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем имя сервера
        server_name = await conn.fetchval('SELECT name FROM servers WHERE id = $1', server_id) or 'local'
        # Вставляем клиента
        row = await conn.fetchrow('''
            INSERT INTO clients (user_id, public_key, name, ip, private_key, server_id, server_name)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        ''', user_id, public_key, name, ip, private_key, server_id, server_name)
        client_id = row['id']
        logger.info(f"Created client {name} ({public_key[:8]}...) for user {user_id}")
        return client_id


async def get_user_clients(user_id: int) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id, public_key, name, ip, server_id, server_name, is_active, created_at
            FROM clients WHERE user_id = $1 AND is_deleted = FALSE ORDER BY created_at DESC
        ''', user_id)
        return [dict(row) for row in rows]


async def get_client_by_id(client_id: int) -> Optional[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT c.*, u.traffic_limit_bytes, u.traffic_used_bytes, u.expiry_date
            FROM clients c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = $1
        ''', client_id)
        if row:
            logger.debug(f"Found client by ID {client_id}")
            return dict(row)
        logger.debug(f"Client ID {client_id} not found")
        return None


async def get_client_by_public_key(public_key: str) -> Optional[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT c.*, u.traffic_limit_bytes, u.traffic_used_bytes, u.expiry_date
            FROM clients c
            JOIN users u ON c.user_id = u.id
            WHERE c.public_key = $1
        ''', public_key)
        if row:
            logger.debug(f"Found client by public key {public_key[:8]}...")
            return dict(row)
        logger.debug(f"Client {public_key[:8]}... not found")
        return None


async def soft_delete_client(client_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE clients SET is_active = FALSE, is_deleted = TRUE WHERE id = $1', client_id)
        logger.info(f"Client {client_id} soft deleted")


async def get_all_clients_with_user_info(include_deleted: bool = False) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = '''
            SELECT
                c.id as client_id,
                c.public_key,
                c.name,
                c.ip,
                c.server_id,
                c.server_name,
                c.is_active,
                c.user_id,
                c.created_at,
                c.last_ip,
                u.username,
                u.traffic_limit_bytes,
                u.traffic_used_bytes,
                u.expiry_date
            FROM clients c
            LEFT JOIN users u ON c.user_id = u.id
        '''
        if not include_deleted:
            query += ' WHERE c.is_deleted = FALSE'
        query += ' ORDER BY c.created_at DESC'
        rows = await conn.fetch(query)
        return [dict(row) for row in rows]


# ---------- Клиенты - GeoIP ----------
async def get_client_ip_history(client_id: int) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT ip, first_seen, last_seen, count
            FROM client_ip_history
            WHERE client_id = $1
            ORDER BY last_seen DESC
        ''', client_id)
        return [dict(row) for row in rows]


async def update_client_ip_history(client_id: int, ip: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute('''
            UPDATE client_ip_history
            SET last_seen = CURRENT_TIMESTAMP,
                count = count + 1
            WHERE client_id = $1 AND ip = $2
        ''', client_id, ip)
        if result == "UPDATE 0":
            await conn.execute('''
                INSERT INTO client_ip_history (client_id, ip)
                VALUES ($1, $2)
            ''', client_id, ip)


# ---------- Серверы ----------
async def get_server(server_id: int) -> Optional[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT id, name, host, port, username, auth_type, password, private_key,
                   is_active, created_at
            FROM servers WHERE id = $1
        ''', server_id)
        if row:
            logger.debug(f"Fetched server {server_id}: {row['name']}")
            return dict(row)
        logger.debug(f"Server {server_id} not found")
        return None


async def get_all_servers() -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id, name, host, port, username, auth_type, is_active, created_at
            FROM servers ORDER BY id ASC
        ''')
        return [dict(row) for row in rows]


async def get_all_servers_full() -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id, name, host, port, username, auth_type, password, private_key, is_active, created_at
            FROM servers ORDER BY id ASC
        ''')
        return [dict(row) for row in rows]


async def add_server(server_data: dict) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            INSERT INTO servers (name, host, port, username, auth_type, password, private_key, is_active)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        ''',
            server_data.get('name'),
            server_data.get('host', 'localhost'),
            server_data.get('port', 22),
            server_data.get('username'),
            server_data.get('auth_type', 'password'),
            server_data.get('password', ''),
            server_data.get('private_key', ''),
            True
        )
        server_id = row['id']
        logger.info(f"Added new server: {server_data.get('name')} (ID {server_id})")
        return server_id


async def update_server(server_id: int, server_data: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        fields = []
        values = []
        old_name = None
        if 'name' in server_data:
            old_name = await conn.fetchval('SELECT name FROM servers WHERE id = $1', server_id)

        for key in ['name', 'host', 'port', 'username', 'auth_type', 'password', 'private_key', 'is_active']:
            if key in server_data:
                fields.append(f"{key} = ${len(values)+1}")
                values.append(server_data[key])

        if fields:
            values.append(server_id)
            query = f"UPDATE servers SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = ${len(values)}"
            await conn.execute(query, *values)
            logger.info(f"Updated server {server_id} with fields: {fields}")

            if 'name' in server_data and server_data['name'] != old_name:
                await update_server_name_for_clients(server_id, server_data['name'])
        else:
            logger.warning(f"No fields to update for server {server_id}")


async def delete_server(server_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval('''
            SELECT COUNT(*) FROM clients WHERE server_id = $1 AND is_deleted = FALSE
        ''', server_id)
        if count > 0:
            logger.warning(f"Attempt to delete server {server_id} with {count} active clients")
            raise Exception(f"Cannot delete server with {count} active clients")
        await conn.execute('DELETE FROM servers WHERE id = $1', server_id)


# ---------- Проверка лимитов ----------
async def get_traffic_today() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        row = await conn.fetchval('''
            SELECT COALESCE(SUM(total_bytes), 0) FROM traffic_history
            WHERE recorded_at >= $1
        ''', today_start)
        return row or 0

async def get_traffic_history(days: int = 30) -> List[Dict]:
    history = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Генерируем список последних дней
        for i in range(days - 1, -1, -1):
            day = datetime.now().date() - timedelta(days=i)
            day_start = datetime.combine(day, datetime.min.time())
            day_end = day_start + timedelta(days=1)
            row = await conn.fetchval('''
                SELECT COALESCE(SUM(total_bytes), 0) FROM traffic_history
                WHERE recorded_at >= $1 AND recorded_at < $2
            ''', day_start, day_end)
            history.append({
                "date": day.isoformat(),
                "bytes": row or 0
            })
    return history

async def get_total_traffic_users() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval('SELECT COALESCE(SUM(traffic_used_bytes), 0) FROM users')
        return row or 0


async def get_expiring_users(days: int = 7) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now()
        future = now + timedelta(days=days)
        rows = await conn.fetch('''
            SELECT id, username, expiry_date FROM users
            WHERE expiry_date IS NOT NULL
            AND expiry_date BETWEEN $1 AND $2
            ORDER BY expiry_date ASC
        ''', now, future)
        return [dict(row) for row in rows]


async def update_client_private_key(client_id: int, private_key: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('UPDATE clients SET private_key = $1 WHERE id = $2', private_key, client_id)
        logger.debug(f"Updated private key for client {client_id}")


async def update_user_limit(user_id: int, limit_bytes: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if limit_bytes <= 0:
            await conn.execute('UPDATE users SET traffic_limit_bytes = NULL WHERE id = $1', user_id)
        else:
            await conn.execute('UPDATE users SET traffic_limit_bytes = $1 WHERE id = $2', limit_bytes, user_id)
        logger.info(f"Updated traffic limit for user {user_id} to {limit_bytes}")


async def update_user_expiry(user_id: int, expiry_date: Optional[str]):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if expiry_date is None:
            await conn.execute('UPDATE users SET expiry_date = NULL WHERE id = $1', user_id)
        else:
            # Преобразуем строку в datetime для PostgreSQL
            dt = datetime.fromisoformat(expiry_date.replace(' ', 'T'))
            await conn.execute('UPDATE users SET expiry_date = $1 WHERE id = $2', dt, user_id)
        logger.info(f"Updated expiry for user {user_id} to {expiry_date}")


async def sync_user_clients_with_limits(user_id: int, server_instance=None):
    ok, reason = await check_user_limits(user_id)
    if not ok:
        await deactivate_user_clients(user_id, server_instance)
    else:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch('SELECT public_key FROM clients WHERE user_id = $1 AND is_active = FALSE', user_id)
            for (pub_key,) in rows:
                await conn.execute('UPDATE clients SET is_active = TRUE WHERE public_key = $1', pub_key)
                if server_instance:
                    await server_instance.unblock_client(pub_key)
        logger.info(f"Activated all clients for user {user_id} (limits OK)")


async def get_server_clients(server_id: int) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT public_key, is_active FROM clients WHERE server_id = $1', server_id)
        return [dict(row) for row in rows]


async def get_users_exceeded_traffic() -> List[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id FROM users
            WHERE traffic_limit_bytes IS NOT NULL
              AND traffic_used_bytes > traffic_limit_bytes
        ''')
        return [row['id'] for row in rows]


async def get_users_expired() -> List[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id FROM users
            WHERE expiry_date IS NOT NULL
              AND expiry_date <= NOW()
        ''')
        return [row['id'] for row in rows]


async def get_user_clients_grouped_by_server(user_id: int) -> Dict[int, List[Dict]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id, public_key, server_id, is_active
            FROM clients
            WHERE user_id = $1 AND is_deleted = FALSE
        ''', user_id)
        result = {}
        for row in rows:
            d = dict(row)
            server_id = d['server_id']
            result.setdefault(server_id, []).append(d)
        return result


async def sync_user_limits_across_servers(user_id: int, server_instances: Dict[int, 'AmneziaWGServer']):
    ok, reason = await check_user_limits(user_id)
    clients_by_server = await get_user_clients_grouped_by_server(user_id)
    for server_id, clients in clients_by_server.items():
        server = server_instances.get(server_id)
        if not server:
            logger.warning(f"Server {server_id} not available for user {user_id} sync")
            continue
        for client in clients:
            if ok:
                await server.unblock_client(client['public_key'])
                await activate_client(client['public_key'])
            else:
                await server.block_client(client['public_key'])
                await deactivate_client(client['id'])
    logger.info(f"Synced user {user_id} across servers, limits ok: {ok}")


async def update_server_name_for_clients(server_id: int, new_name: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            UPDATE clients SET server_name = $1 WHERE server_id = $2
        ''', new_name, server_id)
        logger.info(f"Updated server_name to '{new_name}' for all clients of server {server_id}")