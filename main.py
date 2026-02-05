import discord
from discord.ext import commands
import os
import logging
from datetime import datetime
from typing import Optional, List
from dotenv import load_dotenv
import asyncpg
import asyncio
from aiohttp import web
import socket

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Настройки бота
TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('BOT_PREFIX', '!')
ADMIN_ROLE_IDS = [int(role_id.strip()) for role_id in os.getenv('ADMIN_ROLE_IDS', '').split(',') if role_id.strip()]
DATABASE_URL = os.getenv('DATABASE_URL')

# Автоматическое определение порта для Render
PORT = int(os.getenv('PORT', '10000'))

# Проверка наличия токена
if not TOKEN:
    logger.error("❌ Токен бота не найден! Установите DISCORD_TOKEN в .env файле")
    exit(1)

# Настройки интентов Discord
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

# Создание бота
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(PREFIX),
    intents=intents,
    help_command=None
)

# Цвета для embed сообщений
COLORS = {
    'success': discord.Color.green(),
    'error': discord.Color.red(),
    'info': discord.Color.blue(),
    'warning': discord.Color.orange(),
    'points': discord.Color.gold(),
    'admin': discord.Color.purple()
}

# Настройки ролей (поинты: название_роли)
ROLE_SETTINGS = {
    50: 'raider newgen',
    100: 'raider scout', 
    150: 'raider striker',
    350: 'raider legend',
    500: 'raider commander'
}

# Цвета для ролей
ROLE_COLORS = {
    'raider newgen': discord.Color.green(),
    'raider scout': discord.Color.blue(),
    'raider striker': discord.Color.orange(),
    'raider legend': discord.Color.purple(),
    'raider commander': discord.Color.gold()
}

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER ==========

async def handle_root(request):
    """Обработчик корневого пути"""
    return web.Response(text="🤖 Discord Points Bot is running!\n"
                           "📊 Status: Online\n"
                           f"⏰ Uptime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                           f"🔗 GitHub: https://github.com\n"
                           "📞 Support: Available")

async def handle_ping(request):
    """Обработчик пинга"""
    return web.Response(text="pong")

async def handle_health(request):
    """Обработчик health check"""
    return web.json_response({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "discord-points-bot",
        "bot_status": "online" if bot.is_ready() else "starting",
        "guild_count": len(bot.guilds) if bot.is_ready() else 0,
        "database": "connected" if db.pool else "disconnected"
    })

async def start_web_server():
    """Запуск веб-сервера"""
    try:
        app = web.Application()
        
        # Добавляем маршруты
        app.router.add_get('/', handle_root)
        app.router.add_get('/ping', handle_ping)
        app.router.add_get('/health', handle_health)
        
        # Запускаем сервер
        runner = web.AppRunner(app)
        await runner.setup()
        
        # Используем порт из переменных окружения
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"🌐 Веб-сервер запущен на порту {PORT}")
        logger.info(f"📡 Доступные эндпоинты:")
        logger.info(f"   http://0.0.0.0:{PORT}/")
        logger.info(f"   http://0.0.0.0:{PORT}/ping")
        logger.info(f"   http://0.0.0.0:{PORT}/health")
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка запуска веб-сервера: {e}")
        return False

# ========== БАЗА ДАННЫХ ==========

