import logging
import asyncio
import os
from typing import Dict, List
from datetime import datetime
from dotenv import load_dotenv

from telegram import Update, ChatMember
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация бота - теперь из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден в переменных окружения!")
    exit(1)

COOLDOWN_SECONDS = 300  # Кулдаун 5 минут

# Хранилище кулдаунов
cooldowns: Dict[int, datetime] = {}

async def check_admin_rights(bot, chat_id: int, user_id: int) -> bool:
    """Проверяем, является ли пользователь администратором"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки прав админа: {e}")
        return False

async def get_all_chat_members(chat_id: int, bot) -> List[ChatMember]:
    """Получаем администраторов чата (получить всех участников невозможно через API)"""
    members = []
    
    try:
        # Получаем только администраторов
        # ВНИМАНИЕ: Telegram Bot API не позволяет получить ВСЕХ участников чата
        # Можно получить только администраторов
        admins = await bot.get_chat_administrators(chat_id)
        members.extend(admins)
        logger.info(f"Получено {len(admins)} администраторов для чата {chat_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при получении участников: {e}")
    
    return members

async def ping_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /all - пинг ВСЕХ участников чата"""
    
    # Проверяем, что это групповой чат
    if update.effective_chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ Эта команда работает только в групповых чатах!")
        return
    
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Проверка кулдауна
    now = datetime.now()
    if chat_id in cooldowns:
        time_passed = (now - cooldowns[chat_id]).total_seconds()
        if time_passed < COOLDOWN_SECONDS:
            remaining = COOLDOWN_SECONDS - int(time_passed)
            minutes = remaining // 60
            seconds = remaining % 60
            await update.message.reply_text(
                f"⏳ Следующее использование через {minutes}:{seconds:02d}"
            )
            return
    
    # Проверяем права администратора у вызывающего
    is_admin = await check_admin_rights(context.bot, chat_id, user_id)
    if not is_admin:
        await update.message.reply_text("❌ Только администраторы могут использовать эту команду!")
        return
    
    # Получаем причину
    reason = " ".join(context.args) if context.args else "Общий сбор!"
    
    # Уведомляем о начале сбора
    collecting_msg = await update.message.reply_text(
        "🔄 Собираю список участников..."
    )
    
    try:
        # Получаем администраторов чата
        members = await get_all_chat_members(chat_id, context.bot)
        
        if not members:
            await collecting_msg.edit_text("❌ Не удалось получить список участников. Убедитесь, что бот администратор.")
            return
        
        # Формируем список упоминаний
        mentions = []
        total_count = 0
        bot_count = 0
        admin_count = 0
        
        for member in members:
            if member.user.is_bot:
                bot_count += 1
                continue
            
            total_count += 1
            if member.status in ['administrator', 'creator']:
                admin_count += 1
            
            # Создаем упоминание
            user = member.user
            username = f"@{user.username}" if user.username else user.first_name
            mentions.append(f"[{username}](tg://user?id={user.id})")
        
        if total_count == 0:
            await collecting_msg.edit_text("🤔 В чате нет участников для упоминания.")
            return
        
        # Разбиваем на части
        chunk_size = 30
        mention_chunks = [mentions[i:i + chunk_size] for i in range(0, len(mentions), chunk_size)]
        
        # Отправляем первое сообщение
        first_message = (
            f"📢 *ВНИМАНИЕ ВСЕМ!*\n\n"
            f"*Причина:* {reason}\n"
            f"*Инициатор:* {update.effective_user.mention_markdown()}\n"
            f"*Упомянуто участников:* {total_count}\n"
            f"*Из них администраторов:* {admin_count}\n"
            f"*Ботов пропущено:* {bot_count}\n\n"
            f"────────────────────\n"
            f"*Примечание:* Бот может упомянуть только администраторов чата.\n"
            f"Для упоминания всех участников используйте reply-ответы на это сообщение."
        )
        
        await collecting_msg.edit_text(first_message, parse_mode='Markdown')
        
        # Отправляем упоминания частями (если есть кого упоминать)
        if mentions:
            for i, chunk in enumerate(mention_chunks, 1):
                mention_text = ", ".join(chunk)
                chunk_message = f"📋 *Часть {i}:*\n{mention_text}"
                
                await asyncio.sleep(0.5)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk_message,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
        
        # Обновляем кулдаун
        cooldowns[chat_id] = now
        
        logger.info(f"User {user_id} упомянул {total_count} участников в чате {chat_id}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await collecting_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

async def ping_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admins - пинг только администраторов"""
    
    if update.effective_chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ Только для групповых чатов!")
        return
    
    chat_id = update.effective_chat.id
    
    try:
        # Получаем администраторов
        admins = await context.bot.get_chat_administrators(chat_id)
        
        # Фильтруем ботов
        human_admins = [admin for admin in admins if not admin.user.is_bot]
        
        if not human_admins:
            await update.message.reply_text("🤔 В этом чате нет администраторов-людей.")
            return
        
        # Формируем упоминания
        mentions = []
        for admin in human_admins:
            user = admin.user
            username = f"@{user.username}" if user.username else user.first_name
            mentions.append(f"[{username}](tg://user?id={user.id})")
        
        reason = " ".join(context.args) if context.args else "Требуется внимание администрации!"
        
        message_text = (
            f"👑 *ВНИМАНИЕ АДМИНИСТРАЦИИ!*\n\n"
            f"*Причина:* {reason}\n"
            f"*Инициатор:* {update.effective_user.mention_markdown()}\n\n"
            f"*Список администраторов:*\n"
            f"{', '.join(mentions)}"
        )
        
        await update.message.reply_text(
            message_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "🤖 *Бот для упоминания участников чата*\n\n"
        "*Команды:*\n"
        "• /all [причина] - Упоминание администраторов чата (только для админов)\n"
        "• /admins [причина] - Упоминание только администраторов\n"
        "• /help - Помощь\n\n"
        "⚠️ *Важная информация:*\n"
        "• Бот может упоминать только администраторов чата\n"
        "• Для работы бота нужны права администратора\n"
        "• Кулдаун: 5 минут между использованиями /all\n\n"
        "🔧 *Технические детали:*\n"
        "Telegram Bot API не позволяет ботам получать полный список участников чата.",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    await update.message.reply_text(
        "📋 *Помощь по командам:*\n\n"
        "*/all [текст]* - Упоминает администраторов чата\n"
        "   - Только для администраторов\n"
        "   - Кулдаун 5 минут\n\n"
        "*/admins [текст]* - Упоминает только администраторов\n"
        "   - Доступно всем\n\n"
        "*/start* - Информация о боте\n"
        "*/help* - Эта справка\n\n"
        "💡 *Как упомянуть всех участников:*\n"
        "1. Используйте /all для упоминания админов\n"
        "2. Ответьте (reply) на сообщение бота\n"
        "3. Все участники увидят уведомление!",
        parse_mode='Markdown'
    )

async def check_bot_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяем, что бот администратор, когда его добавляют в чат"""
    if update.message and update.message.new_chat_members:
        for member in update.message.new_chat_members:
            if member.id == context.bot.id:
                # Даем инструкции
                await update.message.reply_text(
                    "🤖 *Бот активирован!*\n\n"
                    "Для работы команд:\n"
                    "1. Назначьте бота администратором\n"
                    "2. Дайте права:\n"
                    "   - Просмотр участников\n"
                    "   - Отправка сообщений\n\n"
                    "⚠️ *Ограничения:*\n"
                    "• Бот может упоминать только администраторов\n"
                    "• Для упоминания всех используйте reply к сообщениям бота\n\n"
                    "Используйте /help для списка команд",
                    parse_mode='Markdown'
                )

def main():
    """Запуск бота"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("all", ping_all_command))
    application.add_handler(CommandHandler("admins", ping_admins_command))
    
    # Обработчики событий
    application.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, 
        check_bot_admin
    ))
    
    print("=" * 50)
    print("🤖 Бот для упоминания участников чата")
    print(f"👤 Токен: {BOT_TOKEN[:10]}...")
    print("=" * 50)
    print("\nВажные условия для работы:")
    print("1. Добавьте бота в группу")
    print("2. Сделайте бота администратором")
    print("3. Бот запущен и ожидает команд...")
    print("\nЛогирование начато...")
    
    # Запускаем бота
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()