# database.py
import aiosqlite
import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import bcrypt
from logger import logger

DB_PATH = "/app/data/amnezia.db"


async def init_db():
    """Инициализация БД (создание таблиц, если нет)."""
    logger.info("Initializing database...")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                traffic_limit_bytes INTEGER,
                traffic_used_bytes INTEGER DEFAULT 0,
                expiry_date TIMESTAMP,
                config_limit INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица клиентов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                public_key TEXT UNIQUE NOT NULL,
                name TEXT,
                ip TEXT,
                private_key TEXT,
                last_received INTEGER DEFAULT 0,
                last_sent INTEGER DEFAULT 0,
                server_id INTEGER DEFAULT 1,
                server_name TEXT DEFAULT 'local',
                is_active BOOLEAN DEFAULT 1,
                is_deleted BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (server_id) REFERENCES servers(id)
            )
        ''')
        # Таблица истории трафика
        await db.execute('''
            CREATE TABLE IF NOT EXISTS traffic_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                bytes_received INTEGER,
                bytes_sent INTEGER,
                total_bytes INTEGER,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (client_id) REFERENCES clients(id)
            )
        ''')
        # Таблица серверов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                host TEXT,
                port INTEGER DEFAULT 22,
                username TEXT,
                auth_type TEXT DEFAULT 'password',
                password TEXT,
                private_key TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()
        logger.debug("Database tables created/verified")

        # Добавляем сервер по умолчанию (локальный), если нет
        cursor = await db.execute("SELECT COUNT(*) FROM servers")
        count = (await cursor.fetchone())[0]
        if count == 0:
            await db.execute('''
                INSERT INTO servers (name, host, username, auth_type, is_active)
                VALUES (?, ?, ?, ?, ?)
            ''', ('local', 'localhost', 'local', 'local', 1))
            await db.commit()
            logger.info("Added default local server")

        # Пользователь admin/admin (пароль хешируем)
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        count = (await cursor.fetchone())[0]
        if count == 0:
            password_hash = bcrypt.hashpw(b'admin', bcrypt.gensalt()).decode()
            await db.execute('''
                INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)
            ''', ('admin', password_hash, 'admin'))
            await db.commit()
            logger.info("Created default admin user")


# ---------- Пользователи ----------
async def get_user_by_username(username: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT id, username, password_hash, role, traffic_limit_bytes,
                   traffic_used_bytes, expiry_date, config_limit, created_at
            FROM users WHERE username = ?
        ''', (username,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def create_user(username: str, password: str, role: str = 'user') -> bool:
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute('INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
                             (username, password_hash, role))
            await db.commit()
            logger.info(f"Created user {username} with role {role}")
            return True
        except aiosqlite.IntegrityError:
            logger.warning(f"Attempt to create duplicate user {username}")
            return False


async def get_all_users() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT id, username, role, traffic_limit_bytes, traffic_used_bytes, 
                   expiry_date, config_limit, created_at
            FROM users ORDER BY created_at DESC
        ''')
        rows = await cursor.fetchall()
        logger.debug(f"Fetched {len(rows)} users")
        return [dict(row) for row in rows]


async def update_user(user_id: int, username: str = None, password: str = None, 
                      role: str = None, config_limit: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if username:
            await db.execute('UPDATE users SET username = ? WHERE id = ?', (username, user_id))
        if password:
            password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            await db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, user_id))
        if role:
            await db.execute('UPDATE users SET role = ? WHERE id = ?', (role, user_id))
        if config_limit is not None:
            await db.execute('UPDATE users SET config_limit = ? WHERE id = ?', (config_limit, user_id))
        await db.commit()
        logger.info(f"Updated user {user_id}")

async def delete_user(user_id: int):
    """Удаляет пользователя и всех его клиентов (включая историю трафика)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT id FROM clients WHERE user_id = ?', (user_id,))
        rows = await cursor.fetchall()
        client_ids = [row[0] for row in rows]
        for cid in client_ids:
            await db.execute('DELETE FROM traffic_history WHERE client_id = ?', (cid,))
        await db.execute('DELETE FROM clients WHERE user_id = ?', (user_id,))
        await db.execute('DELETE FROM users WHERE id = ?', (user_id,))
        await db.commit()
        logger.info(f"Deleted user {user_id} and {len(client_ids)} associated clients")


async def get_user_by_id(user_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT id, username, role, traffic_limit_bytes, traffic_used_bytes,
                   expiry_date, config_limit, created_at
            FROM users WHERE id = ?
        ''', (user_id,))
        row = await cursor.fetchone()
        if row:
            logger.debug(f"Found user by ID {user_id}")
            return dict(row)
        logger.debug(f"User ID {user_id} not found")
        return None