class Database:
    def __init__(self):
        self.pool = None
    
    async def connect(self):
        """Подключение к базе данных"""
        try:
            self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
            await self.init_tables()
            logger.info("✅ Подключено к базе данных")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к БД: {e}")
            return False
    
    async def init_tables(self):
        """Инициализация таблиц"""
        async with self.pool.acquire() as conn:
            # Таблица пользователей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT,
                    guild_id BIGINT,
                    points INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            ''')
            
            # Таблица транзакций
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    guild_id BIGINT,
                    amount INTEGER,
                    admin_id BIGINT,
                    reason TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица блокировок каналов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS locked_channels (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    channel_id BIGINT,
                    role_id BIGINT,
                    lock_type TEXT,
                    created_by BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, channel_id, role_id)
                )
            ''')
            
            # Таблица каналов для блокировки
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS channel_list (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    channel_id BIGINT,
                    channel_name TEXT,
                    added_by BIGINT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, channel_id)
                )
            ''')
            
            logger.info("✅ Таблицы инициализированы")
    
    async def get_user_points(self, user_id: int, guild_id: int) -> int:
        """Получить количество поинтов пользователя"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT points FROM users WHERE user_id = $1 AND guild_id = $2',
                user_id, guild_id
            )
            return result['points'] if result else 0
    
    async def add_points(self, user_id: int, guild_id: int, amount: int, admin_id: int, reason: str = "Выдано админом"):
        """Добавить поинты пользователю"""
        async with self.pool.acquire() as conn:
            # Добавляем или обновляем запись пользователя
            await conn.execute('''
                INSERT INTO users (user_id, guild_id, points)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, guild_id) 
                DO UPDATE SET points = users.points + EXCLUDED.points
            ''', user_id, guild_id, amount)
            
            # Записываем транзакцию
            await conn.execute('''
                INSERT INTO transactions (user_id, guild_id, amount, admin_id, reason)
                VALUES ($1, $2, $3, $4, $5)
            ''', user_id, guild_id, amount, admin_id, reason)
            
            # Получаем новый баланс
            points = await self.get_user_points(user_id, guild_id)
            return points
    
    async def remove_points(self, user_id: int, guild_id: int, amount: int, admin_id: int, reason: str = "Изъято админом"):
        """Убрать поинты у пользователя"""
        async with self.pool.acquire() as conn:
            # Получаем текущие поинты
            current = await self.get_user_points(user_id, guild_id)
            new_amount = max(0, current - amount)
            
            # Обновляем поинты
            await conn.execute('''
                UPDATE users SET points = $1 
                WHERE user_id = $2 AND guild_id = $3
            ''', new_amount, user_id, guild_id)
            
            # Записываем транзакцию
            await conn.execute('''
                INSERT INTO transactions (user_id, guild_id, amount, admin_id, reason)
                VALUES ($1, $2, $3, $4, $5)
            ''', user_id, guild_id, -amount, admin_id, reason)
            
            return new_amount
    
    async def set_points(self, user_id: int, guild_id: int, amount: int, admin_id: int, reason: str = "Установлено админом"):
        """Установить точное количество поинтов"""
        async with self.pool.acquire() as conn:
            # Устанавливаем поинты
            await conn.execute('''
                INSERT INTO users (user_id, guild_id, points)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, guild_id) 
                DO UPDATE SET points = EXCLUDED.points
            ''', user_id, guild_id, amount)
            
            # Записываем транзакцию
            difference = amount - (await self.get_user_points(user_id, guild_id))
            await conn.execute('''
                INSERT INTO transactions (user_id, guild_id, amount, admin_id, reason)
                VALUES ($1, $2, $3, $4, $5)
            ''', user_id, guild_id, difference, admin_id, reason)
            
            return amount
    
    async def get_leaderboard(self, guild_id: int, limit: int = 10):
        """Получить таблицу лидеров"""
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT user_id, points FROM users 
                WHERE guild_id = $1 AND points > 0
                ORDER BY points DESC 
                LIMIT $2
            ''', guild_id, limit)
    
    async def get_user_position(self, user_id: int, guild_id: int) -> int:
        """Получить позицию пользователя в рейтинге"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow('''
                SELECT COUNT(*) as position FROM users 
                WHERE guild_id = $1 AND points > (
                    SELECT points FROM users 
                    WHERE user_id = $2 AND guild_id = $1
                )
            ''', guild_id, user_id)
            return result['position'] + 1 if result else 1
    
    async def get_guild_stats(self, guild_id: int):
        """Получить статистику сервера"""
        async with self.pool.acquire() as conn:
            stats = await conn.fetchrow('''
                SELECT 
                    COUNT(*) as total_users,
                    SUM(points) as total_points,
                    AVG(points) as avg_points,
                    MAX(points) as max_points
                FROM users 
                WHERE guild_id = $1
            ''', guild_id)
            
            return {
                'total_users': stats['total_users'] or 0,
                'total_points': stats['total_points'] or 0,
                'avg_points': stats['avg_points'] or 0,
                'max_points': stats['max_points'] or 0
            }
    
    async def reset_guild_points(self, guild_id: int):
        """Сбросить все поинты на сервере"""
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM users WHERE guild_id = $1', guild_id)
            await conn.execute('DELETE FROM transactions WHERE guild_id = $1', guild_id)
    
    # Методы для управления списком каналов
    async def add_channel_to_list(self, guild_id: int, channel_id: int, channel_name: str, added_by: int):
        """Добавить канал в список для блокировки"""
        async with self.pool.acquire() as conn:
            try:
                await conn.execute('''
                    INSERT INTO channel_list (guild_id, channel_id, channel_name, added_by)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (guild_id, channel_id) 
                    DO UPDATE SET channel_name = EXCLUDED.channel_name
                ''', guild_id, channel_id, channel_name, added_by)
                return True
            except Exception as e:
                logger.error(f"Ошибка добавления канала: {e}")
                return False
    
    async def remove_channel_from_list(self, guild_id: int, channel_id: int = None):
        """Удалить канал из списка"""
        async with self.pool.acquire() as conn:
            try:
                if channel_id:
                    await conn.execute(
                        'DELETE FROM channel_list WHERE guild_id = $1 AND channel_id = $2',
                        guild_id, channel_id
                    )
                else:
                    await conn.execute(
                        'DELETE FROM channel_list WHERE guild_id = $1',
                        guild_id
                    )
                return True
            except Exception as e:
                logger.error(f"Ошибка удаления канала: {e}")
                return False
    
    async def get_channel_list(self, guild_id: int):
        """Получить список каналов для блокировки"""
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                'SELECT * FROM channel_list WHERE guild_id = $1 ORDER BY added_at',
                guild_id
            )
    
    async def get_channel_count(self, guild_id: int):
        """Получить количество каналов в списке"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT COUNT(*) as count FROM channel_list WHERE guild_id = $1',
                guild_id
            )
            return result['count'] if result else 0
    
    # Методы для блокировок каналов
    async def add_channel_lock(self, guild_id: int, channel_id: int, role_id: int, lock_type: str, created_by: int):
        """Добавить блокировку канала для роли"""
        async with self.pool.acquire() as conn:
            try:
                await conn.execute('''
                    INSERT INTO locked_channels (guild_id, channel_id, role_id, lock_type, created_by)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (guild_id, channel_id, role_id) 
                    DO UPDATE SET lock_type = EXCLUDED.lock_type
                ''', guild_id, channel_id, role_id, lock_type, created_by)
                return True
            except Exception as e:
                logger.error(f"Ошибка добавления блокировки: {e}")
                return False
    
    async def remove_channel_lock(self, guild_id: int, channel_id: int, role_id: int = None):
        """Удалить блокировку канала"""
        async with self.pool.acquire() as conn:
            try:
                if role_id:
                    await conn.execute(
                        'DELETE FROM locked_channels WHERE guild_id = $1 AND channel_id = $2 AND role_id = $3',
                        guild_id, channel_id, role_id
                    )
                else:
                    await conn.execute(
                        'DELETE FROM locked_channels WHERE guild_id = $1 AND channel_id = $2',
                        guild_id, channel_id
                    )
                return True
            except Exception as e:
                logger.error(f"Ошибка удаления блокировки: {e}")
                return False
    
    async def get_channel_locks(self, guild_id: int, channel_id: int = None):
        """Получить все блокировки каналов"""
        async with self.pool.acquire() as conn:
            if channel_id:
                return await conn.fetch(
                    'SELECT * FROM locked_channels WHERE guild_id = $1 AND channel_id = $2',
                    guild_id, channel_id
                )
            else:
                return await conn.fetch(
                    'SELECT * FROM locked_channels WHERE guild_id = $1',
                    guild_id
                )
    
    async def clear_all_locks(self, guild_id: int):
        """Удалить все блокировки на сервере"""
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM locked_channels WHERE guild_id = $1', guild_id)

# Создаем экземпляр базы данных
db = Database()

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С РОЛЯМИ ==========

