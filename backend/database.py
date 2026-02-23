import sqlite3
import os
from datetime import datetime

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
            name TEXT NOT NULL,              -- например, "Finland", "Germany"
            host TEXT NOT NULL,               -- IP или домен
            port INTEGER DEFAULT 22,
            user TEXT DEFAULT 'root',
            ssh_key_path TEXT,                -- путь к ключу на центральном сервере
            status TEXT DEFAULT 'active',      -- active/offline/maintenance
            location TEXT,                     -- страна/город для отображения
            clients_count INTEGER DEFAULT 0,   -- кэш для удобства
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Добавляем поле server_id в таблицу clients, если его нет - ВРЕМЕННО
    try:
        c.execute("ALTER TABLE clients ADD COLUMN server_id INTEGER REFERENCES servers(id);")
        print("✅ Added server_id column to clients table")
    except sqlite3.OperationalError:
        pass

    # Добавляем локальный сервер по умолчанию, если серверов ещё нет
    c.execute("SELECT COUNT(*) FROM servers")
    if c.fetchone()[0] == 0:
        c.execute('''
            INSERT INTO servers (name, host, port, user, ssh_key_path, status, location)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ("Local server", "localhost", 22, "root", "", "active", "local"))
        conn.commit()
        print("✅ Added default local server")

    # Привязываем существующих клиентов к локальному серверу, если они не привязаны
    c.execute('''
        UPDATE clients 
        SET server_id = (SELECT id FROM servers WHERE name = 'Local server' LIMIT 1)
        WHERE server_id IS NULL
    ''')
    
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

def create_client(public_key: str, name: str, ip: str, private_key: str = "", server_id: int = None):
    """Создаёт запись о клиенте"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Добавляем server_id в запрос
    c.execute('''
        INSERT INTO clients (public_key, name, ip, private_key, server_id, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(public_key) DO UPDATE SET
            name=excluded.name,
            ip=excluded.ip,
            server_id=excluded.server_id,
            private_key=CASE WHEN excluded.private_key IS NULL OR excluded.private_key = '' THEN clients.private_key ELSE excluded.private_key END,
            updated_at=CURRENT_TIMESTAMP
    ''', (public_key, name, ip, private_key, server_id))
    
    conn.commit()
    conn.close()

def delete_client(public_key: str):
    """Удаляет клиента"""
    # Сначала получаем server_id клиента
    client = get_client(public_key)
    server_id = client.get("server_id") if client else None
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM clients WHERE public_key = ?', (public_key,))
    conn.commit()
    conn.close()
    
    # Уменьшаем счётчик на сервере
    if server_id:
        decrement_server_clients(server_id)

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

def get_all_clients():
    """Получает всех клиентов из БД"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT public_key, name, ip, traffic_limit_bytes, traffic_used_bytes, is_active
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
            "is_active": bool(row[5])
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

def get_all_servers():
    """Получает список всех серверов"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, name, host, port, user, ssh_key_path, status, location, clients_count, created_at
        FROM servers
        ORDER BY created_at DESC
    ''')
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "id": row[0],
            "name": row[1],
            "host": row[2],
            "port": row[3],
            "user": row[4],
            "ssh_key_path": row[5],
            "status": row[6],
            "location": row[7],
            "clients_count": row[8],
            "created_at": row[9]
        }
        for row in rows
    ]

def get_server(server_id: int):
    """Получает данные сервера по ID"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, name, host, port, user, ssh_key_path, status, location, clients_count, created_at
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
            "user": row[4],
            "ssh_key_path": row[5],
            "status": row[6],
            "location": row[7],
            "clients_count": row[8],
            "created_at": row[9]
        }
    return None

def create_server(server_data: dict):
    """Создаёт новый сервер"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO servers (name, host, port, user, ssh_key_path, status, location)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        server_data.get("name"),
        server_data.get("host"),
        server_data.get("port", 22),
        server_data.get("user", "root"),
        server_data.get("ssh_key_path"),
        server_data.get("status", "active"),
        server_data.get("location", "")
    ))
    
    server_id = c.lastrowid
    conn.commit()
    conn.close()
    return server_id

def update_server(server_id: int, server_data: dict):
    """Обновляет данные сервера"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    updates = []
    values = []
    
    for field in ["name", "host", "port", "user", "ssh_key_path", "status", "location"]:
        if field in server_data:
            updates.append(f"{field} = ?")
            values.append(server_data[field])
    
    if not updates:
        conn.close()
        return
    
    values.append(server_id)
    c.execute(f'''
        UPDATE servers 
        SET {', '.join(updates)}
        WHERE id = ?
    ''', values)
    
    conn.commit()
    conn.close()

def delete_server(server_id: int):
    """Удаляет сервер (только если нет клиентов)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Проверяем, есть ли клиенты на этом сервере
    c.execute('SELECT COUNT(*) FROM clients WHERE server_id = ?', (server_id,))
    count = c.fetchone()[0]
    
    if count > 0:
        conn.close()
        raise Exception(f"Cannot delete server with {count} clients")
    
    c.execute('DELETE FROM servers WHERE id = ?', (server_id,))
    conn.commit()
    conn.close()

def increment_server_clients(server_id: int):
    """Увеличивает счётчик клиентов на сервере"""
    if not server_id:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        UPDATE servers 
        SET clients_count = clients_count + 1 
        WHERE id = ?
    ''', (server_id,))
    conn.commit()
    conn.close()

def decrement_server_clients(server_id: int):
    """Уменьшает счётчик клиентов на сервере"""
    if not server_id:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        UPDATE servers 
        SET clients_count = clients_count - 1 
        WHERE id = ?
    ''', (server_id,))
    conn.commit()
    conn.close()

# Инициализация при импорте
init_db()