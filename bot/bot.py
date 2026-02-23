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
user_sessions = {}
_cached_token = None
last_message_id = {}  # {chat_id: message_id}

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
            # Если не получилось — удаляем и создадим новое
            try:
                await context.bot.delete_message(chat_id, last_message_id[chat_id])
            except:
                pass
            del last_message_id[chat_id]
    
    # Отправляем новое сообщение
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    last_message_id[chat_id] = msg.message_id

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📋 Список клиентов", callback_data="list_clients")],
        [InlineKeyboardButton("➕ Создать клиента", callback_data="create_client")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "👋 **Amnezia Panel Bot**\n\nУправляйте VPN-клиентами прямо из Telegram.\n\nВыберите действие:"
    
    # При /start всегда новое сообщение
    await update_or_send(chat_id, text, reply_markup, context, is_new=True)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки — всегда редактируем существующее"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ Нет доступа")
        return
    
    # Сохраняем ID сообщения, которое будем редактировать
    last_message_id[chat_id] = query.message.message_id
    
    if query.data == "list_clients":
        await list_clients(chat_id, context)
    elif query.data == "create_client":
        await create_client_prompt(chat_id, context)
    elif query.data == "help":
        await show_help(chat_id, context)
    elif query.data == "back":
        await start(update, context)
    elif query.data.startswith("client_"):
        public_key = query.data.replace("client_", "")
        await show_client_details(chat_id, public_key, context)
    elif query.data.startswith("get_config_"):
        public_key = query.data.replace("get_config_", "")
        await get_client_config(chat_id, public_key, context)
    elif query.data.startswith("delete_client_"):
        public_key = query.data.replace("delete_client_", "")
        await delete_client_confirm(chat_id, public_key, context)
    elif query.data.startswith("confirm_delete_"):
        public_key = query.data.replace("confirm_delete_", "")
        await confirm_delete(chat_id, public_key, context)