def is_admin():
    """Проверка, является ли пользователь администратором"""
    async def predicate(ctx):
        # Проверка прав администратора Discord
        if ctx.author.guild_permissions.administrator:
            return True
        
        # Проверка кастомных админских ролей
        author_role_ids = [role.id for role in ctx.author.roles]
        return any(admin_role_id in author_role_ids for admin_role_id in ADMIN_ROLE_IDS)
    
    return commands.check(predicate)

async def check_and_assign_roles(member: discord.Member):
    """Проверка и выдача ролей на основе поинтов"""
    try:
        guild_id = member.guild.id
        user_id = member.id
        
        # Получаем поинты пользователя
        points = await db.get_user_points(user_id, guild_id)
        
        # Находим соответствующую роль
        target_role_name = None
        for required_points, role_name in sorted(ROLE_SETTINGS.items()):
            if points >= required_points:
                target_role_name = role_name
        
        if not target_role_name:
            return
        
        # Проверяем, есть ли уже эта роль
        discord_role = discord.utils.get(member.guild.roles, name=target_role_name)
        if discord_role and discord_role in member.roles:
            return  # Уже есть эта роль
        
        # Удаляем старые роли за поинты
        for role_name in ROLE_SETTINGS.values():
            if role_name != target_role_name:
                old_role = discord.utils.get(member.guild.roles, name=role_name)
                if old_role and old_role in member.roles:
                    try:
                        await member.remove_roles(old_role)
                    except:
                        pass
        
        # Находим или создаем новую роль
        if not discord_role:
            try:
                color = ROLE_COLORS.get(target_role_name, discord.Color.default())
                discord_role = await member.guild.create_role(
                    name=target_role_name,
                    color=color,
                    mentionable=True,
                    reason="Автоматическое создание роли за поинты"
                )
                logger.info(f"Создана новая роль: {target_role_name}")
            except discord.Forbidden:
                logger.error(f'Недостаточно прав для создания роли {target_role_name}')
                return
            except Exception as e:
                logger.error(f'Ошибка создания роли: {e}')
                return
        
        # Добавляем новую роль
        try:
            await member.add_roles(discord_role)
            
            # Отправляем уведомление
            await send_role_notification(member, target_role_name, points)
            
            logger.info(f'Выдана роль {target_role_name} пользователю {member.display_name}')
            
        except discord.Forbidden:
            logger.error(f'Недостаточно прав для выдачи роли {target_role_name}')
        except Exception as e:
            logger.error(f'Ошибка выдачи роли: {e}')
            
    except Exception as e:
        logger.error(f'Ошибка в check_and_assign_roles: {e}')

async def send_role_notification(member: discord.Member, role_name: str, points: int):
    """Отправка уведомления о получении новой роли"""
    try:
        embed = discord.Embed(
            title="🎉 Новая роль получена!",
            description=f"**{member.display_name}** получил(а) новую роль!",
            color=ROLE_COLORS.get(role_name, discord.Color.green())
        )
        
        embed.add_field(name="Роль", value=f"`{role_name}`", inline=True)
        embed.add_field(name="Поинты", value=f"`{points}`", inline=True)
        
        congratulations = {
            'raider newgen': "Добро пожаловать в ряды рейдеров! 🚀",
            'raider scout': "Отличная работа! Ты становишься опытным скаутом! 🔍",
            'raider striker': "Впечатляюще! Ты теперь ударная сила нашего отряда! 💥",
            'raider legend': "Легендарно! Твои достижения войдут в историю! 📜",
            'raider commander': "Величайший из великих! Ты ведешь за собой весь отряд! 👑"
        }
        
        congrats = congratulations.get(role_name, "Поздравляем с получением новой роли! ✨")
        embed.add_field(name="Поздравления!", value=congrats, inline=False)
        
        # Пытаемся отправить в системный канал или ЛС
        if member.guild.system_channel:
            await member.guild.system_channel.send(member.mention, embed=embed)
        else:
            await member.send(embed=embed)
            
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")

# ========== ФУНКЦИИ ДЛЯ БЛОКИРОВКИ КАНАЛОВ ==========

async def apply_channel_lock(channel: discord.TextChannel, role: discord.Role, lock_type: str):
    """Применить блокировку к каналу"""
    try:
        # Получаем текущие права
        overwrites = channel.overwrites_for(role)
        
        # Настраиваем права в зависимости от типа блокировки
        if lock_type == 'send':
            overwrites.send_messages = False
            overwrites.add_reactions = False
            overwrites.attach_files = False
        elif lock_type == 'view':
            overwrites.read_messages = False
            overwrites.send_messages = False
        elif lock_type == 'both':
            overwrites.read_messages = False
            overwrites.send_messages = False
            overwrites.add_reactions = False
            overwrites.attach_files = False
        
        # Применяем права
        await channel.set_permissions(role, overwrite=overwrites)
        return True
        
    except discord.Forbidden:
        logger.error(f"Недостаточно прав для блокировки канала {channel.name}")
        return False
    except Exception as e:
        logger.error(f"Ошибка блокировки канала: {e}")
        return False

async def remove_channel_lock(channel: discord.TextChannel, role: discord.Role):
    """Снять блокировку с канала"""
    try:
        # Сбрасываем права для роли
        await channel.set_permissions(role, overwrite=None)
        return True
        
    except discord.Forbidden:
        logger.error(f"Недостаточно прав для разблокировки канала {channel.name}")
        return False
    except Exception as e:
        logger.error(f"Ошибка разблокировки канала: {e}")
        return False

async def lock_all_channels_in_list(guild: discord.Guild, role: discord.Role, lock_type: str):
    """Заблокировать все каналы из списка для роли"""
    try:
        results = []
        channels_list = await db.get_channel_list(guild.id)
        
        if not channels_list:
            return ["❌ Список каналов пуст. Добавьте каналы с помощью /addchannel"]
        
        for channel_data in channels_list:
            try:
                channel = guild.get_channel(channel_data['channel_id'])
                if not channel:
                    # Пробуем получить через fetch
                    try:
                        channel = await guild.fetch_channel(channel_data['channel_id'])
                    except:
                        channel = None
                
                if channel:
                    # Сохраняем блокировку в БД
                    await db.add_channel_lock(
                        guild.id, channel.id, role.id, 
                        lock_type, guild.me.id
                    )
                    
                    # Применяем блокировку
                    success = await apply_channel_lock(channel, role, lock_type)
                    
                    if success:
                        results.append(f"✅ {channel.mention}")
                    else:
                        results.append(f"⚠️ {channel.mention} - ошибка прав")
                else:
                    results.append(f"❌ Канал {channel_data['channel_name']} не найден")
                    
            except Exception as e:
                results.append(f"❌ Ошибка: {str(e)[:50]}")
        
        return results
        
    except Exception as e:
        logger.error(f"Ошибка блокировки всех каналов: {e}")
        return [f"❌ Ошибка: {str(e)[:100]}"]

