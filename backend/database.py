import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict

DB_PATH = "/app/data/amnezia.db"

def init_db():
    """Создаёт таблицы если их нет"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Таблица пользователей
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица клиентов
    c.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            public_key TEXT PRIMARY KEY,
            name TEXT,
            ip TEXT,
            private_key TEXT,
            traffic_limit_bytes INTEGER,
            traffic_used_bytes INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # !!!!!!! ВРЕМЕННО! ДО СБРОСА БД! #
    try:
        # Проверяем, есть ли уже колонка server_id
        c.execute("SELECT server_id FROM clients LIMIT 1")
    except sqlite3.OperationalError:
        # Если нет - добавляем
        c.execute("ALTER TABLE clients ADD COLUMN server_id INTEGER DEFAULT 1")
        c.execute("ALTER TABLE clients ADD COLUMN server_name TEXT DEFAULT 'local'")
        print("Added server_id and server_name columns to clients table")
    ##################################################

    # Таблица истории трафика
    c.execute('''
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT,
            port INTEGER DEFAULT 22,
            username TEXT,
            auth_type TEXT DEFAULT 'password',  -- 'password', 'key', 'key+sudo'
            password TEXT,                      -- может быть пустым если только ключ
            private_key TEXT,                   -- может быть пустым если только пароль
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Добавляем сервер по умолчанию (локальный)
    try:
        c.execute("SELECT COUNT(*) FROM servers")
        if c.fetchone()[0] == 0:
            c.execute('''
                INSERT INTO servers (name, host, username, auth_type, is_active)
                VALUES (?, ?, ?, ?, ?)
            ''', ('local', 'localhost', 'local', 'local', 1))
            print("Added default local server")
    except:
        pass
    
    conn.commit()
    
    # Инициализируем пользователей по умолчанию
    try:
        c.execute("SELECT COUNT(*) FROM users")
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                     ("admin", "admin", "admin"))
            conn.commit()
    except:
        pass
    
    conn.close()

def get_client(public_key: str):
    """Получает данные клиента"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT name, ip, private_key, traffic_limit_bytes, traffic_used_bytes, is_active FROM clients WHERE public_key = ?', 
              (public_key,))
    result = c.fetchone()
    conn.close()
    
    if result:
        return {
            "name": result[0],
            "ip": result[1],
            "private_key": result[2],
            "limit": result[3],
            "used": result[4],
            "is_active": bool(result[5])
        }
    return None

def create_client(public_key: str, name: str, ip: str, private_key: str = "", server_id: int = 1):
    """Создаёт запись о клиенте с привязкой к серверу"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Получаем имя сервера для быстрого доступа
    server = get_server(server_id)
    server_name = server['name'] if server else 'local'
    
    c.execute('''
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
    
    conn.commit()
    conn.close()

def set_client_limit(public_key: str, limit_bytes: int):
    """Устанавливает лимит и синхронизирует iptables"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    if limit_bytes <= 0:
        c.execute('''
            UPDATE clients 
            SET traffic_limit_bytes = NULL,
                traffic_used_bytes = 0,
                is_active = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE public_key = ?
        ''', (public_key,))
    else:
        c.execute('''
            UPDATE clients 
            SET traffic_limit_bytes = ?,
                traffic_used_bytes = 0,
                is_active = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE public_key = ?
        ''', (limit_bytes, public_key))
    
    conn.commit()
    conn.close()
    
    # Разблокируем в iptables
    from awg_manager import AWGManager
    awg = AWGManager()
    awg.unblock_client(public_key)

def update_traffic_usage(public_key: str, received: int, sent: int, awg_manager=None):
    """Обновляет трафик и блокирует при превышении"""
    total = received + sent
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('SELECT traffic_limit_bytes, traffic_used_bytes, is_active FROM clients WHERE public_key = ?', (public_key,))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    
    limit_bytes, used_bytes, is_active = row
    
    # Обновляем использованный трафик
    new_used = max(total, used_bytes)
    c.execute('UPDATE clients SET traffic_used_bytes = ? WHERE public_key = ?', (new_used, public_key))
    c.execute('INSERT INTO traffic_history (public_key, bytes_received, bytes_sent, total_bytes) VALUES (?, ?, ?, ?)',
              (public_key, received, sent, total))
    
    conn.commit()
    
    # Проверяем лимит
    if limit_bytes and new_used > limit_bytes and is_active:
        c.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (public_key,))
        conn.commit()
        conn.close()
        
        # Блокируем в iptables
        if awg_manager:
            awg_manager.block_client(public_key)
        return
    
    conn.close()


def check_and_deactivate_overlimit():
    """Проверяет клиентов, превысивших лимит, и деактивирует их"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Находим кто превысил лимит
    c.execute('''
        SELECT public_key FROM clients
        WHERE traffic_limit_bytes IS NOT NULL
          AND traffic_used_bytes > traffic_limit_bytes
          AND is_active = 1
    ''')
    
    overlimit = c.fetchall()
    
    for (public_key,) in overlimit:
        print(f"Client {public_key} over limit, deactivating")
        c.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (public_key,))
    
    conn.commit()
    conn.close()
    return [pk[0] for pk in overlimit]

def activate_client(public_key: str):
    """Активирует клиента"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE clients SET is_active = 1 WHERE public_key = ?', (public_key,))
    conn.commit()
    conn.close()