async def list_clients(chat_id, context):
    """Показать список клиентов — редактируем"""
    try:
        headers = get_headers()
        
        clients_res = requests.get(f"{PANEL_URL}/api/clients", headers=headers, timeout=10)
        limits_res = requests.get(f"{PANEL_URL}/api/limits", headers=headers, timeout=10)
        traffic_res = requests.get(f"{PANEL_URL}/api/traffic", headers=headers, timeout=10)
        
        if clients_res.status_code != 200:
            await update_or_send(chat_id, "❌ Ошибка получения списка клиентов", None, context)
            return
        
        clients = clients_res.json()
        limits = limits_res.json() if limits_res.status_code == 200 else []
        traffic = traffic_res.json() if traffic_res.status_code == 200 else []
        
        limits_map = {l["public_key"]: l for l in limits}
        traffic_map = {t["public_key"]: t for t in traffic}
        
        if not clients:
            await update_or_send(chat_id, "📭 У вас пока нет клиентов.", None, context)
            return
        
        text = "**📋 Список клиентов:**\n\n"
        keyboard = []
        
        for client in clients[:5]:
            pk = client["public_key"]
            limit_data = limits_map.get(pk, {})
            traffic_data = traffic_map.get(pk, {})
            
            status = "🟢" if limit_data.get("is_active", True) else "🔴"
            used = format_bytes(limit_data.get("used", 0))
            limit = format_bytes(limit_data.get("limit", 0)) if limit_data.get("limit") else "∞"
            handshake = traffic_data.get("latest_handshake", "Never")[:10] + "..." if len(traffic_data.get("latest_handshake", "")) > 10 else traffic_data.get("latest_handshake", "Never")
            
            text += f"{status} **{client['name']}** — {used}/{limit}\n"
            text += f"IP: `{client['ip']}` 🤝 {handshake}\n\n"
            
            keyboard.append([InlineKeyboardButton(f"🔑 {client['name']}", callback_data=f"client_{pk}")])
        
        if len(clients) > 5:
            text += f"... и ещё {len(clients) - 5} клиентов.\n\n"
        
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in list_clients: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def show_client_details(chat_id, public_key, context):
    """Показать детали клиента — редактируем"""
    try:
        headers = get_headers()
        
        clients_res = requests.get(f"{PANEL_URL}/api/clients", headers=headers, timeout=10)
        limits_res = requests.get(f"{PANEL_URL}/api/limits", headers=headers, timeout=10)
        traffic_res = requests.get(f"{PANEL_URL}/api/traffic", headers=headers, timeout=10)
        
        if clients_res.status_code != 200:
            await update_or_send(chat_id, "❌ Ошибка получения данных", None, context)
            return
        
        clients = clients_res.json()
        limits = limits_res.json() if limits_res.status_code == 200 else []
        traffic = traffic_res.json() if traffic_res.status_code == 200 else []
        
        client = next((c for c in clients if c["public_key"] == public_key), None)
        if not client:
            await update_or_send(chat_id, "❌ Клиент не найден", None, context)
            return
        
        limit_data = next((l for l in limits if l["public_key"] == public_key), {})
        traffic_data = next((t for t in traffic if t["public_key"] == public_key), {})
        
        status = "🟢 Активен" if limit_data.get("is_active", True) else "🔴 Заблокирован"
        used = format_bytes(limit_data.get("used", 0))
        limit = format_bytes(limit_data.get("limit", 0)) if limit_data.get("limit") else "Без лимита"
        percent = f" ({limit_data.get('used', 0) / limit_data.get('limit', 1) * 100:.1f}%)" if limit_data.get("limit") else ""
        traffic_str = traffic_data.get("transfer", "0 B")
        handshake = traffic_data.get("latest_handshake", "Never")
        
        text = (
            f"**👤 {client['name']}**\n\n"
            f"**IP:** `{client['ip']}`\n"
            f"**Публичный ключ:**\n`{client['public_key'][:30]}...`\n\n"
            f"**📊 Статус:** {status}\n"
            f"**📈 Трафик:** {traffic_str}\n"
            f"**💾 Использовано:** {used}{percent}\n"
            f"**🎯 Лимит:** {limit}\n"
            f"**🤝 Последнее рукопожатие:** {handshake}\n"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🔗 Ссылка", callback_data=f"get_config_{public_key}"),
                InlineKeyboardButton("❌ Удалить", callback_data=f"delete_client_{public_key}")
            ],
            [InlineKeyboardButton("◀️ К списку", callback_data="list_clients")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_or_send(chat_id, text, reply_markup, context)
        
    except Exception as e:
        logger.error(f"Error in show_client_details: {e}")
        await update_or_send(chat_id, f"❌ Ошибка: {str(e)}", None, context)

async def create_client_prompt(chat_id, context):
    """Запросить имя клиента — редактируем"""
    text = "✏️ **Создание нового клиента**\n\nВведите имя для клиента (например, 'iPhone 15'):"
    keyboard = [[InlineKeyboardButton("◀️ Отмена", callback_data="list_clients")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update_or_send(chat_id, text, reply_markup, context)
    user_sessions[chat_id] = {"state": "awaiting_client_name"}

async def handle_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка введённого имени клиента — ВСЕГДА НОВОЕ СООБЩЕНИЕ"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS or chat_id not in user_sessions:
        return
    
    if user_sessions[chat_id].get("state") != "awaiting_client_name":
        return
    
    client_name = update.message.text.strip()
    if not client_name:
        await context.bot.send_message(chat_id, "❌ Имя не может быть пустым.")
        return
    
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        headers = get_headers()
        
        response = requests.post(
            f"{PANEL_URL}/api/clients",
            headers=headers,
            json={"name": client_name},
            timeout=10
        )
        
        if response.status_code != 200:
            await context.bot.send_message(chat_id, f"❌ Ошибка создания клиента: {response.text}")
            return
        
        client = response.json()
        
        encoded_key = urllib.parse.quote(client['public_key'])
        link_response = requests.get(
            f"{PANEL_URL}/api/generate-link?public_key={encoded_key}",
            headers=headers,
            timeout=10
        )
        
        vpn_link = link_response.json()["link"] if link_response.status_code == 200 else "Не удалось получить ссылку"
        
        text = (
            f"✅ **Клиент создан!**\n\n"
            f"**Имя:** {client['name']}\n"
            f"**IP:** {client['ip']}\n\n"
            f"**🔗 VPN ссылка:**\n`{vpn_link}`"
        )
        
        keyboard = [[InlineKeyboardButton("◀️ К списку", callback_data="list_clients")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Это новое сообщение
        await context.bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error creating client: {e}")
        await context.bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")
    
    user_sessions.pop(chat_id, None)

async def get_client_config(chat_id, public_key, context):
    """Получить конфиг — редактируем"""
    try:
        headers = get_headers()
        encoded_key = urllib.parse.quote(public_key)
        
        response = requests.get(
            f"{PANEL_URL}/api/generate-link?public_key={encoded_key}",
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
    """Подтверждение удаления — редактируем"""
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
    """Реальное удаление — редактируем"""
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

async def show_help(chat_id, context):
    """Помощь — редактируем"""
    text = (
        "**🤖 Amnezia Panel Bot**\n\n"
        "**Доступные команды:**\n"
        "/start - Главное меню\n\n"
        "**Возможности:**\n"
        "• Просмотр списка клиентов\n"
        "• Создание новых клиентов\n"
        "• Получение VPN-ссылок\n"
        "• Удаление клиентов\n"
        "• Детальная статистика по каждому клиенту"
    )
    
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update_or_send(chat_id, text, reply_markup, context)

def main():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_name))
    
    logger.info("Bot started!")
    application.run_polling()

if __name__ == "__main__":
    main()