async def unlock_all_channels_in_list(guild: discord.Guild, role: discord.Role = None):
    """Разблокировать все каналы из списка для роли"""
    try:
        results = []
        channels_list = await db.get_channel_list(guild.id)
        
        if not channels_list:
            return ["❌ Список каналов пуст"]
        
        for channel_data in channels_list:
            try:
                channel = guild.get_channel(channel_data['channel_id'])
                if not channel:
                    continue
                
                if role:
                    # Снимаем блокировку для конкретной роли
                    await db.remove_channel_lock(guild.id, channel.id, role.id)
                    success = await remove_channel_lock(channel, role)
                    
                    if success:
                        results.append(f"✅ {channel.mention} - разблокирован для {role.mention}")
                    else:
                        results.append(f"⚠️ {channel.mention} - ошибка прав")
                else:
                    # Снимаем все блокировки канала
                    locks = await db.get_channel_locks(guild.id, channel.id)
                    for lock in locks:
                        role_obj = guild.get_role(lock['role_id'])
                        if role_obj:
                            await remove_channel_lock(channel, role_obj)
                    
                    await db.remove_channel_lock(guild.id, channel.id)
                    results.append(f"✅ {channel.mention} - все блокировки сняты")
                    
            except Exception as e:
                results.append(f"❌ {channel_data['channel_name']} - ошибка: {str(e)[:50]}")
        
        return results
        
    except Exception as e:
        logger.error(f"Ошибка разблокировки всех каналов: {e}")
        return [f"❌ Ошибка: {str(e)[:100]}"]

# ========== СОБЫТИЯ БОТА ==========

@bot.event
async def on_ready():
    """Событие при запуске бота"""
    logger.info(f'✅ Бот {bot.user} запущен!')
    logger.info(f'📊 Серверов: {len(bot.guilds)}')
    logger.info(f'🌐 Порт веб-сервера: {PORT}')
    
    # Подключаемся к базе данных
    if not await db.connect():
        logger.error("❌ Не удалось подключиться к базе данных!")
    
    # Запускаем веб-сервер
    await start_web_server()
    
    # Устанавливаем статус
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{PREFIX}help | {len(bot.guilds)} серв."
        )
    )
    
    # Синхронизация slash команд
    try:
        synced = await bot.tree.sync()
        logger.info(f'✅ Синхронизировано {len(synced)} команд')
    except Exception as e:
        logger.error(f'❌ Ошибка синхронизации команд: {e}')
    
    logger.info("🚀 Бот полностью готов к работе!")

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ СПИСКОМ КАНАЛОВ ==========

@bot.hybrid_command(
    name='addchannel',
    description='Добавить канал в список для блокировки'
)
@is_admin()
async def add_channel(
    ctx,
    channel: discord.TextChannel
):
    """Добавить канал в список для блокировки"""
    success = await db.add_channel_to_list(
        ctx.guild.id, channel.id, channel.name, ctx.author.id
    )
    
    if success:
        embed = discord.Embed(
            title="✅ Канал добавлен",
            description=f"Канал {channel.mention} добавлен в список для блокировки",
            color=COLORS['success']
        )
        embed.add_field(name="Название", value=f"`{channel.name}`", inline=True)
        embed.add_field(name="ID", value=f"`{channel.id}`", inline=True)
        embed.add_field(name="Добавил", value=ctx.author.mention, inline=True)
        
        # Показываем текущее количество каналов
        count = await db.get_channel_count(ctx.guild.id)
        embed.set_footer(text=f"Всего каналов в списке: {count}")
    else:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Не удалось добавить канал в список",
            color=COLORS['error']
        )
    
    await ctx.send(embed=embed)

@bot.hybrid_command(
    name='removechannel',
    description='Удалить канал из списка для блокировки'
)
@is_admin()
async def remove_channel(
    ctx,
    channel: Optional[discord.TextChannel] = None
):
    """Удалить канал из списка для блокировки"""
    if channel:
        success = await db.remove_channel_from_list(ctx.guild.id, channel.id)
        
        if success:
            embed = discord.Embed(
                title="✅ Канал удален",
                description=f"Канал {channel.mention} удален из списка",
                color=COLORS['success']
            )
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Не удалось удалить канал {channel.mention}",
                color=COLORS['error']
            )
    else:
        embed = discord.Embed(
            title="⚠️ Удаление всех каналов",
            description="Вы уверены, что хотите удалить ВСЕ каналы из списка?",
            color=COLORS['warning']
        )
        
        view = discord.ui.View(timeout=30)
        
        async def confirm_callback(interaction):
            if interaction.user != ctx.author:
                await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
                return
            
            success = await db.remove_channel_from_list(ctx.guild.id)
            
            if success:
                confirm_embed = discord.Embed(
                    title="✅ Все каналы удалены",
                    description="Все каналы удалены из списка",
                    color=COLORS['success']
                )
            else:
                confirm_embed = discord.Embed(
                    title="❌ Ошибка",
                    description="Не удалось удалить каналы",
                    color=COLORS['error']
                )
            
            await interaction.response.edit_message(embed=confirm_embed, view=None)
        
        async def cancel_callback(interaction):
            if interaction.user != ctx.author:
                await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
                return
            
            cancel_embed = discord.Embed(
                title="❌ Отменено",
                description="Удаление каналов отменено",
                color=COLORS['warning']
            )
            await interaction.response.edit_message(embed=cancel_embed, view=None)
        
        confirm_button = discord.ui.Button(label="✅ Подтвердить", style=discord.ButtonStyle.danger)
        cancel_button = discord.ui.Button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
        
        confirm_button.callback = confirm_callback
        cancel_button.callback = cancel_callback
        
        view.add_item(confirm_button)
        view.add_item(cancel_button)
        
        await ctx.send(embed=embed, view=view)
        return
    
    await ctx.send(embed=embed)