async def update_user_traffic_used(user_id: int, bytes_used: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE users SET traffic_used_bytes = traffic_used_bytes + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (bytes_used, user_id))
        await db.commit()
        logger.debug(f"Updated traffic for user {user_id}: +{bytes_used} bytes")


async def check_user_limits(user_id: int) -> tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT traffic_limit_bytes, traffic_used_bytes, expiry_date
            FROM users WHERE id = ?
        ''', (user_id,))
        row = await cursor.fetchone()
        if not row:
            return False, "User not found"
        limit_bytes, used_bytes, expiry_date = row
        if limit_bytes and used_bytes > limit_bytes:
            return False, "Traffic limit exceeded"
        if expiry_date:
            try:
                expiry = datetime.fromisoformat(expiry_date.replace(' ', 'T'))
                if datetime.now() > expiry:
                    return False, "Subscription expired"
            except Exception as e:
                logger.error(f"Error parsing expiry date: {e}")
        return True, "OK"


async def can_create_config(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT role, config_limit FROM users WHERE id = ?', (user_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        role, config_limit = row
        if role == 'admin':
            return True
        cursor = await db.execute('''
            SELECT COUNT(*) FROM clients 
            WHERE user_id = ? AND is_deleted = 0
        ''', (user_id,))
        current_configs = (await cursor.fetchone())[0]
        return current_configs < config_limit


async def get_user_traffic_stats(user_id: int, days: int = 30) -> Dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT id FROM clients WHERE user_id = ?', (user_id,))
        rows = await cursor.fetchall()
        client_ids = [r[0] for r in rows]
        if not client_ids:
            return {"total_received": 0, "total_sent": 0, "total": 0, "by_client": []}
        placeholders = ','.join('?' for _ in client_ids)
        query = f'''
            SELECT SUM(bytes_received), SUM(bytes_sent)
            FROM traffic_history
            WHERE client_id IN ({placeholders}) AND recorded_at >= datetime('now', ?)
        '''
        params = client_ids + [f'-{days} days']
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
        total_recv = row[0] or 0
        total_sent = row[1] or 0
        detailed = []
        for cid in client_ids:
            cursor = await db.execute('SELECT name FROM clients WHERE id = ?', (cid,))
            name_row = await cursor.fetchone()
            name = name_row[0] if name_row else 'Unknown'

            cursor = await db.execute('''
                SELECT SUM(bytes_received), SUM(bytes_sent)
                FROM traffic_history
                WHERE client_id = ? AND recorded_at >= datetime('now', ?)
            ''', (cid, f'-{days} days'))
            row2 = await cursor.fetchone()
            recv = row2[0] or 0
            sent = row2[1] or 0
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
async def update_traffic(public_key: str, received: int, sent: int, server_instance=None):
    client = await get_client_by_public_key(public_key)
    if not client:
        return

    client_id = client['id']
    user_id = client['user_id']

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем последние сохранённые значения
        cursor = await db.execute(
            'SELECT last_received, last_sent FROM clients WHERE id = ?',
            (client_id,)
        )
        row = await cursor.fetchone()
        if row:
            last_received, last_sent = row
        else:
            last_received = last_sent = 0

        # Проверка на сброс счётчиков (перезапуск контейнера)
        if received < last_received or sent < last_sent:
            # Просто обновляем последние значения, без записи в историю
            await db.execute(
                'UPDATE clients SET last_received = ?, last_sent = ? WHERE id = ?',
                (received, sent, client_id)
            )
            await db.commit()
            logger.debug(f"Counter reset for client {client_id}, updated last values")
            return

        delta_received = received - last_received
        delta_sent = sent - last_sent

        if delta_received > 0 or delta_sent > 0:
            # Вставляем дельту в историю
            await db.execute('''
                INSERT INTO traffic_history (client_id, bytes_received, bytes_sent, total_bytes)
                VALUES (?, ?, ?, ?)
            ''', (client_id, delta_received, delta_sent, delta_received + delta_sent))

            # Обновляем суммарный трафик пользователя
            await db.execute('''
                UPDATE users SET traffic_used_bytes = traffic_used_bytes + ?
                WHERE id = ?
            ''', (delta_received + delta_sent, user_id))

        # Всегда обновляем последние значения
        await db.execute(
            'UPDATE clients SET last_received = ?, last_sent = ? WHERE id = ?',
            (received, sent, client_id)
        )
        await db.commit()

    # Проверка лимитов после обновления
    ok, reason = await check_user_limits(user_id)
    if not ok:
        logger.warning(f"User {user_id} limit exceeded: {reason}, deactivating all clients")
        await deactivate_user_clients(user_id, server_instance)


async def get_all_clients(server_id: Optional[int] = None) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if server_id:
            cursor = await db.execute('''
                SELECT public_key, name, ip, traffic_limit_bytes, traffic_used_bytes,
                       expiry_date, is_active, server_id, server_name
                FROM clients WHERE server_id = ? ORDER BY created_at DESC
            ''', (server_id,))
        else:
            cursor = await db.execute('''
                SELECT public_key, name, ip, traffic_limit_bytes, traffic_used_bytes,
                       expiry_date, is_active, server_id, server_name
                FROM clients ORDER BY created_at DESC
            ''')
        rows = await cursor.fetchall()
        logger.debug(f"Fetched {len(rows)} clients")
        return [dict(row) for row in rows]


async def get_client_id_by_public_key(public_key: str) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT id FROM clients WHERE public_key = ?', (public_key,))
        row = await cursor.fetchone()
        return row[0] if row else None


async def deactivate_user_clients(user_id: int, server_instance=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT public_key FROM clients WHERE user_id = ?', (user_id,))
        rows = await cursor.fetchall()
        for (pub_key,) in rows:
            await db.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (pub_key,))
            if server_instance:
                await server_instance.block_client(pub_key)
        await db.commit()
        logger.info(f"Deactivated all clients for user {user_id}")


async def activate_client(public_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE clients SET is_active = 1 WHERE public_key = ?', (public_key,))
        await db.commit()
        logger.info(f"Activated client {public_key[:8]}...")


async def deactivate_client(client_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE clients SET is_active = 0 WHERE id = ?', (client_id,))
        await db.commit()
        logger.info(f"Client {client_id} deactivated")


async def reset_traffic(public_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE clients SET traffic_used_bytes = 0 WHERE public_key = ?', (public_key,))
        await db.commit()
        logger.info(f"Reset traffic for client {public_key[:8]}...")


async def delete_traffic_history_by_client(client_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM traffic_history WHERE client_id = ?', (client_id,))
        await db.commit()
        logger.debug(f"Deleted traffic history for client {client_id}")


async def delete_client_by_id(client_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM traffic_history WHERE client_id = ?', (client_id,))
        await db.execute('DELETE FROM clients WHERE id = ?', (client_id,))
        await db.commit()
        logger.info(f"Deleted client {client_id} and its traffic history")


async def create_client_for_user(
    user_id: int,
    public_key: str,
    name: str,
    ip: str,
    private_key: str = "",
    server_id: int = 1
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT name FROM servers WHERE id = ?', (server_id,))
        row = await cursor.fetchone()
        server_name = row[0] if row else 'local'

        cursor = await db.execute('''
            INSERT INTO clients (user_id, public_key, name, ip, private_key, server_id, server_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        ''', (user_id, public_key, name, ip, private_key, server_id, server_name))

        row = await cursor.fetchone()
        await db.commit()
        client_id = row[0]
        logger.info(f"Created client {name} ({public_key[:8]}...) for user {user_id}")
        return client_id


async def get_user_clients(user_id: int) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT id, public_key, name, ip, server_id, server_name, is_active, created_at
            FROM clients WHERE user_id = ? ORDER BY created_at DESC
        ''', (user_id,))
        rows = await cursor.fetchall()
        logger.debug(f"Found {len(rows)} clients for user {user_id}")
        return [dict(row) for row in rows]


