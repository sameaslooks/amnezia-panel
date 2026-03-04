import os
import logging
import requests
import urllib.parse
import math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PANEL_URL = os.getenv("PANEL_URL", "http://amnezia-panel:8000")
ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "").split(",") if id]
BOT_USERNAME = os.getenv("BOT_USERNAME", "admin")
BOT_PASSWORD = os.getenv("BOT_PASSWORD", "admin123")

# Хранилище состояний
user_sessions = {}  # {chat_id: {'state': 'awaiting_client_name', 'user_id': int, 'server_id': int}}
_cached_token = None
last_message_id = {}  # {chat_id: message_id}
servers_cache = []  # Кэш списка серверов
users_cache = []    # Кэш списка пользователей

def get_panel_token():
    global _cached_token
    if _cached_token:
        return _cached_token
    
    try:
        response = requests.post(
            f"{PANEL_URL}/api/login",
            json={"username": BOT_USERNAME, "password": BOT_PASSWORD},
            timeout=10
        )
        if response.status_code == 200:
            _cached_token = response.json()["access_token"]
            return _cached_token
        return None
    except Exception as e:
        logger.error(f"Login error: {e}")
        return None

def get_headers():
    token = get_panel_token()
    if not token:
        raise Exception("Failed to authenticate with panel")
    return {"Authorization": f"Bearer {token}"}

def format_bytes(bytes_val):
    if not bytes_val or bytes_val == 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    k = 1024
    i = int(math.log(bytes_val) / math.log(k))
    return f"{bytes_val / (k ** i):.2f} {units[i]}"

def format_handshake(handshake_str):
    """Форматирует время handshake для отображения"""
    if not handshake_str or handshake_str == 'Never':
        return '🤝 Never'
    return f"🤝 {handshake_str}"

async def update_or_send(chat_id, text, reply_markup=None, context=None, is_new=False):
    """Если is_new=True → новое сообщение, иначе редактируем последнее"""
    if not is_new and chat_id in last_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_message_id[chat_id],
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            logger.warning(f"Failed to edit message: {e}")
            try:
                await context.bot.delete_message(chat_id, last_message_id[chat_id])
            except:
                pass
            del last_message_id[chat_id]
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    last_message_id[chat_id] = msg.message_id

async def fetch_servers():
    """Получает список серверов и кэширует"""
    global servers_cache
    try:
        headers = get_headers()
        response = requests.get(f"{PANEL_URL}/api/servers", headers=headers, timeout=10)
        if response.status_code == 200:
            servers_cache = response.json()
            return servers_cache
    except Exception as e:
        logger.error(f"Failed to fetch servers: {e}")
    return servers_cache