@bot.hybrid_command(
    name='listchannels',
    description='Показать список каналов для блокировки'
)
@is_admin()
async def list_channels(ctx):
    """Показать список каналов для блокировки"""
    channels = await db.get_channel_list(ctx.guild.id)
    
    if not channels:
        embed = discord.Embed(
            title="📋 Список каналов пуст",
            description="Добавьте каналы с помощью `/addchannel #канал`",
            color=COLORS['info']
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="📋 Список каналов для блокировки",
        color=COLORS['info']
    )
    
    for i, channel_data in enumerate(channels, 1):
        channel = ctx.guild.get_channel(channel_data['channel_id'])
        channel_mention = channel.mention if channel else f"`{channel_data['channel_name']}`"
        
        # Получаем информацию о добавившем
        try:
            added_by = await ctx.guild.fetch_member(channel_data['added_by'])
            added_by_name = added_by.display_name if added_by else f"ID: {channel_data['added_by']}"
        except:
            added_by_name = f"ID: {channel_data['added_by']}"
        
        embed.add_field(
            name=f"{i}. {channel_data['channel_name']}",
            value=f"Канал: {channel_mention}\n"
                  f"ID: `{channel_data['channel_id']}`\n"
                  f"Добавил: {added_by_name}\n"
                  f"Дата: {channel_data['added_at'].strftime('%d.%m.%Y %H:%M')}",
            inline=False
        )
    
    count = len(channels)
    embed.set_footer(text=f"Всего каналов: {count}")
    
    await ctx.send(embed=embed)

# ========== КОМАНДЫ ДЛЯ БЛОКИРОВКИ КАНАЛОВ ==========

@bot.hybrid_command(
    name='lockchannels',
    description='Заблокировать все каналы из списка для роли'
)
@is_admin()
async def lock_channels(
    ctx,
    role: discord.Role,
    lock_type: str = "send"
):
    """
    Заблокировать все каналы из списка для роли
    
    Типы блокировки:
    - send: запрет писать, ставить реакции, прикреплять файлы
    - view: запрет читать и писать (канал скрыт)
    - both: полная блокировка
    """
    lock_types = ['send', 'view', 'both']
    
    if lock_type.lower() not in lock_types:
        embed = discord.Embed(
            title="❌ Неверный тип блокировки",
            description=f"Доступные типы: {', '.join(lock_types)}",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="🔒 Блокировка каналов",
        description=f"Начинаю блокировку каналов для роли {role.mention}...",
        color=COLORS['warning']
    )
    
    # Показываем тип блокировки
    lock_info = {
        'send': "📝 Запрещено писать, ставить реакции и прикреплять файлы",
        'view': "👁️ Запрещено читать и писать (канал скрыт)",
        'both': "🚫 Полная блокировка"
    }
    
    embed.add_field(name="Тип блокировки", value=lock_info[lock_type.lower()], inline=False)
    
    message = await ctx.send(embed=embed)
    
    # Блокируем каналы
    results = await lock_all_channels_in_list(ctx.guild, role, lock_type.lower())
    
    # Создаем итоговый отчет
    success_count = sum(1 for r in results if "✅" in r)
    warning_count = sum(1 for r in results if "⚠️" in r)
    error_count = sum(1 for r in results if "❌" in r)
    
    final_embed = discord.Embed(
        title="✅ Блокировка завершена",
        color=COLORS['success'] if error_count == 0 else COLORS['warning']
    )
    
    final_embed.add_field(
        name="📊 Результаты",
        value=f"✅ Успешно: {success_count} каналов\n"
              f"⚠️ С предупреждениями: {warning_count} каналов\n"
              f"❌ Ошибки: {error_count} каналов",
        inline=False
    )
    
    # Показываем первые 10 результатов
    if len(results) <= 10:
        final_embed.add_field(
            name="📝 Детали",
            value="\n".join(results[:10]),
            inline=False
        )
    else:
        # Показываем статистику
        final_embed.add_field(
            name="ℹ️ Информация",
            value=f"Обработано {len(results)} каналов",
            inline=False
        )
    
    final_embed.add_field(
        name="🎯 Для роли",
        value=role.mention,
        inline=True
    )
    
    final_embed.add_field(
        name="👤 Заблокировал",
        value=ctx.author.mention,
        inline=True
    )
    
    final_embed.set_footer(text=f"Тип блокировки: {lock_type.upper()}")
    
    await message.edit(embed=final_embed)

@bot.hybrid_command(
    name='unlockchannels',
    description='Разблокировать все каналы из списка для роли'
)
@is_admin()
async def unlock_channels(
    ctx,
    role: Optional[discord.Role] = None
):
    """Разблокировать все каналы из списка для роли или полностью"""
    if role:
        embed = discord.Embed(
            title="🔓 Разблокировка каналов",
            description=f"Начинаю разблокировку каналов для роли {role.mention}...",
            color=COLORS['info']
        )
    else:
        embed = discord.Embed(
            title="🔓 Полная разблокировка",
            description="Начинаю полную разблокировку всех каналов...",
            color=COLORS['info']
        )
    
    message = await ctx.send(embed=embed)
    
    # Разблокируем каналы
    results = await unlock_all_channels_in_list(ctx.guild, role)
    
    # Создаем итоговый отчет
    success_count = sum(1 for r in results if "✅" in r)
    warning_count = sum(1 for r in results if "⚠️" in r)
    error_count = sum(1 for r in results if "❌" in r)
    
    final_embed = discord.Embed(
        title="✅ Разблокировка завершена",
        color=COLORS['success'] if error_count == 0 else COLORS['warning']
    )
    
    if role:
        final_embed.description = f"Каналы разблокированы для роли {role.mention}"
    else:
        final_embed.description = "Все каналы полностью разблокированы"
    
    final_embed.add_field(
        name="📊 Результаты",
        value=f"✅ Успешно: {success_count} каналов\n"
              f"⚠️ С предупреждениями: {warning_count} каналов\n"
              f"❌ Ошибки: {error_count} каналов",
        inline=False
    )
    
    # Показываем первые 10 результатов
    if len(results) <= 10:
        final_embed.add_field(
            name="📝 Детали",
            value="\n".join(results[:10]),
            inline=False
        )
    
    final_embed.add_field(
        name="👤 Разблокировал",
        value=ctx.author.mention,
        inline=True
    )
    
    await message.edit(embed=final_embed)