async def get_client_by_id(client_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT c.*, u.traffic_limit_bytes, u.traffic_used_bytes, u.expiry_date
            FROM clients c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = ?
        ''', (client_id,))
        row = await cursor.fetchone()
        if row:
            logger.debug(f"Found client by ID {client_id}")
            return dict(row)
        logger.debug(f"Client ID {client_id} not found")
        return None


async def get_client_by_public_key(public_key: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT c.*, u.traffic_limit_bytes, u.traffic_used_bytes, u.expiry_date
            FROM clients c
            JOIN users u ON c.user_id = u.id
            WHERE c.public_key = ?
        ''', (public_key,))
        row = await cursor.fetchone()
        if row:
            logger.debug(f"Found client by public key {public_key[:8]}...")
            return dict(row)
        logger.debug(f"Client {public_key[:8]}... not found")
        return None


async def soft_delete_client(client_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE clients SET is_active = 0, is_deleted = 1 WHERE id = ?', (client_id,))
        await db.commit()
        logger.info(f"Client {client_id} soft deleted")


async def get_all_clients_with_user_info(include_deleted: bool = False) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
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
                u.username,
                u.traffic_limit_bytes,
                u.traffic_used_bytes,
                u.expiry_date
            FROM clients c
            LEFT JOIN users u ON c.user_id = u.id
        '''
        if not include_deleted:
            query += ' WHERE c.is_deleted = 0'
        query += ' ORDER BY c.created_at DESC'
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ---------- Серверы ----------
async def get_server(server_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT id, name, host, port, username, auth_type, password, private_key,
                   is_active, created_at
            FROM servers WHERE id = ?
        ''', (server_id,))
        row = await cursor.fetchone()
        if row:
            logger.debug(f"Fetched server {server_id}: {row['name']}")
            return dict(row)
        logger.debug(f"Server {server_id} not found")
        return None


