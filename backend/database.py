import aiosqlite
import os
from datetime import datetime
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица клиентов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                public_key TEXT PRIMARY KEY,
                name TEXT,
                ip TEXT,
                private_key TEXT,
                traffic_limit_bytes INTEGER,
                traffic_used_bytes INTEGER DEFAULT 0,
                expiry_date TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                server_id INTEGER DEFAULT 1,
                server_name TEXT DEFAULT 'local',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица истории трафика
        await db.execute('''
            CREATE TABLE IF NOT EXISTS traffic_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT,
                bytes_received INTEGER,
                bytes_sent INTEGER,
                total_bytes INTEGER,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (public_key) REFERENCES clients(public_key)
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
        cursor = await db.execute('SELECT username, password_hash, role FROM users WHERE username = ?', (username,))
        row = await cursor.fetchone()
        if row:
            logger.debug(f"Found user {username}")
            return dict(row)
        logger.debug(f"User {username} not found")
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
        cursor = await db.execute('SELECT id, username, role, created_at FROM users ORDER BY created_at DESC')
        rows = await cursor.fetchall()
        logger.debug(f"Fetched {len(rows)} users")
        return [dict(row) for row in rows]

async def update_user(user_id: int, username: str = None, password: str = None, role: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if username:
            await db.execute('UPDATE users SET username = ? WHERE id = ?', (username, user_id))
        if password:
            password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            await db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, user_id))
        if role:
            await db.execute('UPDATE users SET role = ? WHERE id = ?', (role, user_id))
        await db.commit()
        logger.info(f"Updated user {user_id}")

async def delete_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM users WHERE id = ?', (user_id,))
        await db.commit()
        logger.info(f"Deleted user {user_id}")

# ---------- Клиенты ----------
async def get_client(public_key: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT name, ip, private_key, traffic_limit_bytes, traffic_used_bytes,
                   expiry_date, is_active, server_id, server_name
            FROM clients WHERE public_key = ?
        ''', (public_key,))
        row = await cursor.fetchone()
        if row:
            logger.debug(f"Found client {public_key[:8]}...")
            return dict(row)
        logger.debug(f"Client {public_key[:8]}... not found")
        return None