@bot.hybrid_command(
    name='clearlocks',
    description='Удалить все блокировки на сервере'
)
@is_admin()
async def clear_locks(ctx):
    """Удалить все блокировки на сервере"""
    # Получаем все блокировки
    locks = await db.get_channel_locks(ctx.guild.id)
    
    if not locks:
        embed = discord.Embed(
            title="ℹ️ Нет активных блокировок",
            description="На сервере нет активных блокировок каналов",
            color=COLORS['info']
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="🗑️ Удаление всех блокировок",
        description=f"Вы уверены, что хотите удалить ВСЕ блокировки каналов ({len(locks)} шт.)?\nЭто действие необратимо!",
        color=COLORS['error']
    )
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        # Удаляем все блокировки из БД
        await db.clear_all_locks(ctx.guild.id)
        
        # Снимаем блокировки со всех каналов
        for lock in locks:
            try:
                channel = ctx.guild.get_channel(lock['channel_id'])
                role = ctx.guild.get_role(lock['role_id'])
                
                if channel and role:
                    await remove_channel_lock(channel, role)
            except:
                pass
        
        confirm_embed = discord.Embed(
            title="✅ Все блокировки удалены",
            description=f"Удалено {len(locks)} блокировок",
            color=COLORS['success']
        )
        confirm_embed.add_field(
            name="👤 Удалил",
            value=ctx.author.mention,
            inline=True
        )
        
        await interaction.response.edit_message(embed=confirm_embed, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Отменено",
            description="Удаление блокировок отменено",
            color=COLORS['warning']
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)
    
    confirm_button = discord.ui.Button(label="✅ Подтвердить", style=discord.ButtonStyle.danger)
    cancel_button = discord.ui.Button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    
    confirm_button.callback = confirm_callback
    cancel_button.callback = cancel_callback
    
    view.add_item(confirm_button)
    view.add_item(cancel_button)
    
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(
    name='lockinfo',
    description='Показать информацию о блокировках'
)
@is_admin()
async def lock_info(ctx):
    """Показать информацию о блокировках"""
    # Получаем все блокировки
    locks = await db.get_channel_locks(ctx.guild.id)
    
    if not locks:
        embed = discord.Embed(
            title="ℹ️ Нет активных блокировок",
            description="На сервере нет активных блокировок каналов",
            color=COLORS['info']
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="🔒 Активные блокировки",
        description=f"Всего активных блокировок: **{len(locks)}**",
        color=COLORS['info']
    )
    
    # Группируем по ролям
    roles_dict = {}
    for lock in locks:
        role_id = lock['role_id']
        if role_id not in roles_dict:
            roles_dict[role_id] = []
        roles_dict[role_id].append(lock)
    
    for role_id, role_locks in list(roles_dict.items())[:5]:  # Показываем первые 5 ролей
        role = ctx.guild.get_role(role_id)
        role_name = role.mention if role else f"Роль {role_id}"
        
        # Группируем по типам блокировок
        lock_types = {}
        for lock in role_locks:
            lock_type = lock['lock_type']
            if lock_type not in lock_types:
                lock_types[lock_type] = []
            
            channel = ctx.guild.get_channel(lock['channel_id'])
            channel_name = channel.mention if channel else f"Канал {lock['channel_id']}"
            lock_types[lock_type].append(channel_name)
        
        # Формируем текст для роли
        role_text = []
        for lock_type, channels in lock_types.items():
            lock_type_name = {
                'send': 'Запрет писать',
                'view': 'Скрытие канала',
                'both': 'Полная блокировка'
            }.get(lock_type, lock_type)
            
            role_text.append(f"**{lock_type_name}** ({len(channels)}): {', '.join(channels[:3])}")
            if len(channels) > 3:
                role_text[-1] += f" и ещё {len(channels) - 3}"
        
        embed.add_field(
            name=f"🎯 {role_name}",
            value="\n".join(role_text),
            inline=False
        )
    
    # Показываем количество каналов в списке
    channel_count = await db.get_channel_count(ctx.guild.id)
    embed.add_field(
        name="📋 Список каналов",
        value=f"Каналов в списке для блокировки: **{channel_count}**",
        inline=False
    )
    
    if len(roles_dict) > 5:
        embed.set_footer(text=f"Показано 5 из {len(roles_dict)} ролей. Используйте /listchannels для полного списка")
    
    await ctx.send(embed=embed)

# ========== КОМАНДЫ ДЛЯ ПОИНТОВ ==========