async def get_all_servers() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT id, name, host, port, username, auth_type, is_active, created_at
            FROM servers ORDER BY id ASC
        ''')
        rows = await cursor.fetchall()
        logger.debug(f"Fetched {len(rows)} servers")
        return [dict(row) for row in rows]


async def get_all_servers_full() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT id, name, host, port, username, auth_type, password, private_key, is_active, created_at
            FROM servers ORDER BY id ASC
        ''')
        rows = await cursor.fetchall()
        logger.debug(f"Fetched {len(rows)} servers (full)")
        return [dict(row) for row in rows]


async def add_server(server_data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            INSERT INTO servers (name, host, port, username, auth_type, password, private_key, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            server_data.get('name'),
            server_data.get('host', 'localhost'),
            server_data.get('port', 22),
            server_data.get('username'),
            server_data.get('auth_type', 'password'),
            server_data.get('password', ''),
            server_data.get('private_key', ''),
            1
        ))
        await db.commit()
        server_id = cursor.lastrowid
        logger.info(f"Added new server: {server_data.get('name')} (ID {server_id})")
        return server_id


async def update_server(server_id: int, server_data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        fields = []
        values = []
        for key in ['name', 'host', 'port', 'username', 'auth_type', 'password', 'private_key', 'is_active']:
            if key in server_data:
                fields.append(f"{key} = ?")
                values.append(server_data[key])
        if fields:
            values.append(server_id)
            query = f"UPDATE servers SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            await db.execute(query, values)
            await db.commit()
            logger.info(f"Updated server {server_id} with fields: {fields}")
        else:
            logger.warning(f"No fields to update for server {server_id}")


async def delete_server(server_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT COUNT(*) FROM clients WHERE server_id = ?', (server_id,))
        count = (await cursor.fetchone())[0]
        if count > 0:
            logger.warning(f"Attempt to delete server {server_id} with {count} clients")
            raise Exception(f"Cannot delete server with {count} active clients")
        await db.execute('DELETE FROM servers WHERE id = ?', (server_id,))
        await db.commit()
        logger.info(f"Deleted server {server_id}")


# ---------- Проверка лимитов ----------
async def check_traffic_limits(server_instance=None) -> List[int]:
    deactivated_users = []
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT id FROM users
            WHERE traffic_limit_bytes IS NOT NULL
              AND traffic_used_bytes > traffic_limit_bytes
        ''')
        rows = await cursor.fetchall()
        for (user_id,) in rows:
            deactivated_users.append(user_id)
            await db.execute('UPDATE clients SET is_active = 0 WHERE user_id = ?', (user_id,))
            logger.warning(f"Traffic limit exceeded for user {user_id}, deactivating all clients")
            if server_instance:
                cursor2 = await db.execute('SELECT public_key FROM clients WHERE user_id = ?', (user_id,))
                keys = await cursor2.fetchall()
                for (pub_key,) in keys:
                    await server_instance.block_client(pub_key)
        await db.commit()
    return deactivated_users


async def check_expiry_limits(server_instance=None) -> List[int]:
    deactivated_users = []
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT id FROM users
            WHERE expiry_date IS NOT NULL
              AND datetime(expiry_date) <= datetime('now')
        ''')
        rows = await cursor.fetchall()
        for (user_id,) in rows:
            deactivated_users.append(user_id)
            await db.execute('UPDATE clients SET is_active = 0 WHERE user_id = ?', (user_id,))
            logger.warning(f"Expiry date reached for user {user_id}, deactivating all clients")
            if server_instance:
                cursor2 = await db.execute('SELECT public_key FROM clients WHERE user_id = ?', (user_id,))
                keys = await cursor2.fetchall()
                for (pub_key,) in keys:
                    await server_instance.block_client(pub_key)
        await db.commit()
    return deactivated_users


async def check_all_limits(server_instance=None) -> Dict:
    traffic = await check_traffic_limits(server_instance)
    expiry = await check_expiry_limits(server_instance)
    total = len(traffic) + len(expiry)
    logger.info(f"Checked limits: {total} users deactivated (traffic: {len(traffic)}, expiry: {len(expiry)})")
    return {
        'traffic_deactivated': traffic,
        'expiry_deactivated': expiry,
        'total_deactivated': total
    }


async def get_traffic_today() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_str = today_start.strftime('%Y-%m-%d %H:%M:%S')
        cursor = await db.execute('''
            SELECT SUM(total_bytes) FROM traffic_history
            WHERE recorded_at >= ?
        ''', (today_start_str,))
        row = await cursor.fetchone()
        return row[0] if row and row[0] else 0


async def get_traffic_history(days: int = 30) -> List[Dict]:
    history = []
    async with aiosqlite.connect(DB_PATH) as db:
        for i in range(days - 1, 0, -1):
            date = datetime.now() - timedelta(days=i)
            next_date = date + timedelta(days=1)
            date_str = date.strftime('%Y-%m-%d')
            date_start = date.strftime('%Y-%m-%d %H:%M:%S')
            date_end = next_date.strftime('%Y-%m-%d %H:%M:%S')

            cursor = await db.execute('''
                SELECT SUM(total_bytes) FROM traffic_history
                WHERE recorded_at >= ? AND recorded_at < ?
            ''', (date_start, date_end))
            row = await cursor.fetchone()
            bytes_total = row[0] if row and row[0] else 0
            history.append({
                "date": date_str,
                "bytes": bytes_total
            })
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_str = today_start.strftime('%Y-%m-%d')
        cursor = await db.execute('''
            SELECT SUM(total_bytes) FROM traffic_history
            WHERE recorded_at >= ?
        ''', (today_start.strftime('%Y-%m-%d %H:%M:%S'),))
        row = await cursor.fetchone()
        bytes_today = row[0] if row and row[0] else 0
        history.append({
            "date": today_str,
            "bytes": bytes_today
        })
    return history

async def get_total_traffic_users() -> int:
    """Возвращает суммарный трафик всех пользователей (traffic_used_bytes)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT COALESCE(SUM(traffic_used_bytes), 0) FROM users')
        row = await cursor.fetchone()
        return row[0] if row else 0

async def get_expiring_users(days: int = 7) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        now = datetime.now()
        future = now + timedelta(days=days)
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')
        future_str = future.strftime('%Y-%m-%d %H:%M:%S')
        cursor = await db.execute('''
            SELECT id, username, expiry_date FROM users
            WHERE expiry_date IS NOT NULL
            AND expiry_date BETWEEN ? AND ?
            ORDER BY expiry_date ASC
        ''', (now_str, future_str))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def update_client_private_key(client_id: int, private_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE clients SET private_key = ? WHERE id = ?', (private_key, client_id))
        await db.commit()
        logger.debug(f"Updated private key for client {client_id}")


async def update_user_limit(user_id: int, limit_bytes: int):
    async with aiosqlite.connect(DB_PATH) as db:
        if limit_bytes <= 0:
            await db.execute('UPDATE users SET traffic_limit_bytes = NULL WHERE id = ?', (user_id,))
        else:
            await db.execute('UPDATE users SET traffic_limit_bytes = ? WHERE id = ?', (limit_bytes, user_id))
        await db.commit()
        logger.info(f"Updated traffic limit for user {user_id} to {limit_bytes}")


async def update_user_expiry(user_id: int, expiry_date: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET expiry_date = ? WHERE id = ?', (expiry_date, user_id))
        await db.commit()
        logger.info(f"Updated expiry for user {user_id} to {expiry_date}")


async def sync_user_clients_with_limits(user_id: int, server_instance=None):
    ok, reason = await check_user_limits(user_id)
    if not ok:
        await deactivate_user_clients(user_id, server_instance)
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT public_key FROM clients WHERE user_id = ? AND is_active = 0', (user_id,))
            rows = await cursor.fetchall()
            for (pub_key,) in rows:
                await db.execute('UPDATE clients SET is_active = 1 WHERE public_key = ?', (pub_key,))
                if server_instance:
                    await server_instance.unblock_client(pub_key)
            await db.commit()
        logger.info(f"Activated all clients for user {user_id} (limits OK)")


async def get_server_clients(server_id: int) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT public_key, is_active FROM clients WHERE server_id = ?', (server_id,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]