async def fetch_users():
    """Получает список пользователей и кэширует"""
    global users_cache
    try:
        headers = get_headers()
        response = requests.get(f"{PANEL_URL}/api/users", headers=headers, timeout=10)
        if response.status_code == 200:
            users_cache = response.json()
            return users_cache
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
    return users_cache

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return
    
    # Обновляем кэши при старте
    await fetch_servers()
    await fetch_users()
    
    keyboard = [
        [InlineKeyboardButton("📋 Список клиентов", callback_data="list_clients")],
        [InlineKeyboardButton("➕ Создать клиента", callback_data="create_client_start")],
        [InlineKeyboardButton("🖥️ Серверы", callback_data="list_servers")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="list_users")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "👋 **Amnezia Panel Bot**\n\nУправляйте VPN-клиентами прямо из Telegram.\n\nВыберите действие:"
    
    await update_or_send(chat_id, text, reply_markup, context, is_new=True)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ Нет доступа")
        return
    
    last_message_id[chat_id] = query.message.message_id
    
    # Обработка callback_data
    if query.data == "list_clients":
        await list_clients(chat_id, context)
    elif query.data == "create_client_start":
        await create_client_start(chat_id, context)
    elif query.data == "list_servers":
        await list_servers(chat_id, context)
    elif query.data == "list_users":
        await list_users(chat_id, context)
    elif query.data == "help":
        await show_help(chat_id, context)
    elif query.data == "back":
        await start(update, context)
    elif query.data.startswith("client_"):
        public_key = query.data.replace("client_", "")
        await show_client_details(chat_id, public_key, context)
    elif query.data.startswith("get_config_"):
        parts = query.data.replace("get_config_", "").split("|")
        public_key = parts[0]
        server_id = int(parts[1]) if len(parts) > 1 else 1
        await get_client_config(chat_id, public_key, server_id, context)
    elif query.data.startswith("delete_client_"):
        public_key = query.data.replace("delete_client_", "")
        await delete_client_confirm(chat_id, public_key, context)
    elif query.data.startswith("confirm_delete_"):
        public_key = query.data.replace("confirm_delete_", "")
        await confirm_delete(chat_id, public_key, context)
    elif query.data.startswith("server_"):
        server_id = int(query.data.replace("server_", ""))
        await show_server_details(chat_id, server_id, context)
    elif query.data.startswith("user_"):
        user_id_data = int(query.data.replace("user_", ""))
        await show_user_details(chat_id, user_id_data, context)
    elif query.data.startswith("create_client_server_"):
        # Выбор сервера для создания клиента
        parts = query.data.replace("create_client_server_", "").split("_")
        server_id = int(parts[0])
        user_id_data = int(parts[1]) if len(parts) > 1 else None
        await create_client_prompt(chat_id, server_id, user_id_data, context)

async def list_clients(chat_id, context):
    """Показать список клиентов"""
    try:
        headers = get_headers()
        
        limits_res = requests.get(f"{PANEL_URL}/api/limits", headers=headers, timeout=10)
        traffic_res = requests.get(f"{PANEL_URL}/api/traffic", headers=headers, timeout=10)
        
        if limits_res.status_code != 200:
            await update_or_send(chat_id, "❌ Ошибка получения списка клиентов", None, context)
            return
        
        clients = limits_res.json()
        traffic = traffic_res.json() if traffic_res.status_code == 200 else []
        
        traffic_map = {t["public_key"]: t for t in traffic}
        
        if not clients:
            await update_or_send(chat_id, "📭 Нет клиентов.", None, context)
            return
        
        # Группируем по пользователям
        users_dict = {}
        for client in clients:
            user_id = client["user_id"]
            if user_id not in users_dict:
                users_dict[user_id] = {
                    "username": client.get("username", f"User {user_id}"),
                    "clients": []
                }
            users_dict[user_id]["clients"].append(client)
        
        text = "**📋 Клиенты по пользователям:**\n\n"
        keyboard = []
        
        for user_id, user_data in users_dict.items():
            text += f"👤 **{user_data['username']}**\n"
            for client in user_data["clients"][:3]:  # Показываем первых 3 клиента пользователя
                traffic_data = traffic_map.get(client["public_key"], {})
                status = "🟢" if client.get("is_active", True) else "🔴"
                used = format_bytes(client.get("used", 0))
                limit = format_bytes(client.get("limit", 0)) if client.get("limit") else "∞"
                handshake = format_handshake(traffic_data.get("latest_handshake", "Never"))
                
                text += f"  {status} **{client['name']}** — {used}/{limit} {handshake[:20]}\n"
            
            if len(user_data["clients"]) > 3:
                text += f"  ... и ещё {len(user_data['clients']) - 3}\n"
            text += "\n"
            
            # Кнопка для просмотра всех клиентов пользователя
            keyboard.append([InlineKeyboardButton(
                f"👤 {user_data['username']} ({len(user_data['clients'])})", 
                callback_data=f"user_{user_id}"
            )])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in list_clients: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def show_client_details(chat_id, public_key, context):
    """Показать детали клиента"""
    try:
        headers = get_headers()
        
        limits_res = requests.get(f"{PANEL_URL}/api/limits", headers=headers, timeout=10)
        traffic_res = requests.get(f"{PANEL_URL}/api/traffic", headers=headers, timeout=10)
        
        if limits_res.status_code != 200:
            await update_or_send(chat_id, "❌ Ошибка получения данных", None, context)
            return
        
        clients = limits_res.json()
        traffic = traffic_res.json() if traffic_res.status_code == 200 else []
        
        client = next((c for c in clients if c["public_key"] == public_key), None)
        if not client:
            await update_or_send(chat_id, "❌ Клиент не найден", None, context)
            return
        
        traffic_data = next((t for t in traffic if t["public_key"] == public_key), {})
        
        status = "🟢 Активен" if client.get("is_active", True) else "🔴 Заблокирован"
        used = format_bytes(client.get("used", 0))
        limit = format_bytes(client.get("limit", 0)) if client.get("limit") else "Без лимита"
        percent = f" ({client.get('used', 0) / client.get('limit', 1) * 100:.1f}%)" if client.get("limit") else ""
        traffic_str = traffic_data.get("transfer", "0 B")
        handshake = traffic_data.get("latest_handshake", "Never")
        
        text = (
            f"**👤 {client['name']}**\n\n"
            f"**👥 Владелец:** {client.get('username', 'Unknown')}\n"
            f"**🖥️ Сервер:** {client.get('server_name', 'local')}\n"
            f"**🌐 IP:** `{client['ip']}`\n"
            f"**🔑 Публичный ключ:**\n`{client['public_key'][:30]}...`\n\n"
            f"**📊 Статус:** {status}\n"
            f"**📈 Текущий трафик:** {traffic_str}\n"
            f"**💾 Использовано:** {used}{percent}\n"
            f"**🎯 Лимит:** {limit}\n"
            f"**📅 Истекает:** {client.get('expiry_date', 'Never')}\n"
            f"**🤝 Последнее рукопожатие:** {handshake}\n"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🔗 Ссылка", callback_data=f"get_config_{public_key}|{client['server_id']}"),
                InlineKeyboardButton("❌ Удалить", callback_data=f"delete_client_{public_key}")
            ],
            [InlineKeyboardButton("◀️ К списку", callback_data="list_clients")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in show_client_details: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def create_client_start(chat_id, context):
    """Начало создания клиента - выбор пользователя"""
    try:
        users = await fetch_users()
        if not users:
            await update_or_send(chat_id, "❌ Нет пользователей. Создайте пользователя в панели.", None, context)
            return
        
        text = "👤 **Выберите пользователя:**"
        keyboard = []
        
        for user in users:
            if user.get('role') == 'admin':
                continue  # Пропускаем админов, им не нужны клиенты
            keyboard.append([InlineKeyboardButton(
                f"{user['username']} (лимит: {user.get('config_limit', 1)})",
                callback_data=f"create_client_user_{user['id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        user_sessions[chat_id] = {"state": "selecting_user"}
        
    except Exception as e:
        logger.error(f"Error in create_client_start: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def create_client_user_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора пользователя"""
    query = update.callback_query
    await query.answer()
    
    user_id_data = int(query.data.replace("create_client_user_", ""))
    chat_id = update.effective_chat.id
    
    # Переходим к выбору сервера
    servers = await fetch_servers()
    active_servers = [s for s in servers if s.get('is_active')]
    
    if not active_servers:
        await update_or_send(chat_id, "❌ Нет активных серверов.", None, context)
        return
    
    text = "🖥️ **Выберите сервер:**"
    keyboard = []
    
    for server in active_servers:
        keyboard.append([InlineKeyboardButton(
            server['name'],
            callback_data=f"create_client_server_{server['id']}_{user_id_data}"
        )])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="create_client_start")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update_or_send(chat_id, text, reply_markup, context)

async def create_client_prompt(chat_id, server_id, user_id, context):
    """Запросить имя клиента"""
    text = "✏️ **Создание нового клиента**\n\nВведите имя для клиента (например, 'iPhone 15'):"
    keyboard = [[InlineKeyboardButton("◀️ Отмена", callback_data="list_clients")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update_or_send(chat_id, text, reply_markup, context)
    user_sessions[chat_id] = {
        "state": "awaiting_client_name",
        "user_id": user_id,
        "server_id": server_id
    }

async def handle_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка введённого имени клиента"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS or chat_id not in user_sessions:
        return
    
    session = user_sessions[chat_id]
    if session.get("state") != "awaiting_client_name":
        return
    
    client_name = update.message.text.strip()
    if not client_name:
        await context.bot.send_message(chat_id, "❌ Имя не может быть пустым.")
        return
    
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        headers = get_headers()
        server_id = session.get("server_id", 1)
        target_user_id = session.get("user_id")
        
        # Проверяем лимит конфигов
        users = await fetch_users()
        user = next((u for u in users if u['id'] == target_user_id), None)
        if user and user.get('role') != 'admin':
            clients_res = requests.get(f"{PANEL_URL}/api/limits", headers=headers, timeout=10)
            if clients_res.status_code == 200:
                user_clients = [c for c in clients_res.json() if c.get('user_id') == target_user_id]
                if len(user_clients) >= user.get('config_limit', 1):
                    await context.bot.send_message(
                        chat_id, 
                        f"❌ Пользователь достиг лимита клиентов ({user.get('config_limit', 1)})"
                    )
                    user_sessions.pop(chat_id, None)
                    return
        
        # Создаём клиента
        url = f"{PANEL_URL}/api/clients?server_id={server_id}"
        response = requests.post(
            url,
            headers=headers,
            json={"name": client_name, "user_id": target_user_id},
            timeout=10
        )
        
        if response.status_code != 200:
            await context.bot.send_message(
                chat_id, 
                f"❌ Ошибка создания клиента: {response.text}"
            )
            return
        
        client = response.json()
        
        # Получаем VPN ссылку
        encoded_key = urllib.parse.quote(client['public_key'])
        link_response = requests.get(
            f"{PANEL_URL}/api/generate-link?public_key={encoded_key}&server_id={server_id}",
            headers=headers,
            timeout=10
        )
        
        vpn_link = link_response.json()["link"] if link_response.status_code == 200 else "Не удалось получить ссылку"
        
        text = (
            f"✅ **Клиент создан!**\n\n"
            f"**Имя:** {client['name']}\n"
            f"**IP:** {client['ip']}\n"
            f"**Сервер:** {client.get('server_name', 'local')}\n\n"
            f"**🔗 VPN ссылка:**\n`{vpn_link}`"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ К списку", callback_data="list_clients")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id, 
            text, 
            reply_markup=reply_markup, 
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error creating client: {e}")
        await context.bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")
    
    user_sessions.pop(chat_id, None)

async def get_client_config(chat_id, public_key, server_id, context):
    """Получить конфиг клиента"""
    try:
        headers = get_headers()
        encoded_key = urllib.parse.quote(public_key)
        
        response = requests.get(
            f"{PANEL_URL}/api/generate-link?public_key={encoded_key}&server_id={server_id}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code != 200:
            await update_or_send(chat_id, "❌ Ошибка получения конфига", None, context)
            return
        
        data = response.json()
        vpn_link = data["link"]
        
        text = f"🔗 **VPN ссылка:**\n`{vpn_link}`"
        
        keyboard = [
            [InlineKeyboardButton("◀️ Назад", callback_data=f"client_{public_key}")],
            [InlineKeyboardButton("📋 К списку", callback_data="list_clients")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in get_client_config: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def delete_client_confirm(chat_id, public_key, context):
    """Подтверждение удаления"""
    text = "⚠️ **Вы уверены, что хотите удалить этого клиента?**\nЭто действие нельзя отменить."
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{public_key}"),
            InlineKeyboardButton("❌ Нет", callback_data=f"client_{public_key}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update_or_send(chat_id, text, reply_markup, context)

async def confirm_delete(chat_id, public_key, context):
    """Реальное удаление клиента"""
    try:
        headers = get_headers()
        encoded_key = urllib.parse.quote(public_key)
        
        response = requests.delete(
            f"{PANEL_URL}/api/clients?public_key={encoded_key}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code != 200:
            await update_or_send(chat_id, "❌ Ошибка удаления клиента", None, context)
            return
        
        text = "✅ Клиент успешно удалён!"
        keyboard = [[InlineKeyboardButton("◀️ К списку", callback_data="list_clients")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in delete_client: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def list_servers(chat_id, context):
    """Показать список серверов"""
    try:
        servers = await fetch_servers()
        
        text = "**🖥️ Серверы:**\n\n"
        keyboard = []
        
        for server in servers:
            status = "🟢 Активен" if server.get('is_active') else "🔴 Отключён"
            text += f"**{server['name']}**\n"
            text += f"  📍 {server.get('host', 'local')} ({server['auth_type']})\n"
            text += f"  📊 Статус: {status}\n\n"
            
            keyboard.append([InlineKeyboardButton(
                f"📊 {server['name']}",
                callback_data=f"server_{server['id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in list_servers: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def show_server_details(chat_id, server_id, context):
    """Показать детали сервера"""
    try:
        headers = get_headers()
        
        # Получаем статус сервера
        status_res = requests.get(
            f"{PANEL_URL}/api/servers/{server_id}/status",
            headers=headers,
            timeout=10
        )
        
        servers = await fetch_servers()
        server = next((s for s in servers if s['id'] == server_id), None)
        
        if not server:
            await update_or_send(chat_id, "❌ Сервер не найден", None, context)
            return
        
        status = status_res.json() if status_res.status_code == 200 else {}
        
        online_status = "🟢 Online" if status.get('online') else "🔴 Offline"
        container_status = "✅ Запущен" if status.get('container_running') else "⛔ Остановлен"
        
        text = (
            f"**🖥️ {server['name']}**\n\n"
            f"**📍 Хост:** {server.get('host', 'local')}\n"
            f"**🔑 Тип:** {server['auth_type']}\n"
            f"**📊 Статус сервера:** {online_status}\n"
            f"**📦 Контейнер:** {container_status}\n"
            f"**🔧 Версия:** {status.get('version', 'unknown')}\n"
            f"**👥 Клиентов:** {status.get('clients_count', 0)}\n"
        )
        
        if status.get('errors'):
            text += "\n**⚠️ Ошибки:**\n"
            for err in status['errors'][:3]:
                text += f"  • {err}\n"
        
        keyboard = [
            [InlineKeyboardButton("◀️ К списку", callback_data="list_servers")],
            [InlineKeyboardButton("📋 На главную", callback_data="back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in show_server_details: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def list_users(chat_id, context):
    """Показать список пользователей"""
    try:
        users = await fetch_users()
        
        text = "**👥 Пользователи:**\n\n"
        keyboard = []
        
        for user in users:
            role_emoji = "👑" if user.get('role') == 'admin' else "👤"
            limit = format_bytes(user.get('traffic_limit_bytes', 0)) if user.get('traffic_limit_bytes') else "∞"
            used = format_bytes(user.get('traffic_used_bytes', 0))
            expiry = user.get('expiry_date', 'Never')[:10] if user.get('expiry_date') else 'Never'
            
            text += f"{role_emoji} **{user['username']}**\n"
            text += f"  📊 {used} / {limit}\n"
            text += f"  📅 {expiry}\n\n"
            
            if user.get('role') != 'admin':
                keyboard.append([InlineKeyboardButton(
                    f"👤 {user['username']}",
                    callback_data=f"user_{user['id']}"
                )])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in list_users: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def show_user_details(chat_id, user_id_data, context):
    """Показать детали пользователя и его клиентов"""
    try:
        headers = get_headers()
        
        users = await fetch_users()
        user = next((u for u in users if u['id'] == user_id_data), None)
        
        if not user:
            await update_or_send(chat_id, "❌ Пользователь не найден", None, context)
            return
        
        # Получаем клиентов пользователя
        limits_res = requests.get(f"{PANEL_URL}/api/limits", headers=headers, timeout=10)
        traffic_res = requests.get(f"{PANEL_URL}/api/traffic", headers=headers, timeout=10)
        
        clients = []
        if limits_res.status_code == 200:
            all_clients = limits_res.json()
            clients = [c for c in all_clients if c.get('user_id') == user_id_data]
        
        traffic = traffic_res.json() if traffic_res.status_code == 200 else []
        traffic_map = {t["public_key"]: t for t in traffic}
        
        limit = format_bytes(user.get('traffic_limit_bytes', 0)) if user.get('traffic_limit_bytes') else "∞"
        used = format_bytes(user.get('traffic_used_bytes', 0))
        percent = f" ({user.get('traffic_used_bytes', 0) / user.get('traffic_limit_bytes', 1) * 100:.1f}%)" if user.get('traffic_limit_bytes') else ""
        
        text = (
            f"**👤 {user['username']}**\n\n"
            f"**📊 Трафик:** {used}{percent} / {limit}\n"
            f"**📅 Истекает:** {user.get('expiry_date', 'Never')}\n"
            f"**📱 Клиентов:** {len(clients)} / {user.get('config_limit', 1)}\n\n"
            f"**📋 Клиенты:**\n"
        )
        
        keyboard = []
        
        for client in clients[:5]:  # Показываем первых 5 клиентов
            traffic_data = traffic_map.get(client["public_key"], {})
            status = "🟢" if client.get("is_active", True) else "🔴"
            used_client = format_bytes(client.get("used", 0))
            handshake = format_handshake(traffic_data.get("latest_handshake", "Never"))
            
            text += f"{status} **{client['name']}** — {used_client} {handshake[:20]}\n"
            
            keyboard.append([InlineKeyboardButton(
                f"🔑 {client['name']}",
                callback_data=f"client_{client['public_key']}"
            )])
        
        if len(clients) > 5:
            text += f"... и ещё {len(clients) - 5}\n"
        
        if clients:
            keyboard.append([InlineKeyboardButton(
                "➕ Создать клиента",
                callback_data=f"create_client_server_{clients[0]['server_id']}_{user_id_data}"
            )])
        
        keyboard.append([InlineKeyboardButton("◀️ К списку пользователей", callback_data="list_users")])
        keyboard.append([InlineKeyboardButton("📋 На главную", callback_data="back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in show_user_details: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def show_help(chat_id, context):
    """Помощь"""
    text = (
        "**🤖 Amnezia Panel Bot**\n\n"
        "**Доступные команды:**\n"
        "/start - Главное меню\n\n"
        "**Возможности:**\n"
        "• Просмотр клиентов по пользователям\n"
        "• Создание клиентов (выбор пользователя и сервера)\n"
        "• Получение VPN-ссылок\n"
        "• Удаление клиентов\n"
        "• Просмотр статуса серверов\n"
        "• Детальная статистика по каждому клиенту\n\n"
        "**Лимиты:**\n"
        "• При создании проверяется лимит конфигов пользователя\n"
        "• Отображается использованный трафик и лимиты"
    )
    
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update_or_send(chat_id, text, reply_markup, context)

def main():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return
    
    application = Application.builder().token(TOKEN).build()
    
    # Хендлеры
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CallbackQueryHandler(
        create_client_user_selected, 
        pattern="^create_client_user_"
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_name))
    
    logger.info("Bot started!")
    application.run_polling()

if __name__ == "__main__":
    main()