@bot.hybrid_command(
    name='addpoints',
    description='Выдать поинты пользователю'
)
@is_admin()
async def add_points(
    ctx,
    member: discord.Member,
    amount: int,
    reason: str = "Выдано админом"
):
    """Выдать поинты пользователю"""
    if amount <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    new_total = await db.add_points(member.id, ctx.guild.id, amount, ctx.author.id, reason)
    
    embed = discord.Embed(
        title="✅ Поинты выданы!",
        color=COLORS['success']
    )
    embed.add_field(name="Получатель", value=member.mention, inline=True)
    embed.add_field(name="Добавлено", value=f"{amount} поинтов", inline=True)
    embed.add_field(name="Новый баланс", value=f"{new_total} поинтов", inline=True)
    embed.add_field(name="Причина", value=reason, inline=False)
    embed.add_field(name="Выдал", value=ctx.author.mention, inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    
    await ctx.send(embed=embed)
    
    # Проверяем и выдаем роли
    await check_and_assign_roles(member)

@bot.hybrid_command(
    name='removepoints',
    description='Забрать поинты у пользователя'
)
@is_admin()
async def remove_points(
    ctx,
    member: discord.Member,
    amount: int,
    reason: str = "Изъято админом"
):
    """Забрать поинты у пользователя"""
    if amount <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    new_total = await db.remove_points(member.id, ctx.guild.id, amount, ctx.author.id, reason)
    
    embed = discord.Embed(
        title="✅ Поинты изъяты!",
        color=COLORS['success']
    )
    embed.add_field(name="Пользователь", value=member.mention, inline=True)
    embed.add_field(name="Изъято", value=f"{amount} поинтов", inline=True)
    embed.add_field(name="Новый баланс", value=f"{new_total} поинтов", inline=True)
    embed.add_field(name="Причина", value=reason, inline=False)
    embed.add_field(name="Изъял", value=ctx.author.mention, inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    
    await ctx.send(embed=embed)
    
    # Проверяем и обновляем роли
    await check_and_assign_roles(member)

@bot.hybrid_command(
    name='setpoints',
    description='Установить точное количество поинтов'
)
@is_admin()
async def set_points(
    ctx,
    member: discord.Member,
    amount: int,
    reason: str = "Установлено админом"
):
    """Установить точное количество поинтов"""
    if amount < 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов не может быть отрицательным!",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    new_total = await db.set_points(member.id, ctx.guild.id, amount, ctx.author.id, reason)
    
    embed = discord.Embed(
        title="✅ Поинты установлены!",
        color=COLORS['success']
    )
    embed.add_field(name="Пользователь", value=member.mention, inline=True)
    embed.add_field(name="Новое значение", value=f"{new_total} поинтов", inline=True)
    embed.add_field(name="Причина", value=reason, inline=False)
    embed.add_field(name="Установил", value=ctx.author.mention, inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    
    await ctx.send(embed=embed)
    
    # Проверяем и выдаем роли
    await check_and_assign_roles(member)

@bot.hybrid_command(
    name='resetpoints',
    description='Сбросить все поинты на сервере'
)
@is_admin()
async def reset_points(ctx):
    """Сбросить все поинты на сервере"""
    embed = discord.Embed(
        title="⚠️ ОПАСНОЕ ДЕЙСТВИЕ",
        description="Вы уверены, что хотите сбросить ВСЕ поинты на сервере?\nЭто действие необратимо!",
        color=COLORS['error']
    )
    embed.add_field(name="Что будет сброшено:", 
                   value="• Все поинты пользователей\n• Вся история транзакций", 
                   inline=False)
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        await db.reset_guild_points(ctx.guild.id)
        
        confirm_embed = discord.Embed(
            title="✅ Все поинты сброшены!",
            description="Все данные о поинтах на этом сервере были удалены.",
            color=COLORS['success']
        )
        await interaction.response.edit_message(embed=confirm_embed, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Сброс отменен",
            color=COLORS['warning']
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)
    
    confirm_button = discord.ui.Button(label="✅ Подтвердить", style=discord.ButtonStyle.danger)
    cancel_button = discord.ui.Button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    
    confirm_button.callback = confirm_callback
    cancel_button.callback = cancel_callback
    
    view.add_item(confirm_button)
    view.add_item(cancel_button)
    
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(
    name='points',
    description='Проверить свои поинты или поинты другого пользователя'
)
async def check_points(ctx, member: Optional[discord.Member] = None):
    """Проверить поинты"""
    if member is None:
        member = ctx.author
    
    user_id = member.id
    guild_id = ctx.guild.id
    
    # Получаем данные из базы
    points = await db.get_user_points(user_id, guild_id)
    position = await db.get_user_position(user_id, guild_id)
    
    # Создаем embed
    embed = discord.Embed(
        title=f"🏆 Поинты {member.display_name}",
        color=COLORS['points']
    )
    
    # Основная информация
    embed.add_field(name="Баланс", value=f"**{points}** поинтов", inline=True)
    embed.add_field(name="Позиция в рейтинге", value=f"**#{position}**", inline=True)
    
    # Система ролей
    roles_text = []
    for required_points, role_name in sorted(ROLE_SETTINGS.items()):
        status = "✅" if points >= required_points else "⏳"
        roles_text.append(f"{status} **{role_name}** - {required_points} поинтов")
    
    embed.add_field(
        name="🏅 Система ролей",
        value="\n".join(roles_text),
        inline=False
    )
    
    # Следующая роль
    next_role = None
    points_needed = 0
    for required_points, role_name in sorted(ROLE_SETTINGS.items()):
        if points < required_points:
            next_role = role_name
            points_needed = required_points - points
            break
    
    if next_role:
        embed.add_field(
            name="Следующая цель",
            value=f"**{next_role}** (нужно ещё {points_needed} поинтов)",
            inline=False
        )
    elif points > 0:
        embed.add_field(
            name="🎉 Поздравляем!",
            value="Вы достигли максимальной роли!",
            inline=False
        )
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"ID: {user_id}")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(
    name='leaderboard',
    description='Таблица лидеров по поинтам'
)
async def leaderboard(ctx, page: int = 1):
    """Таблица лидеров"""
    guild_id = ctx.guild.id
    
    # Получаем лидерборд из базы
    leaderboard_data = await db.get_leaderboard(guild_id, 20)
    
    if not leaderboard_data:
        embed = discord.Embed(
            title="📊 Таблица лидеров",
            description="Пока никто не имеет поинтов!",
            color=COLORS['info']
        )
        await ctx.send(embed=embed)
        return
    
    # Получаем статистику
    stats = await db.get_guild_stats(guild_id)
    
    # Создаем embed
    embed = discord.Embed(
        title="🏆 Таблица лидеров",
        color=COLORS['points']
    )
    
    # Добавляем записи
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, record in enumerate(leaderboard_data, start=1):
        try:
            member = await ctx.guild.fetch_member(record['user_id'])
            username = member.display_name
        except:
            username = f"Пользователь ({record['user_id']})"
        
        medal = medals[i-1] if i <= len(medals) else f"{i}."
        
        # Определяем роль
        user_role = "Нет роли"
        for required_points, role_name in sorted(ROLE_SETTINGS.items(), reverse=True):
            if record['points'] >= required_points:
                user_role = role_name
                break
        
        embed.add_field(
            name=f"{medal} {username}",
            value=f"**{record['points']}** поинтов | 🏅 {user_role}",
            inline=False
        )
    
    # Статистика
    embed.add_field(
        name="📊 Статистика сервера",
        value=f"• Всего пользователей: **{stats['total_users']}**\n"
              f"• Всего поинтов: **{stats['total_points']}**\n"
              f"• Среднее: **{stats['avg_points']:.1f}**\n"
              f"• Максимум: **{stats['max_points']}**",
        inline=False
    )
    
    embed.set_footer(text=f"Всего участников: {stats['total_users']}")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(
    name='roles',
    description='Показать систему ролей'
)
async def show_roles(ctx):
    """Показать систему ролей"""
    embed = discord.Embed(
        title="🏅 Система ролей",
        description="Роли выдаются автоматически при достижении определенного количества поинтов",
        color=COLORS['points']
    )
    
    for required_points, role_name in sorted(ROLE_SETTINGS.items()):
        color = ROLE_COLORS.get(role_name, discord.Color.default())
        color_block = f"`{str(color).upper()}`"
        
        embed.add_field(
            name=f"🎖️ {role_name}",
            value=f"**{required_points}** поинтов\nЦвет: {color_block}",
            inline=True
        )
    
    embed.set_footer(text="Админские роли: " + ", ".join(ADMIN_ROLES))
    await ctx.send(embed=embed)

@bot.hybrid_command(
    name='ping',
    description='Проверить пинг бота'
)
async def ping_command(ctx):
    """Проверить пинг бота"""
    latency = round(bot.latency * 1000)
    
    embed = discord.Embed(
        title="🏓 Понг!",
        color=COLORS['success']
    )
    embed.add_field(name="Задержка API", value=f"**{latency}мс**", inline=True)
    embed.add_field(name="Серверов", value=f"**{len(bot.guilds)}**", inline=True)
    embed.add_field(name="Порт веб-сервера", value=f"**{PORT}**", inline=True)
    embed.add_field(name="Статус БД", value="✅ **Подключена**" if db.pool else "❌ **Отключена**", inline=True)
    embed.add_field(name="Режим работы", value="✅ **24/7 Активен**", inline=False)
    embed.set_footer(text=f"Эндпоинт для пинга: /ping")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(
    name='help',
    description='Показать все команды'
)
async def help_command(ctx):
    """Показать все команды"""
    # Проверяем, является ли пользователь админом
    is_user_admin = False
    try:
        is_user_admin = await is_admin().predicate(ctx)
    except:
        pass
    
    embed = discord.Embed(
        title="🆘 Помощь по командам",
        description=f"Префикс команд: `{PREFIX}`\nБот также поддерживает slash-команды (/)",
        color=COLORS['info']
    )
    
    # Команды для всех
    embed.add_field(
        name="👤 Команды для всех",
        value="• `/points [@пользователь]` - Проверить поинты\n"
              "• `/leaderboard [страница]` - Таблица лидеров\n"
              "• `/roles` - Система ролей\n"
              "• `/ping` - Проверить статус бота\n"
              "• `/help` - Эта справка",
        inline=False
    )
    
    # Команды для админов
    if is_user_admin:
        embed.add_field(
            name="👑 Команды для админов (поинты)",
            value="• `/addpoints @пользователь количество [причина]` - Выдать поинты\n"
                  "• `/removepoints @пользователь количество [причина]` - Забрать поинты\n"
                  "• `/setpoints @пользователь количество [причина]` - Установить поинты\n"
                  "• `/resetpoints` - Сбросить все поинты",
            inline=False
        )
        
        # Команды для админов (блокировка каналов)
        embed.add_field(
            name="🔒 Команды для блокировки каналов",
            value="• `/addchannel #канал` - Добавить канал в список\n"
                  "• `/removechannel [#канал]` - Удалить канал из списка\n"
                  "• `/listchannels` - Показать список каналов\n"
                  "• `/lockchannels @роль [тип]` - Заблокировать каналы\n"
                  "• `/unlockchannels [@роль]` - Разблокировать каналы\n"
                  "• `/lockinfo` - Показать активные блокировки\n"
                  "• `/clearlocks` - Удалить все блокировки",
            inline=False
        )
        
        embed.add_field(
            name="ℹ️ Типы блокировки:",
            value="• `send` - запрет писать и прикреплять файлы\n"
                  "• `view` - скрытие канала\n"
                  "• `both` - полная блокировка",
            inline=False
        )
    
    # Информация о системе
    embed.add_field(
        name="ℹ️ Информация о боте",
        value=f"• Админские роли: {', '.join(ADMIN_ROLES)}\n"
              f"• База данных: PostgreSQL\n"
              f"• Хостинг: Render.com\n"
              f"• Порт: {PORT}\n"
              f"• Режим работы: 24/7",
        inline=False
    )
    
    await ctx.send(embed=embed)

# ========== ОБРАБОТКА ОШИБОК ==========

@bot.event
async def on_command_error(ctx, error):
    """Обработка ошибок команд"""
    if isinstance(error, commands.CheckFailure):
        embed = discord.Embed(
            title="❌ Недостаточно прав",
            description=f"Только **{', '.join(ADMIN_ROLES)}** могут использовать эту команду!",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="❌ Не хватает аргументов",
            description=f"Используйте `{PREFIX}help` для справки по командам",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.BadArgument):
        embed = discord.Embed(
            title="❌ Неправильные аргументы",
            description="Проверьте правильность введенных данных",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.CommandNotFound):
        pass  # Игнорируем
    else:
        logger.error(f"Необработанная ошибка команды: {error}")
        embed = discord.Embed(
            title="❌ Неизвестная ошибка",
            description="Произошла неизвестная ошибка.",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)

# ========== ЗАПУСК БОТА ==========

if __name__ == "__main__":
    logger.info("🚀 Запуск Discord Points Bot")
    logger.info(f"🤖 Префикс команд: {PREFIX}")
    logger.info(f"👑 Админские роли: {ADMIN_ROLE_ID}")
    logger.info(f"🌐 Порт веб-сервера: {PORT}")
    logger.info("🗄️  Используется база данных PostgreSQL")
    logger.info("🔄 Бот будет работать 24/7 с веб-сервером для пинга")
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Ошибка авторизации! Проверьте токен бота.")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