async def upsert_client(public_key: str, name: str, ip: str, server_id: int = 1):
    """Создаёт или обновляет запись клиента (имя и IP)."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем имя сервера
        cursor = await db.execute('SELECT name FROM servers WHERE id = ?', (server_id,))
        row = await cursor.fetchone()
        server_name = row[0] if row else 'local'

        await db.execute('''
            INSERT INTO clients (public_key, name, ip, server_id, server_name, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(public_key) DO UPDATE SET
                name = excluded.name,
                ip = excluded.ip,
                server_id = excluded.server_id,
                server_name = excluded.server_name,
                updated_at = CURRENT_TIMESTAMP
        ''', (public_key, name, ip, server_id, server_name))
        await db.commit()

async def create_client(public_key: str, name: str, ip: str, private_key: str = "", server_id: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем имя сервера
        cursor = await db.execute('SELECT name FROM servers WHERE id = ?', (server_id,))
        row = await cursor.fetchone()
        server_name = row[0] if row else 'local'
        await db.execute('''
            INSERT INTO clients (public_key, name, ip, private_key, server_id, server_name, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(public_key) DO UPDATE SET
                name=excluded.name,
                ip=excluded.ip,
                private_key=CASE WHEN excluded.private_key IS NULL OR excluded.private_key = '' THEN clients.private_key ELSE excluded.private_key END,
                server_id=excluded.server_id,
                server_name=excluded.server_name,
                updated_at=CURRENT_TIMESTAMP
        ''', (public_key, name, ip, private_key, server_id, server_name))
        await db.commit()
        logger.info(f"Created/updated client {name} ({public_key[:8]}...) on server {server_name}")

async def set_client_limit(public_key: str, limit_bytes: int):
    logger.info(f"Setting limit for {public_key[:8]}... to {limit_bytes} bytes")
    async with aiosqlite.connect(DB_PATH) as db:
        if limit_bytes <= 0:
            await db.execute('''
                UPDATE clients SET traffic_limit_bytes = NULL, traffic_used_bytes = 0,
                    is_active = 1, updated_at = CURRENT_TIMESTAMP
                WHERE public_key = ?
            ''', (public_key,))
            logger.info(f"Limit removed for {public_key[:8]}..., client activated")
        else:
            await db.execute('''
                UPDATE clients SET traffic_limit_bytes = ?, traffic_used_bytes = 0,
                    is_active = 1, updated_at = CURRENT_TIMESTAMP
                WHERE public_key = ?
            ''', (limit_bytes, public_key))
            logger.info(f"Limit set to {limit_bytes} for {public_key[:8]}..., client activated")
        await db.commit()

async def set_client_expiry(public_key: str, expiry_date: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        is_active = 1
        if expiry_date:
            try:
                expiry = datetime.fromisoformat(expiry_date.replace(' ', 'T'))
                if datetime.now() > expiry:
                    is_active = 0
            except Exception as e:
                logger.error(f"Invalid expiry date {expiry_date}: {e}")
        await db.execute('''
            UPDATE clients SET expiry_date = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
            WHERE public_key = ?
        ''', (expiry_date, is_active, public_key))
        await db.commit()
        logger.info(f"Set expiry {expiry_date} for {public_key[:8]}...")

async def update_traffic_usage(public_key: str, received: int, sent: int, server_instance=None):
    logger.info(f"Updating traffic for {public_key[:8]}: recv={received}, sent={sent}")
    total = received + sent
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT traffic_limit_bytes, traffic_used_bytes, is_active
            FROM clients WHERE public_key = ?
        ''', (public_key,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                logger.warning(f"Client {public_key[:8]} not found in DB, skipping traffic update")
                return
            limit_bytes, used_bytes, is_active = row

        new_used = max(total, used_bytes)
        await db.execute('UPDATE clients SET traffic_used_bytes = ? WHERE public_key = ?', (new_used, public_key))
        await db.execute('''
            INSERT INTO traffic_history (public_key, bytes_received, bytes_sent, total_bytes)
            VALUES (?, ?, ?, ?)
        ''', (public_key, received, sent, total))
        await db.commit()
        logger.info(f"Updated {public_key[:8]} used to {new_used}")

        if limit_bytes and new_used > limit_bytes and is_active:
            await db.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (public_key,))
            await db.commit()
            logger.warning(f"Client {public_key[:8]} exceeded limit, deactivating")
            if server_instance:
                await server_instance.block_client(public_key)

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

async def activate_client(public_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE clients SET is_active = 1 WHERE public_key = ?', (public_key,))
        await db.commit()
        logger.info(f"Activated client {public_key[:8]}...")

async def deactivate_client(public_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (public_key,))
        await db.commit()
        logger.info(f"Deactivated client {public_key[:8]}...")

async def reset_traffic(public_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE clients SET traffic_used_bytes = 0 WHERE public_key = ?', (public_key,))
        await db.commit()
        logger.info(f"Reset traffic for client {public_key[:8]}...")

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
            logger.info(f"Updated server {server_id}")

async def delete_server(server_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, есть ли клиенты
        cursor = await db.execute('SELECT COUNT(*) FROM clients WHERE server_id = ?', (server_id,))
        count = (await cursor.fetchone())[0]
        if count > 0:
            logger.warning(f"Attempt to delete server {server_id} with {count} clients")
            raise Exception(f"Cannot delete server with {count} active clients")
        await db.execute('DELETE FROM servers WHERE id = ?', (server_id,))
        await db.commit()
        logger.info(f"Deleted server {server_id}")

# ---------- Проверка лимитов ----------
async def check_traffic_limits(server_instance=None) -> List[str]:
    """Возвращает список публичных ключей клиентов, превысивших лимит."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT public_key FROM clients
            WHERE traffic_limit_bytes IS NOT NULL
              AND traffic_used_bytes > traffic_limit_bytes
              AND is_active = 1
        ''')
        rows = await cursor.fetchall()
        deactivated = []
        for (pub_key,) in rows:
            await db.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (pub_key,))
            deactivated.append(pub_key)
            logger.warning(f"Traffic limit exceeded for {pub_key[:8]}... deactivating")
            if server_instance:
                await server_instance.block_client(pub_key)
        await db.commit()
        return deactivated

async def check_expiry_limits(server_instance=None) -> List[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            SELECT public_key FROM clients
            WHERE expiry_date IS NOT NULL
              AND datetime(expiry_date) <= datetime('now')
              AND is_active = 1
        ''')
        rows = await cursor.fetchall()
        deactivated = []
        for (pub_key,) in rows:
            await db.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (pub_key,))
            deactivated.append(pub_key)
            logger.warning(f"Expiry date reached for {pub_key[:8]}... deactivating")
            if server_instance:
                await server_instance.block_client(pub_key)
        await db.commit()
        return deactivated

async def check_all_limits(server_instance=None) -> Dict:
    traffic = await check_traffic_limits(server_instance)
    expiry = await check_expiry_limits(server_instance)
    total = len(traffic) + len(expiry)
    logger.info(f"Checked limits: {total} clients deactivated (traffic: {len(traffic)}, expiry: {len(expiry)})")
    return {
        'traffic_deactivated': traffic,
        'expiry_deactivated': expiry,
        'total_deactivated': total
    }