import sqlite3
import os
from datetime import datetime

DB_PATH = "/app/data/amnezia.db"

def init_db():
    """Создаёт таблицы если их нет"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Таблица клиентов
    c.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            public_key TEXT PRIMARY KEY,
            name TEXT,
            ip TEXT,
            traffic_limit_bytes INTEGER,  -- NULL = без лимита
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
    
    conn.commit()
    conn.close()

def get_client(public_key: str):
    """Получает данные клиента"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT name, ip, traffic_limit_bytes, traffic_used_bytes, is_active FROM clients WHERE public_key = ?', 
              (public_key,))
    result = c.fetchone()
    conn.close()
    
    if result:
        return {
            "name": result[0],
            "ip": result[1],
            "limit": result[2],
            "used": result[3],
            "is_active": bool(result[4])
        }
    return None

def create_client(public_key: str, name: str, ip: str):
    """Создаёт запись о клиенте"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO clients (public_key, name, ip)
        VALUES (?, ?, ?)
    ''', (public_key, name, ip))
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

def update_traffic_usage(public_key: str, received: int, sent: int, awg_manager=None):
    """Обновляет использованный трафик и блокирует при превышении"""
    print(f"\n=== update_traffic_usage called for {public_key} ===")
    print(f"Received: {received}, Sent: {sent}, Total: {received + sent}")
    
    total = received + sent
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Получаем текущие данные клиента
    c.execute('SELECT traffic_limit_bytes, traffic_used_bytes, is_active FROM clients WHERE public_key = ?', (public_key,))
    row = c.fetchone()
    
    if not row:
        print(f"Client {public_key} not found in DB")
        conn.close()
        return
    
    limit_bytes, used_bytes, is_active = row
    print(f"DB data - limit: {limit_bytes}, used: {used_bytes}, is_active: {is_active}")
    
    # Обновляем использованный трафик
    new_used = max(total, used_bytes)
    print(f"New used value: {new_used}")
    
    c.execute('UPDATE clients SET traffic_used_bytes = ? WHERE public_key = ?', (new_used, public_key))
    c.execute('INSERT INTO traffic_history (public_key, bytes_received, bytes_sent, total_bytes) VALUES (?, ?, ?, ?)',
              (public_key, received, sent, total))
    
    conn.commit()
    
    if limit_bytes and new_used > limit_bytes and is_active:
        print(f"🚨 LIMIT EXCEEDED! {new_used} > {limit_bytes}")
        
        # Сначала вызываем блокировку
        if awg_manager:
            print(f"Calling block_client for {public_key}")
            awg_manager.block_client(public_key)
        
        # Потом обновляем БД
        c.execute('UPDATE clients SET is_active = 0 WHERE public_key = ?', (public_key,))
        conn.commit()
        conn.close()
        return
    
    print("Limit not exceeded or no limit set")
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

def reset_traffic_for_limit(public_key: str):
    """Сбрасывает счётчик трафика при установке нового лимита"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE clients SET traffic_used_bytes = 0 WHERE public_key = ?', (public_key,))
    conn.commit()
    conn.close()

# Инициализация при импорте
init_db()