def deactivate_client(public_key: str):
    """Деактивирует клиента"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (public_key,))
    conn.commit()
    conn.close()

def reset_traffic(public_key: str):
    """Сбрасывает счётчик трафика"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE clients SET traffic_used_bytes = 0 WHERE public_key = ?', (public_key,))
    conn.commit()
    conn.close()

def get_all_clients(server_id: Optional[int] = None):
    """Получает всех клиентов из БД, опционально фильтруя по server_id"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    if server_id:
        c.execute('''
            SELECT public_key, name, ip, traffic_limit_bytes, traffic_used_bytes, is_active, server_id, server_name
            FROM clients
            WHERE server_id = ?
            ORDER BY created_at DESC
        ''', (server_id,))
    else:
        c.execute('''
            SELECT public_key, name, ip, traffic_limit_bytes, traffic_used_bytes, is_active, server_id, server_name
            FROM clients
            ORDER BY created_at DESC
        ''')
    
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "public_key": row[0],
            "name": row[1],
            "ip": row[2],
            "limit": row[3],
            "used": row[4],
            "is_active": bool(row[5]),
            "server_id": row[6],
            "server_name": row[7]
        }
        for row in rows
    ]

# ========== USERS MANAGEMENT ==========

def get_all_users():
    """Получает всех пользователей"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, username, role, created_at FROM users ORDER BY created_at DESC')
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "id": row[0],
            "username": row[1],
            "role": row[2],
            "created_at": row[3]
        }
        for row in rows
    ]

def create_user(username: str, password: str, role: str = "user"):
    """Создаёт нового пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                 (username, password, role))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def update_user(user_id: int, username: str = None, password: str = None, role: str = None):
    """Обновляет пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    if username:
        c.execute('UPDATE users SET username = ? WHERE id = ?', (username, user_id))
    if password:
        c.execute('UPDATE users SET password = ? WHERE id = ?', (password, user_id))
    if role:
        c.execute('UPDATE users SET role = ? WHERE id = ?', (role, user_id))
    
    conn.commit()
    conn.close()

def delete_user(user_id: int):
    """Удаляет пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_user_by_username(username: str):
    """Получает пользователя по имени"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT username, password, role FROM users WHERE username = ?', (username,))
    result = c.fetchone()
    conn.close()
    
    if result:
        return {
            "username": result[0],
            "password": result[1],
            "role": result[2]
        }
    return None

# ========== SERVERS MANAGEMENT ==========

def add_server(server_data: dict) -> int:
    """
    Добавляет новый сервер.
    server_data должен содержать:
        - name: str
        - host: str (для remote) или 'localhost' для local
        - port: int (по умолчанию 22)
        - username: str
        - auth_type: 'local', 'password', 'key', 'key+sudo'
        - password: str (если нужен для SSH или sudo)
        - private_key: str (если нужен)
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO servers (
            name, host, port, username, auth_type, 
            password, private_key, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        server_data.get('name'),
        server_data.get('host', 'localhost'),
        server_data.get('port', 22),
        server_data.get('username'),
        server_data.get('auth_type', 'password'),
        server_data.get('password', ''),
        server_data.get('private_key', ''),
        1  # is_active по умолчанию
    ))
    
    server_id = c.lastrowid
    conn.commit()
    conn.close()
    return server_id

def get_server(server_id: int) -> Optional[Dict]:
    """Получает данные сервера по ID"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, name, host, port, username, auth_type, 
               password, private_key, is_active, created_at
        FROM servers WHERE id = ?
    ''', (server_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            "id": row[0],
            "name": row[1],
            "host": row[2],
            "port": row[3],
            "username": row[4],
            "auth_type": row[5],
            "password": row[6],
            "private_key": row[7],
            "is_active": bool(row[8]),
            "created_at": row[9]
        }
    return None

def get_all_servers() -> List[Dict]:
    """Получает список всех серверов (без sensitive данных)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, name, host, port, username, auth_type, 
               is_active, created_at
        FROM servers ORDER BY id ASC
    ''')
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "id": row[0],
            "name": row[1],
            "host": row[2],
            "port": row[3],
            "username": row[4],
            "auth_type": row[5],
            "is_active": bool(row[6]),
            "created_at": row[7]
        }
        for row in rows
    ]

def update_server(server_id: int, server_data: dict):
    """Обновляет данные сервера"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Строим динамический запрос только для переданных полей
    fields = []
    values = []
    
    for key in ['name', 'host', 'port', 'username', 'auth_type', 
                'password', 'private_key', 'is_active']:
        if key in server_data:
            fields.append(f"{key} = ?")
            values.append(server_data[key])
    
    if not fields:
        conn.close()
        return
    
    values.append(server_id)
    query = f"UPDATE servers SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    
    c.execute(query, values)
    conn.commit()
    conn.close()

def delete_server(server_id: int):
    """Удаляет сервер"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Сначала проверяем, есть ли клиенты на этом сервере
    c.execute('SELECT COUNT(*) FROM clients WHERE server_id = ?', (server_id,))
    count = c.fetchone()[0]
    
    if count > 0:
        conn.close()
        raise Exception(f"Cannot delete server with {count} active clients")
    
    c.execute('DELETE FROM servers WHERE id = ?', (server_id,))
    conn.commit()
    conn.close()

def get_servers_for_dropdown() -> List[Dict]:
    """Получает упрощённый список серверов для выпадающего списка"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, name, host FROM servers WHERE is_active = 1 ORDER BY name')
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "id": row[0],
            "name": row[1],
            "host": row[2]
        }
        for row in rows
    ]





# Инициализация при импорте
init_db()