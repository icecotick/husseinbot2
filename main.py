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
import time

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
    200: 'raider newgen',
    400: 'raider scout', 
    800: 'raider striker', 
    1200: 'raider heavy', 
    1600: 'raider legend',
    2400: 'raider who lives on raids ', 
    3200: 'raid moderator', 
    4000: 'raider commander'
}

# Цвета для ролей
ROLE_COLORS = {
    'raider newgen': discord.Color.green(),
    'raider scout': discord.Color.blue(),
    'raider striker': discord.Color.orange(),
    'raider heavy': discord.Color.yellow(),
    'raider legend': discord.Color.purple(),
    'raider who lives on raids': discord.Color.blue(),
    'raider moderator': discord.Color.red(),
    'raider commander': discord.Color.gold()
}

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
            
            logger.info("✅ Таблицы пользователей и каналов инициализированы")
    
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
            current_points = await self.get_user_points(user_id, guild_id)
            difference = amount - current_points
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
                    SELECT COALESCE(points, 0) FROM users 
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
                'avg_points': round(stats['avg_points'] or 0, 1),
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

# ========== КЛАСС ДЛЯ УПРАВЛЕНИЯ КЛАНАМИ ==========

class ClanManager:
    """Класс для управления кланами"""
    
    def __init__(self, pool):
        self.pool = pool
    
    async def init_tables(self):
        """Инициализация таблиц для кланов"""
        async with self.pool.acquire() as conn:
            # Таблица кланов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS clans (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    name TEXT NOT NULL,
                    tag TEXT,
                    clan_type TEXT NOT NULL CHECK (clan_type IN ('ally', 'enemy', 'peace')),
                    description TEXT,
                    added_by BIGINT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, name, clan_type)
                )
            ''')
            
            logger.info("✅ Таблицы кланов инициализированы")
    
    async def add_clan(self, guild_id: int, name: str, clan_type: str, tag: str = None, description: str = None, added_by: int = None):
        """Добавить клан"""
        async with self.pool.acquire() as conn:
            try:
                # Проверяем, существует ли уже такой клан
                existing = await conn.fetchrow(
                    'SELECT * FROM clans WHERE guild_id = $1 AND LOWER(name) = LOWER($2) AND clan_type = $3',
                    guild_id, name, clan_type
                )
                
                if existing:
                    return False, f"Клан **{name}** уже существует в категории **{self.get_type_name(clan_type)}**"
                
                # Добавляем новый клан
                await conn.execute('''
                    INSERT INTO clans (guild_id, name, tag, clan_type, description, added_by)
                    VALUES ($1, $2, $3, $4, $5, $6)
                ''', guild_id, name, tag, clan_type, description, added_by)
                
                return True, f"✅ Клан **{name}** добавлен в категорию **{self.get_type_name(clan_type)}**"
            except Exception as e:
                logger.error(f"Ошибка добавления клана: {e}")
                return False, f"❌ Ошибка при добавлении клана: {str(e)[:100]}"
    
    async def remove_clan(self, guild_id: int, name: str, clan_type: str = None):
        """Удалить клан"""
        async with self.pool.acquire() as conn:
            try:
                if clan_type:
                    # Удаляем конкретный клан из категории
                    result = await conn.execute('''
                        DELETE FROM clans 
                        WHERE guild_id = $1 AND LOWER(name) = LOWER($2) AND clan_type = $3
                    ''', guild_id, name, clan_type)
                    
                    if result == "DELETE 0":
                        return False, f"❌ Клан **{name}** не найден в категории **{self.get_type_name(clan_type)}**"
                    
                    return True, f"✅ Клан **{name}** удален из категории **{self.get_type_name(clan_type)}**"
                else:
                    # Удаляем клан из всех категорий
                    result = await conn.execute('''
                        DELETE FROM clans 
                        WHERE guild_id = $1 AND LOWER(name) = LOWER($2)
                    ''', guild_id, name)
                    
                    if result == "DELETE 0":
                        return False, f"❌ Клан **{name}** не найден"
                    
                    return True, f"✅ Клан **{name}** удален из всех категорий"
            except Exception as e:
                logger.error(f"Ошибка удаления клана: {e}")
                return False, f"❌ Ошибка при удалении клана: {str(e)[:100]}"
    
    async def get_clans_by_type(self, guild_id: int, clan_type: str):
        """Получить все кланы определенного типа"""
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT * FROM clans 
                WHERE guild_id = $1 AND clan_type = $2
                ORDER BY name
            ''', guild_id, clan_type)
    
    async def get_all_clans(self, guild_id: int):
        """Получить все кланы на сервере"""
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT * FROM clans 
                WHERE guild_id = $1
                ORDER BY 
                    CASE clan_type
                        WHEN 'ally' THEN 1
                        WHEN 'peace' THEN 2
                        WHEN 'enemy' THEN 3
                    END,
                    name
            ''', guild_id)
    
    async def get_clan_count(self, guild_id: int, clan_type: str = None):
        """Получить количество кланов"""
        async with self.pool.acquire() as conn:
            if clan_type:
                result = await conn.fetchrow(
                    'SELECT COUNT(*) as count FROM clans WHERE guild_id = $1 AND clan_type = $2',
                    guild_id, clan_type
                )
            else:
                result = await conn.fetchrow(
                    'SELECT COUNT(*) as count FROM clans WHERE guild_id = $1',
                    guild_id
                )
            return result['count'] if result else 0
    
    async def clear_all_clans(self, guild_id: int):
        """Удалить все кланы на сервере"""
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM clans WHERE guild_id = $1', guild_id)
    
    async def search_clan(self, guild_id: int, search_term: str):
        """Поиск клана по названию"""
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT * FROM clans 
                WHERE guild_id = $1 AND LOWER(name) LIKE LOWER($2)
                ORDER BY 
                    CASE clan_type
                        WHEN 'ally' THEN 1
                        WHEN 'peace' THEN 2
                        WHEN 'enemy' THEN 3
                    END,
                    name
            ''', guild_id, f'%{search_term}%')
    
    async def update_clan_description(self, guild_id: int, name: str, clan_type: str, description: str):
        """Обновить описание клана"""
        async with self.pool.acquire() as conn:
            try:
                await conn.execute('''
                    UPDATE clans 
                    SET description = $1
                    WHERE guild_id = $2 AND LOWER(name) = LOWER($3) AND clan_type = $4
                ''', description, guild_id, name, clan_type)
                return True, f"✅ Описание клана **{name}** обновлено"
            except Exception as e:
                return False, f"❌ Ошибка обновления описания: {str(e)[:100]}"
    
    async def update_clan_tag(self, guild_id: int, name: str, clan_type: str, tag: str):
        """Обновить тег клана"""
        async with self.pool.acquire() as conn:
            try:
                await conn.execute('''
                    UPDATE clans 
                    SET tag = $1
                    WHERE guild_id = $2 AND LOWER(name) = LOWER($3) AND clan_type = $4
                ''', tag, guild_id, name, clan_type)
                return True, f"✅ Тег клана **{name}** обновлен на `[{tag}]`"
            except Exception as e:
                return False, f"❌ Ошибка обновления тега: {str(e)[:100]}"
    
    def get_type_name(self, clan_type: str):
        """Получить читаемое название типа клана"""
        types = {
            'ally': '🤝 Союзники (ALLY)',
            'enemy': '⚔️ Враги (ENEMY)',
            'peace': '🕊️ Нейтральные/Пис (PEACE)'
        }
        return types.get(clan_type, clan_type)
    
    def get_type_emoji(self, clan_type: str):
        """Получить эмодзи для типа клана"""
        emojis = {
            'ally': '🤝',
            'enemy': '⚔️',
            'peace': '🕊️'
        }
        return emojis.get(clan_type, '📌')


# Создаем экземпляр БД
db = Database()
# ClanManager будет инициализирован после подключения к БД
clan_manager = None

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER ==========

async def handle_root(request):
    """Обработчик корневого пути"""
    return web.Response(
        text="✅ Discord Points Bot is running!\n"
             f"📊 Status: Online\n"
             f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
             f"🌐 Port: {PORT}\n"
             f"🔄 Bot is ready to accept commands!"
    )

async def handle_ping(request):
    """Обработчик пинга"""
    return web.Response(text="pong")

async def handle_health(request):
    """Обработчик health check"""
    status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "discord-points-bot",
        "port": PORT,
        "bot_ready": bot.is_ready()
    }
    
    # Добавляем информацию о БД если доступна
    if hasattr(db, 'pool') and db.pool:
        status["database"] = "connected"
    else:
        status["database"] = "connecting"
    
    return web.json_response(status)

async def start_web_server():
    """Запуск веб-сервера"""
    try:
        app = web.Application()
        
        # Добавляем маршруты
        app.router.add_get('/', handle_root)
        app.router.add_get('/ping', handle_ping)
        app.router.add_get('/health', handle_health)
        
        # Запускаем сервер на ВСЕХ интерфейсах
        runner = web.AppRunner(app)
        await runner.setup()
        
        # ВАЖНО: слушаем на 0.0.0.0, а не на localhost
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"🌐 Веб-сервер успешно запущен на порту {PORT}")
        logger.info(f"📡 Сервер слушает на 0.0.0.0:{PORT}")
        
        # Проверяем, что сервер действительно слушает
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', PORT))
        if result == 0:
            logger.info(f"✅ Порт {PORT} открыт и доступен")
        else:
            logger.warning(f"⚠️ Порт {PORT} может быть недоступен (код: {result})")
        sock.close()
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка запуска веб-сервера: {e}")
        return False

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
            try:
                await member.send(embed=embed)
            except:
                pass  # Игнорируем ошибки отправки в ЛС
            
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
            return ["❌ Список каналов пуст. Добавьте каналы с помощью !addchannel"]
        
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
    if await db.connect():
        logger.info("✅ База данных подключена")
        
        # Инициализируем ClanManager ПОСЛЕ подключения к БД
        global clan_manager
        clan_manager = ClanManager(db.pool)
        await clan_manager.init_tables()
        logger.info("✅ Менеджер кланов инициализирован")
    else:
        logger.error("❌ Не удалось подключиться к базе данных!")
        logger.warning("⚠️ Бот будет работать без функций базы данных!")
    
    # Запускаем веб-сервер
    await start_web_server()
    
    # Устанавливаем статус
    activity_text = f"{PREFIX}help | {len(bot.guilds)} серв."
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=activity_text
        )
    )
    
    logger.info("🚀 Бот полностью готов к работе!")
    logger.info(f"📡 Веб-сервер доступен по адресу: http://0.0.0.0:{PORT}/")

# ========== КОМАНДЫ ==========

@bot.command(name='addchannel')
@is_admin()
async def add_channel(ctx, channel: discord.TextChannel):
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
    
    await safe_send(ctx, embed=embed)

@bot.command(name='removechannel')
@is_admin()
async def remove_channel(ctx, channel: Optional[discord.TextChannel] = None):
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
        await safe_send(ctx, embed=embed)
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
        
        await safe_send(ctx, embed=embed, view=view)

@bot.command(name='listchannels')
@is_admin()
async def list_channels(ctx):
    """Показать список каналов для блокировки"""
    channels = await db.get_channel_list(ctx.guild.id)
    
    if not channels:
        embed = discord.Embed(
            title="📋 Список каналов пуст",
            description="Добавьте каналы с помощью `!addchannel #канал`",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
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
    
    await safe_send(ctx, embed=embed)

@bot.command(name='lockchannels')
@is_admin()
async def lock_channels(ctx, role: discord.Role, lock_type: str = "send"):
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
        await safe_send(ctx, embed=embed)
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
    
    message = await safe_send(ctx, embed=embed)
    if not message:
        return
    
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
    
    await safe_edit(message, embed=final_embed)

@bot.command(name='unlockchannels')
@is_admin()
async def unlock_channels(ctx, role: Optional[discord.Role] = None):
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
    
    message = await safe_send(ctx, embed=embed)
    if not message:
        return
    
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
    
    await safe_edit(message, embed=final_embed)

@bot.command(name='clearlocks')
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
        await safe_send(ctx, embed=embed)
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
    
    await safe_send(ctx, embed=embed, view=view)

@bot.command(name='lockinfo')
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
        await safe_send(ctx, embed=embed)
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
        embed.set_footer(text=f"Показано 5 из {len(roles_dict)} ролей. Используйте !listchannels для полного списка")
    
    await safe_send(ctx, embed=embed)

# ========== КОМАНДЫ ДЛЯ ПОИНТОВ ==========

@bot.command(name='addpoints')
@is_admin()
async def add_points(ctx, member: discord.Member, amount: int, *, reason: str = "Выдано админом"):
    """Выдать поинты пользователю"""
    if amount <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
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
    
    await safe_send(ctx, embed=embed)
    
    # Проверяем и выдаем роли
    await check_and_assign_roles(member)

@bot.command(name='removepoints')
@is_admin()
async def remove_points(ctx, member: discord.Member, amount: int, *, reason: str = "Изъято админом"):
    """Забрать поинты у пользователя"""
    if amount <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
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
    
    await safe_send(ctx, embed=embed)
    
    # Проверяем и обновляем роли
    await check_and_assign_roles(member)

@bot.command(name='setpoints')
@is_admin()
async def set_points(ctx, member: discord.Member, amount: int, *, reason: str = "Установлено админом"):
    """Установить точное количество поинтов"""
    if amount < 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов не может быть отрицательным!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
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
    
    await safe_send(ctx, embed=embed)
    
    # Проверяем и выдаем роли
    await check_and_assign_roles(member)

@bot.command(name='resetpoints')
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
    
    await safe_send(ctx, embed=embed, view=view)

@bot.command(name='points')
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
    
    await safe_send(ctx, embed=embed)

@bot.command(name='leaderboard')
async def leaderboard(ctx, page: int = 1):
    """Таблица лидеров по поинтам"""
    guild_id = ctx.guild.id
    
    # Получаем лидерборд из базы
    leaderboard_data = await db.get_leaderboard(guild_id, 20)
    
    if not leaderboard_data:
        embed = discord.Embed(
            title="📊 Таблица лидеров",
            description="Пока никто не имеет поинтов!",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
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
    
    await safe_send(ctx, embed=embed)

@bot.command(name='roles')
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
    
    admin_role_ids_str = ', '.join(str(role_id) for role_id in ADMIN_ROLE_IDS) if ADMIN_ROLE_IDS else "не указаны"
    embed.set_footer(text="Админские ID ролей: " + admin_role_ids_str)
    await safe_send(ctx, embed=embed)

@bot.command(name='ping')
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
    
    db_status = "✅ **Подключена**" if hasattr(db, 'pool') and db.pool else "❌ **Отключена**"
    embed.add_field(name="Статус БД", value=db_status, inline=True)
    
    clan_status = "✅ **Готов**" if clan_manager is not None else "⏳ **Инициализация**"
    embed.add_field(name="Система кланов", value=clan_status, inline=True)
    
    embed.add_field(name="Режим работы", value="✅ **24/7 Активен**", inline=False)
    embed.set_footer(text=f"Префикс команд: {PREFIX}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='export')
@is_admin()
async def export_command(ctx):
    """Экспорт данных в CSV"""
    guild_id = ctx.guild.id
    
    # Получаем все данные
    users = await db.get_leaderboard(guild_id, 1000)  # Получаем до 1000 пользователей
    
    # Формируем CSV
    csv_data = "ID пользователя,Ник,Поинты,Позиция\n"
    
    for i, user in enumerate(users, 1):
        try:
            member = await ctx.guild.fetch_member(user['user_id'])
            username = member.display_name
        except:
            username = f"User_{user['user_id']}"
        
        csv_data += f"{user['user_id']},{username},{user['points']},{i}\n"
    
    # Создаем файл
    filename = f"export_{guild_id}_{int(datetime.now().timestamp())}.csv"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(csv_data)
    
    # Отправляем файл
    file = discord.File(filename)
    await safe_send(ctx, "📁 Экспорт данных о поинтах:", file=file)
    
    # Удаляем временный файл
    os.remove(filename)

@bot.command(name='raid')
@is_admin()
async def raid_command(ctx, clan: str, link: str):
    """
    Отправить рейд-оповещение с упоминанием @everyone
    
    Параметры:
    1. Клан - название клана
    2. Ссылка - ссылка на рейд
    """
    # Проверяем права на упоминание everyone
    if not ctx.channel.permissions_for(ctx.guild.me).mention_everyone:
        embed = discord.Embed(
            title="❌ Недостаточно прав",
            description="Мне нужны права на упоминание @everyone для этой команды!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Проверяем валидность ссылки
    if not link.startswith(('http://', 'https://', 'discord.gg/')):
        await safe_send(ctx, "❌ Ссылка должна начинаться с http://, https:// или discord.gg/")
        return
    
    # Создаем оповещение
    embed = discord.Embed(
        title="🚨 РЕЙД ОБЪЯВЛЕН! 🚨",
        description="Всем участникам срочно присоединиться!",
        color=discord.Color.red()
    )
    
    embed.add_field(
        name="👥 Клан",
        value=f"**{clan}**",
        inline=True
    )
    
    embed.add_field(
        name="🔗 Ссылка",
        value=f"[Нажмите чтобы присоединиться]({link})",
        inline=True
    )
    
    embed.set_footer(
        text=f"Оповещение отправлено {ctx.author.display_name}",
        icon_url=ctx.author.display_avatar.url
    )
    
    # Отправляем с упоминанием everyone
    await safe_send(ctx, content="@everyone", embed=embed)
    
    # Отправляем подтверждение автору
    confirm_embed = discord.Embed(
        title="✅ Рейд-оповещение отправлено!",
        description=f"Клан: **{clan}**\nСсылка: {link}",
        color=COLORS['success']
    )
    await safe_send(ctx, embed=confirm_embed)

@bot.command(name='unlock')
@is_admin()
async def unlock_command(ctx):
    """Разблокировать все каналы из списка для конкретной роли"""
    # ID конкретной роли
    TARGET_ROLE_ID = 1431589898183512129
    
    # Получаем роль по ID
    target_role = ctx.guild.get_role(TARGET_ROLE_ID)
    if not target_role:
        # Пробуем получить роль через fetch
        try:
            target_role = await ctx.guild.fetch_role(TARGET_ROLE_ID)
        except:
            embed = discord.Embed(
                title="❌ Роль не найдена",
                description=f"Роль с ID `{TARGET_ROLE_ID}` не найдена на сервере!",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            return
    
    embed = discord.Embed(
        title="🔓 Разблокировка каналов",
        description=f"Начинаю разблокировку каналов для роли {target_role.mention}...",
        color=COLORS['info']
    )
    
    embed.add_field(name="ID роли", value=f"`{TARGET_ROLE_ID}`", inline=True)
    embed.add_field(name="Название роли", value=f"`{target_role.name}`", inline=True)
    
    message = await safe_send(ctx, embed=embed)
    if not message:
        return
    
    # Разблокируем каналы
    results = await unlock_all_channels_in_list(ctx.guild, target_role)
    
    # Создаем итоговый отчет
    success_count = sum(1 for r in results if "✅" in r)
    warning_count = sum(1 for r in results if "⚠️" in r)
    error_count = sum(1 for r in results if "❌" in r)
    
    final_embed = discord.Embed(
        title="✅ Разблокировка завершена",
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
    
    final_embed.add_field(
        name="🎯 Для роли",
        value=f"{target_role.mention} (ID: `{TARGET_ROLE_ID}`)",
        inline=True
    )
    
    final_embed.add_field(
        name="👤 Разблокировал",
        value=ctx.author.mention,
        inline=True
    )
    
    await safe_edit(message, embed=final_embed)

@bot.command(name='lock')
@is_admin()
async def lock_command(ctx, lock_type: str = "send"):
    """
    Заблокировать все каналы из списка для роли 1431589898183512129
    
    Типы блокировки:
    - send: запрет писать, ставить реакции, прикреплять файлы
    - view: запрет читать и писать (канал скрыт)
    - both: полная блокировка
    """
    # ID конкретной роли
    TARGET_ROLE_ID = 1431589898183512129
    
    lock_types = ['send', 'view', 'both']
    
    if lock_type.lower() not in lock_types:
        embed = discord.Embed(
            title="❌ Неверный тип блокировки",
            description=f"Доступные типы: {', '.join(lock_types)}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Получаем роль по ID
    target_role = ctx.guild.get_role(TARGET_ROLE_ID)
    if not target_role:
        # Пробуем получить роль через fetch
        try:
            target_role = await ctx.guild.fetch_role(TARGET_ROLE_ID)
        except:
            embed = discord.Embed(
                title="❌ Роль не найдена",
                description=f"Роль с ID `{TARGET_ROLE_ID}` не найдена на сервере!",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            return
    
    embed = discord.Embed(
        title="🔒 Блокировка каналов",
        description=f"Начинаю блокировку каналов для роли {target_role.mention}...",
        color=COLORS['warning']
    )
    
    # Показываем тип блокировки
    lock_info = {
        'send': "📝 Запрещено писать, ставить реакции и прикреплять файлы",
        'view': "👁️ Запрещено читать и писать (канал скрыт)",
        'both': "🚫 Полная блокировка"
    }
    
    embed.add_field(name="Тип блокировки", value=lock_info[lock_type.lower()], inline=False)
    embed.add_field(name="ID роли", value=f"`{TARGET_ROLE_ID}`", inline=True)
    embed.add_field(name="Название роли", value=f"`{target_role.name}`", inline=True)
    
    message = await safe_send(ctx, embed=embed)
    if not message:
        return
    
    # Блокируем каналы
    results = await lock_all_channels_in_list(ctx.guild, target_role, lock_type.lower())
    
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
        value=f"{target_role.mention} (ID: `{TARGET_ROLE_ID}`)",
        inline=True
    )
    
    final_embed.add_field(
        name="👤 Заблокировал",
        value=ctx.author.mention,
        inline=True
    )
    
    final_embed.set_footer(text=f"Тип блокировки: {lock_type.upper()}")
    
    await safe_edit(message, embed=final_embed)

@bot.command(name='addpoints_multi')
@is_admin()
async def add_points_multi(ctx, amount: int, *members: discord.Member, reason: str = "Выдано админом"):
    """Выдать поинты нескольким пользователям сразу"""
    if amount <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if len(members) == 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Не указаны пользователи!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if len(members) > 25:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Можно выдать поинты максимум 25 пользователям за раз!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Отправляем начальное сообщение
    embed = discord.Embed(
        title="⏳ Массовая выдача поинтов",
        description=f"Выдаю поинты {len(members)} пользователям...",
        color=COLORS['warning']
    )
    embed.add_field(name="Количество поинтов", value=f"**{amount}** каждому", inline=True)
    embed.add_field(name="Причина", value=reason, inline=True)
    
    message = await safe_send(ctx, embed=embed)
    if not message:
        return
    
    # Выдаем поинты
    results = []
    success_count = 0
    
    for member in members:
        try:
            new_total = await db.add_points(member.id, ctx.guild.id, amount, ctx.author.id, reason)
            results.append(f"✅ {member.mention}: +{amount} поинтов (всего: {new_total})")
            success_count += 1
            
            # Проверяем и выдаем роли
            await check_and_assign_roles(member)
            
        except Exception as e:
            results.append(f"❌ {member.mention}: Ошибка - {str(e)[:50]}")
    
    # Создаем итоговый отчет
    final_embed = discord.Embed(
        title="✅ Массовая выдача завершена",
        color=COLORS['success'] if success_count == len(members) else COLORS['warning']
    )
    
    final_embed.add_field(
        name="📊 Результаты",
        value=f"✅ Успешно: {success_count}/{len(members)} пользователей\n"
              f"❌ Ошибки: {len(members) - success_count}",
        inline=False
    )
    
    final_embed.add_field(
        name="📝 Детали операции",
        value=f"• Поинтов каждому: **{amount}**\n"
              f"• Причина: **{reason}**\n"
              f"• Выдал: {ctx.author.mention}",
        inline=False
    )
    
    # Показываем первые 15 результатов
    if len(results) <= 15:
        final_embed.add_field(
            name="👥 Результаты по пользователям",
            value="\n".join(results[:15]),
            inline=False
        )
    else:
        # Показываем только статистику для большого количества
        final_embed.add_field(
            name="ℹ️ Информация",
            value=f"Обработано {len(members)} пользователей",
            inline=False
        )
        
        # Можно добавить кнопку для просмотра деталей
        if len(results) <= 100:  # Если не слишком много для файла
            # Создаем файл с результатами
            file_content = "Результаты массовой выдачи поинтов:\n\n"
            file_content += f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            file_content += f"Количество поинтов: {amount}\n"
            file_content += f"Причина: {reason}\n"
            file_content += f"Выдал: {ctx.author.display_name} ({ctx.author.id})\n"
            file_content += f"Сервер: {ctx.guild.name} ({ctx.guild.id})\n\n"
            file_content += "Детали:\n" + "\n".join(results)
            
            # Сохраняем во временный файл
            filename = f"mass_addpoints_{ctx.guild.id}_{int(datetime.now().timestamp())}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(file_content)
            
            # Отправляем файл
            file = discord.File(filename)
            await safe_send(ctx, f"📋 Полные результаты для {len(members)} пользователей:", file=file)
            
            # Удаляем временный файл
            os.remove(filename)
    
    await safe_edit(message, embed=final_embed)

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ КЛАНАМИ ==========

@bot.command(name='addclan')
@is_admin()
async def add_clan(ctx, clan_type: str, name: str, tag: str = None, *, description: str = None):
    """
    Добавить клан в базу данных
    
    Типы кланов:
    - ally - союзники
    - enemy - враги
    - peace - нейтральные/пис
    
    Примеры:
    !addclan ally "Название клана" [TAG] [описание]
    !addclan enemy "Враждебный клан" ENMY "Описание врага"
    !addclan peace "Пис клан" PEACE "Нейтральные отношения"
    """
    if clan_manager is None:
        embed = discord.Embed(
            title="❌ Система не готова",
            description="База данных еще подключается. Попробуйте через несколько секунд.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Проверяем тип клана
    valid_types = ['ally', 'enemy', 'peace']
    clan_type = clan_type.lower()
    
    if clan_type not in valid_types:
        embed = discord.Embed(
            title="❌ Неверный тип клана",
            description=f"Доступные типы: {', '.join(valid_types)}\n"
                       f"• ally - союзники\n"
                       f"• enemy - враги\n"
                       f"• peace - нейтральные/пис",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Добавляем клан
    success, message = await clan_manager.add_clan(
        ctx.guild.id, name, clan_type, tag, description, ctx.author.id
    )
    
    if success:
        embed = discord.Embed(
            title=f"{clan_manager.get_type_emoji(clan_type)} Клан добавлен",
            description=message,
            color=COLORS['success']
        )
        
        embed.add_field(name="🏷️ Название", value=f"**{name}**", inline=True)
        if tag:
            embed.add_field(name="📌 Тег", value=f"`[{tag}]`", inline=True)
        embed.add_field(name="📂 Категория", value=clan_manager.get_type_name(clan_type), inline=True)
        
        if description:
            embed.add_field(name="📝 Описание", value=description, inline=False)
        
        embed.add_field(name="👤 Добавил", value=ctx.author.mention, inline=True)
        
        # Показываем общее количество
        count = await clan_manager.get_clan_count(ctx.guild.id)
        embed.set_footer(text=f"Всего кланов в базе: {count}")
    else:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=message,
            color=COLORS['error']
        )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='removeclan')
@is_admin()
async def remove_clan(ctx, clan_type: str = None, *, name: str):
    """
    Удалить клан из базы данных
    
    Примеры:
    !removeclan ally "Название клана" - удалить из союзников
    !removeclan enemy "Название клана" - удалить из врагов
    !removeclan peace "Название клана" - удалить из нейтральных
    !removeclan "Название клана" - удалить из всех категорий
    """
    if clan_manager is None:
        embed = discord.Embed(
            title="❌ Система не готова",
            description="База данных еще подключается. Попробуйте через несколько секунд.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if clan_type and clan_type.lower() not in ['ally', 'enemy', 'peace']:
        # Если clan_type не является допустимым типом, считаем его частью названия
        name = f"{clan_type} {name}"
        clan_type = None
    
    if clan_type:
        clan_type = clan_type.lower()
    
    # Запрашиваем подтверждение
    embed = discord.Embed(
        title="⚠️ Подтверждение удаления",
        description=f"Вы уверены, что хотите удалить клан **{name}**" + 
                   (f" из категории **{clan_manager.get_type_name(clan_type)}**?" if clan_type else " из всех категорий?"),
        color=COLORS['warning']
    )
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        # Удаляем клан
        success, message = await clan_manager.remove_clan(ctx.guild.id, name, clan_type)
        
        if success:
            result_embed = discord.Embed(
                title="✅ Клан удален",
                description=message,
                color=COLORS['success']
            )
        else:
            result_embed = discord.Embed(
                title="❌ Ошибка",
                description=message,
                color=COLORS['error']
            )
        
        await interaction.response.edit_message(embed=result_embed, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Удаление отменено",
            color=COLORS['warning']
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)
    
    confirm_button = discord.ui.Button(label="✅ Подтвердить", style=discord.ButtonStyle.danger)
    cancel_button = discord.ui.Button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    
    confirm_button.callback = confirm_callback
    cancel_button.callback = cancel_callback
    
    view.add_item(confirm_button)
    view.add_item(cancel_button)
    
    await safe_send(ctx, embed=embed, view=view)

@bot.command(name='allyclans')
async def ally_clans(ctx):
    """Показать все союзные кланы (ALLY)"""
    if clan_manager is None:
        embed = discord.Embed(
            title="⏳ Загрузка данных",
            description="Система кланов инициализируется. Попробуйте через несколько секунд.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    await show_clans_by_type(ctx, 'ally', '🤝 Союзные кланы (ALLY)')

@bot.command(name='enemyclans')
async def enemy_clans(ctx):
    """Показать все вражеские кланы (ENEMY)"""
    if clan_manager is None:
        embed = discord.Embed(
            title="⏳ Загрузка данных",
            description="Система кланов инициализируется. Попробуйте через несколько секунд.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    await show_clans_by_type(ctx, 'enemy', '⚔️ Вражеские кланы (ENEMY)')

@bot.command(name='peaceclans')
async def peace_clans(ctx):
    """Показать все нейтральные кланы (PEACE/ПИС)"""
    if clan_manager is None:
        embed = discord.Embed(
            title="⏳ Загрузка данных",
            description="Система кланов инициализируется. Попробуйте через несколько секунд.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    await show_clans_by_type(ctx, 'peace', '🕊️ Нейтральные кланы (PEACE)')

async def show_clans_by_type(ctx, clan_type: str, title: str):
    """Вспомогательная функция для отображения кланов по типу"""
    clans = await clan_manager.get_clans_by_type(ctx.guild.id, clan_type)
    
    if not clans:
        embed = discord.Embed(
            title=title,
            description=f"❌ В этой категории пока нет кланов.\nДобавьте с помощью `!addclan {clan_type} \"название\"`",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title=title,
        color=COLORS['info']
    )
    
    for clan in clans:
        # Формируем название с тегом
        clan_name = clan['name']
        if clan['tag']:
            clan_name = f"[{clan['tag']}] {clan_name}"
        
        # Информация о клане
        info = []
        
        # Добавляем описание если есть
        if clan['description']:
            info.append(f"📝 {clan['description']}")
        
        # Добавляем информацию о добавившем
        try:
            added_by = await ctx.guild.fetch_member(clan['added_by'])
            added_by_name = added_by.display_name if added_by else f"ID: {clan['added_by']}"
        except:
            added_by_name = f"ID: {clan['added_by']}"
        
        info.append(f"👤 Добавил: {added_by_name}")
        info.append(f"🕒 {clan['added_at'].strftime('%d.%m.%Y')}")
        
        embed.add_field(
            name=clan_name,
            value="\n".join(info) if info else "Нет дополнительной информации",
            inline=False
        )
    
    # Добавляем статистику
    count = len(clans)
    total_clans = await clan_manager.get_clan_count(ctx.guild.id)
    
    embed.set_footer(text=f"Всего в категории: {count} | Всего кланов на сервере: {total_clans}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='allclans')
async def all_clans(ctx):
    """Показать все кланы (союзники, враги, нейтральные)"""
    if clan_manager is None:
        embed = discord.Embed(
            title="⏳ Загрузка данных",
            description="Система кланов инициализируется. Попробуйте через несколько секунд.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    clans = await clan_manager.get_all_clans(ctx.guild.id)
    
    if not clans:
        embed = discord.Embed(
            title="📋 Список кланов пуст",
            description="Добавьте кланы с помощью команды `!addclan`",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="📋 Все кланы на сервере",
        description=f"Всего кланов: **{len(clans)}**",
        color=COLORS['info']
    )
    
    # Группируем по типам
    ally_clans_list = [c for c in clans if c['clan_type'] == 'ally']
    enemy_clans_list = [c for c in clans if c['clan_type'] == 'enemy']
    peace_clans_list = [c for c in clans if c['clan_type'] == 'peace']
    
    # Союзники
    if ally_clans_list:
        ally_text = []
        for clan in ally_clans_list[:10]:  # Показываем не больше 10
            name = clan['name']
            if clan['tag']:
                name = f"[{clan['tag']}] {name}"
            ally_text.append(f"• {name}")
        
        if len(ally_clans_list) > 10:
            ally_text.append(f"*... и ещё {len(ally_clans_list) - 10}*")
        
        embed.add_field(
            name=f"🤝 Союзники ({len(ally_clans_list)})",
            value="\n".join(ally_text) if ally_text else "Нет союзников",
            inline=False
        )
    
    # Нейтральные
    if peace_clans_list:
        peace_text = []
        for clan in peace_clans_list[:10]:
            name = clan['name']
            if clan['tag']:
                name = f"[{clan['tag']}] {name}"
            peace_text.append(f"• {name}")
        
        if len(peace_clans_list) > 10:
            peace_text.append(f"*... и ещё {len(peace_clans_list) - 10}*")
        
        embed.add_field(
            name=f"🕊️ Нейтральные ({len(peace_clans_list)})",
            value="\n".join(peace_text) if peace_text else "Нет нейтральных",
            inline=False
        )
    
    # Враги
    if enemy_clans_list:
        enemy_text = []
        for clan in enemy_clans_list[:10]:
            name = clan['name']
            if clan['tag']:
                name = f"[{clan['tag']}] {name}"
            enemy_text.append(f"• {name}")
        
        if len(enemy_clans_list) > 10:
            enemy_text.append(f"*... и ещё {len(enemy_clans_list) - 10}*")
        
        embed.add_field(
            name=f"⚔️ Враги ({len(enemy_clans_list)})",
            value="\n".join(enemy_text) if enemy_text else "Нет врагов",
            inline=False
        )
    
    # Если кланов слишком много, добавляем статистику
    if len(clans) > 30:
        embed.add_field(
            name="📊 Статистика",
            value=f"Для просмотра полного списка используйте:\n"
                  f"• `!allyclans` - союзники\n"
                  f"• `!enemyclans` - враги\n"
                  f"• `!peaceclans` - нейтральные",
            inline=False
        )
    
    embed.set_footer(text="Для добавления кланов используйте !addclan")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='claninfo')
async def clan_info(ctx, clan_type: str, *, name: str):
    """
    Показать подробную информацию о клане
    
    Примеры:
    !claninfo ally "Название клана"
    !claninfo enemy "Название клана"
    !claninfo peace "Название клана"
    """
    if clan_manager is None:
        embed = discord.Embed(
            title="⏳ Загрузка данных",
            description="Система кланов инициализируется. Попробуйте через несколько секунд.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    valid_types = ['ally', 'enemy', 'peace']
    clan_type = clan_type.lower()
    
    if clan_type not in valid_types:
        embed = discord.Embed(
            title="❌ Неверный тип клана",
            description=f"Доступные типы: {', '.join(valid_types)}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Получаем информацию о клане
    clans = await clan_manager.get_clans_by_type(ctx.guild.id, clan_type)
    clan = None
    for c in clans:
        if c['name'].lower() == name.lower():
            clan = c
            break
    
    if not clan:
        embed = discord.Embed(
            title="❌ Клан не найден",
            description=f"Клан **{name}** не найден в категории **{clan_manager.get_type_name(clan_type)}**",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title=f"{clan_manager.get_type_emoji(clan_type)} Информация о клане {clan['name']}",
        color=COLORS['info']
    )
    
    # Основная информация
    if clan['tag']:
        embed.add_field(name="📌 Тег", value=f"`[{clan['tag']}]`", inline=True)
    
    embed.add_field(name="📂 Категория", value=clan_manager.get_type_name(clan_type), inline=True)
    
    if clan['description']:
        embed.add_field(name="📝 Описание", value=clan['description'], inline=False)
    
    # Информация о добавившем
    try:
        added_by = await ctx.guild.fetch_member(clan['added_by'])
        added_by_name = added_by.display_name if added_by else f"ID: {clan['added_by']}"
    except:
        added_by_name = f"ID: {clan['added_by']}"
    
    embed.add_field(name="👤 Добавил", value=added_by_name, inline=True)
    embed.add_field(name="🕒 Дата добавления", value=clan['added_at'].strftime('%d.%m.%Y %H:%M'), inline=True)
    
    await safe_send(ctx, embed=embed)

@bot.command(name='editclan')
@is_admin()
async def edit_clan(ctx, clan_type: str, name: str, field: str, *, value: str):
    """
    Редактировать информацию о клане
    
    Поля для редактирования:
    - tag - изменить тег
    - desc - изменить описание
    
    Примеры:
    !editclan ally "Клан" tag NEWTAG
    !editclan enemy "Клан" desc "Новое описание"
    """
    if clan_manager is None:
        embed = discord.Embed(
            title="❌ Система не готова",
            description="База данных еще подключается. Попробуйте через несколько секунд.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    valid_types = ['ally', 'enemy', 'peace']
    clan_type = clan_type.lower()
    
    if clan_type not in valid_types:
        embed = discord.Embed(
            title="❌ Неверный тип клана",
            description=f"Доступные типы: {', '.join(valid_types)}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    field = field.lower()
    
    if field == 'tag':
        # Обновляем тег
        success, message = await clan_manager.update_clan_tag(
            ctx.guild.id, name, clan_type, value
        )
        embed = discord.Embed(
            title="✅ Тег обновлен" if success else "❌ Ошибка",
            description=message,
            color=COLORS['success'] if success else COLORS['error']
        )
        
    elif field == 'desc' or field == 'description':
        # Обновляем описание
        success, message = await clan_manager.update_clan_description(
            ctx.guild.id, name, clan_type, value
        )
        embed = discord.Embed(
            title="✅ Описание обновлено" if success else "❌ Ошибка",
            description=message,
            color=COLORS['success'] if success else COLORS['error']
        )
        
    else:
        embed = discord.Embed(
            title="❌ Неверное поле",
            description=f"Доступные поля: tag, desc",
            color=COLORS['error']
        )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='clearclans')
@is_admin()
async def clear_clans(ctx):
    """Удалить ВСЕ кланы на сервере"""
    if clan_manager is None:
        embed = discord.Embed(
            title="❌ Система не готова",
            description="База данных еще подключается. Попробуйте через несколько секунд.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    count = await clan_manager.get_clan_count(ctx.guild.id)
    
    if count == 0:
        embed = discord.Embed(
            title="ℹ️ Нет кланов",
            description="На сервере нет кланов для удаления",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="⚠️ ОПАСНОЕ ДЕЙСТВИЕ",
        description=f"Вы уверены, что хотите удалить ВСЕ кланы ({count} шт.)?\nЭто действие необратимо!",
        color=COLORS['error']
    )
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        await clan_manager.clear_all_clans(ctx.guild.id)
        
        confirm_embed = discord.Embed(
            title="✅ Все кланы удалены",
            description=f"Удалено {count} кланов",
            color=COLORS['success']
        )
        await interaction.response.edit_message(embed=confirm_embed, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Удаление отменено",
            color=COLORS['warning']
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)
    
    confirm_button = discord.ui.Button(label="✅ Подтвердить", style=discord.ButtonStyle.danger)
    cancel_button = discord.ui.Button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    
    confirm_button.callback = confirm_callback
    cancel_button.callback = cancel_callback
    
    view.add_item(confirm_button)
    view.add_item(cancel_button)
    
    await safe_send(ctx, embed=embed, view=view)

@bot.command(name='searchclan')
async def search_clan(ctx, *, search_term: str):
    """Поиск клана по названию"""
    if clan_manager is None:
        embed = discord.Embed(
            title="⏳ Загрузка данных",
            description="Система кланов инициализируется. Попробуйте через несколько секунд.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    clans = await clan_manager.search_clan(ctx.guild.id, search_term)
    
    if not clans:
        embed = discord.Embed(
            title="🔍 Результаты поиска",
            description=f"Кланы по запросу **{search_term}** не найдены",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title=f"🔍 Результаты поиска: {search_term}",
        description=f"Найдено кланов: **{len(clans)}**",
        color=COLORS['info']
    )
    
    # Группируем по типам
    for clan_type in ['ally', 'peace', 'enemy']:
        type_clans = [c for c in clans if c['clan_type'] == clan_type]
        if type_clans:
            clan_names = []
            for clan in type_clans[:5]:  # Показываем не больше 5 на категорию
                name = clan['name']
                if clan['tag']:
                    name = f"[{clan['tag']}] {name}"
                clan_names.append(f"• {name}")
            
            if len(type_clans) > 5:
                clan_names.append(f"*... и ещё {len(type_clans) - 5}*")
            
            embed.add_field(
                name=f"{clan_manager.get_type_emoji(clan_type)} {clan_manager.get_type_name(clan_type)} ({len(type_clans)})",
                value="\n".join(clan_names),
                inline=False
            )
    
    await safe_send(ctx, embed=embed)

# ========== КОМАНДА HELP ==========

@bot.command(name='help')
async def help_command(ctx):
    """Показать все команды"""
    # Проверяем, является ли пользователь админом
    is_user_admin = False
    try:
        # Создаем временную проверку админских прав
        is_user_admin = ctx.author.guild_permissions.administrator or any(
            admin_role_id in [role.id for role in ctx.author.roles] 
            for admin_role_id in ADMIN_ROLE_IDS
        )
    except:
        pass
    
    embed = discord.Embed(
        title="📚 Помощь по командам бота",
        description=f"**Префикс команд:** `{PREFIX}`\n"
                   f"Все команды начинаются с префикса `{PREFIX}`\n"
                   f"Пример: `{PREFIX}points @user`",
        color=COLORS['info']
    )
    
    # ========== КОМАНДЫ ДЛЯ ВСЕХ ==========
    embed.add_field(
        name="👤 **Команды для всех пользователей**",
        value=f"```{PREFIX}points [@пользователь]``` - Проверить свои или чужие поинты\n"
              f"```{PREFIX}leaderboard [страница]``` - Показать топ пользователей по поинтам\n"
              f"```{PREFIX}roles``` - Показать систему ролей за поинты\n"
              f"```{PREFIX}ping``` - Проверить статус бота и задержку\n"
              f"```{PREFIX}help``` - Показать это сообщение\n"
              f"```{PREFIX}allyclans``` - Показать список союзных кланов\n"
              f"```{PREFIX}enemyclans``` - Показать список вражеских кланов\n"
              f"```{PREFIX}peaceclans``` - Показать список нейтральных кланов\n"
              f"```{PREFIX}allclans``` - Показать все кланы на сервере\n"
              f"```{PREFIX}claninfo [тип] \"название\"``` - Информация о конкретном клане\n"
              f"```{PREFIX}searchclan \"название\"``` - Поиск клана по названию",
        inline=False
    )
    
    # Если пользователь админ - показываем админские команды
    if is_user_admin:
        # ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ ПОИНТАМИ ==========
        embed.add_field(
            name="💰 **Управление поинтами (Админ)**",
            value=f"```{PREFIX}addpoints @user количество [причина]``` - Выдать поинты пользователю\n"
                  f"```{PREFIX}removepoints @user количество [причина]``` - Забрать поинты у пользователя\n"
                  f"```{PREFIX}setpoints @user количество [причина]``` - Установить точное количество поинтов\n"
                  f"```{PREFIX}addpoints_multi количество @user1 @user2 ...``` - Выдать поинты нескольким пользователям\n"
                  f"```{PREFIX}resetpoints``` - СБРОСИТЬ ВСЕ поинты на сервере (требует подтверждения)",
            inline=False
        )
        
        # ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ РОЛЯМИ ЗА ПОИНТЫ ==========
        embed.add_field(
            name="🎭 **Управление ролями за поинты (Админ)**",
            value=f"```{PREFIX}addrole 200 \"название роли\"``` - Добавить новую роль за поинты\n"
                  f"```{PREFIX}removerole 200``` - Удалить роль по количеству поинтов\n"
                  f"```{PREFIX}removerolebyname \"название роли\"``` - Удалить роль по названию\n"
                  f"```{PREFIX}editrole 200 250``` - Изменить количество поинтов для роли\n"
                  f"```{PREFIX}editrole 200 \"новое название\"``` - Изменить название роли\n"
                  f"```{PREFIX}editrole 200 250 \"новое название\"``` - Изменить и поинты, и название\n"
                  f"```{PREFIX}setrolecolor 200 #FF0000``` - Установить цвет роли (HEX код)\n"
                  f"```{PREFIX}setrolecolor 200 red``` - Или по названию цвета\n"
                  f"```{PREFIX}reorderroles``` - Пересоздать все роли в правильном порядке\n"
                  f"```{PREFIX}updateroles``` - Обновить роли для ВСЕХ участников сервера\n"
                  f"```{PREFIX}saveroles``` - Сохранить конфигурацию ролей в файл",
            inline=False
        )
        
        # ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ АДМИНСКИМИ РОЛЯМИ ==========
        embed.add_field(
            name="👑 **Управление админскими ролями (Админ)**",
            value=f"```{PREFIX}addadminrole @роль``` - Добавить роль в список админов бота\n"
                  f"```{PREFIX}removeadminrole @роль``` - Удалить роль из списка админов\n"
                  f"```{PREFIX}listadminroles``` - Показать все текущие админские роли\n"
                  f"```{PREFIX}clearadminroles``` - Удалить ВСЕ админские роли (требует подтверждения)",
            inline=False
        )
        
        # ========== КОМАНДЫ ДЛЯ БЛОКИРОВКИ КАНАЛОВ ==========
        embed.add_field(
            name="🔒 **Управление блокировкой каналов (Админ)**",
            value=f"```{PREFIX}addchannel #канал``` - Добавить канал в список для блокировки\n"
                  f"```{PREFIX}removechannel [#канал]``` - Удалить канал из списка\n"
                  f"```{PREFIX}listchannels``` - Показать все каналы в списке\n"
                  f"```{PREFIX}lockchannels @роль [тип]``` - Заблокировать каналы для роли\n"
                  f"```{PREFIX}unlockchannels [@роль]``` - Разблокировать каналы для роли\n"
                  f"```{PREFIX}lockinfo``` - Показать все активные блокировки\n"
                  f"```{PREFIX}clearlocks``` - Удалить ВСЕ блокировки на сервере",
            inline=False
        )
        
        # ========== БЫСТРЫЕ КОМАНДЫ ДЛЯ КОНКРЕТНОЙ РОЛИ ==========
        embed.add_field(
            name="⚡ **Быстрые команды для роли (ID: 1431589898183512129)**",
            value=f"```{PREFIX}lock [тип]``` - Быстрая блокировка каналов для роли\n"
                  f"```{PREFIX}unlock``` - Быстрая разблокировка каналов для роли\n"
                  f"**Типы блокировки:** `send` (только писать), `view` (скрыть), `both` (полная)",
            inline=False
        )
        
        # ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ КЛАНАМИ ==========
        embed.add_field(
            name="🏰 **Управление кланами (Админ)**",
            value=f"```{PREFIX}addclan ally \"название\" [тег] [описание]``` - Добавить союзника\n"
                  f"```{PREFIX}addclan enemy \"название\" [тег] [описание]``` - Добавить врага\n"
                  f"```{PREFIX}addclan peace \"название\" [тег] [описание]``` - Добавить нейтрального\n"
                  f"```{PREFIX}removeclan ally \"название\"``` - Удалить из союзников\n"
                  f"```{PREFIX}removeclan enemy \"название\"``` - Удалить из врагов\n"
                  f"```{PREFIX}removeclan peace \"название\"``` - Удалить из нейтральных\n"
                  f"```{PREFIX}removeclan \"название\"``` - Удалить из всех категорий\n"
                  f"```{PREFIX}editclan ally \"название\" tag NEWTAG``` - Изменить тег клана\n"
                  f"```{PREFIX}editclan ally \"название\" desc \"новое описание\"``` - Изменить описание\n"
                  f"```{PREFIX}clearclans``` - Удалить ВСЕ кланы на сервере",
            inline=False
        )
        
        # ========== ЭКСПОРТ ДАННЫХ ==========
        embed.add_field(
            name="📊 **Экспорт данных (Админ)**",
            value=f"```{PREFIX}export``` - Экспортировать все данные о поинтах в CSV файл",
            inline=False
        )
        
        # ========== ИНФОРМАЦИЯ О ТИПАХ БЛОКИРОВКИ ==========
        embed.add_field(
            name="ℹ️ **Типы блокировки каналов**",
            value="• `send` - Запрет на отправку сообщений, реакций и файлов\n"
                  "• `view` - Полное скрытие канала (не видно)\n"
                  "• `both` - Полная блокировка (скрыто + нельзя писать)",
            inline=False
        )
        
        # ========== ИНФОРМАЦИЯ О ТИПАХ КЛАНОВ ==========
        embed.add_field(
            name="📋 **Типы кланов**",
            value="• `ally` - Союзники (🤝)\n"
                  "• `enemy` - Враги (⚔️)\n"
                  "• `peace` - Нейтральные/Пис (🕊️)",
            inline=False
        )
        
        # ========== ИНФОРМАЦИЯ О ЦВЕТАХ ДЛЯ РОЛЕЙ ==========
        embed.add_field(
            name="🎨 **Доступные цвета для ролей**",
            value="**HEX коды:** `#FF0000` (красный), `#00FF00` (зеленый), `#0000FF` (синий)\n"
                  "**Названия:** `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `gold`, `pink`, `brown`, `black`, `white`",
            inline=False
        )
    
    # ========== СИСТЕМНАЯ ИНФОРМАЦИЯ ==========
    # Формируем информацию об админских ролях
    admin_roles_text = []
    if ADMIN_ROLE_IDS:
        for role_id in ADMIN_ROLE_IDS[:5]:  # Показываем первые 5 ролей
            role = ctx.guild.get_role(role_id)
            if role:
                admin_roles_text.append(f"• {role.mention}")
            else:
                admin_roles_text.append(f"• Роль ID: `{role_id}`")
        
        if len(ADMIN_ROLE_IDS) > 5:
            admin_roles_text.append(f"*... и ещё {len(ADMIN_ROLE_IDS) - 5} ролей*")
    else:
        admin_roles_text = ["• Не настроены (только администраторы Discord)"]
    
    # Информация о ролях за поинты
    roles_count = len(ROLE_SETTINGS)
    if roles_count > 0:
        roles_range = f"от {min(ROLE_SETTINGS.keys())} до {max(ROLE_SETTINGS.keys())} поинтов"
    else:
        roles_range = "не настроены"
    
    embed.add_field(
        name="ℹ️ **Информация о системе**",
        value=f"**🤖 Бот:** {bot.user.name}\n"
              f"**📊 Серверов:** {len(bot.guilds)}\n"
              f"**🌐 Порт веб-сервера:** {PORT}\n"
              f"**🗄️ База данных:** PostgreSQL\n"
              f"**🎭 Ролей за поинты:** {roles_count} ({roles_range})\n"
              f"**👑 Админские роли:**\n" + "\n".join(admin_roles_text),
        inline=False
    )
    
    # ========== ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ ==========
    embed.add_field(
        name="📝 **Примеры использования**",
        value=f"`{PREFIX}points` - проверить свои поинты\n"
              f"`{PREFIX}points @User` - проверить поинты пользователя\n"
              f"`{PREFIX}leaderboard` - топ-10 пользователей\n"
              f"`{PREFIX}addpoints @User 100 Спасибо за активность` - выдать поинты\n"
              f"`{PREFIX}addrole 500 \"Легендарный рейдер\"` - добавить новую роль\n"
              f"`{PREFIX}addadminrole @Админ` - добавить админскую роль\n"
              f"`{PREFIX}addclan ally \"Братство\" [BR] \"Наши верные союзники\"` - добавить клан",
        inline=False
    )
    
    # ========== ПОЛЕЗНЫЕ ССЫЛКИ ==========
    embed.add_field(
        name="🔗 **Полезные ссылки**",
        value="• [Документация Discord.py](https://discordpy.readthedocs.io/)\n"
              "• [Поддержка](https://discord.gg/your-server)\n"
              f"• Версия бота: v2.0",
        inline=False
    )
    
    # ========== ФУТЕР ==========
    embed.set_footer(
        text=f"Запрошено: {ctx.author.display_name} | Всего команд: {len(bot.commands)} | {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        icon_url=ctx.author.display_avatar.url
    )
    
    # Добавляем иконку бота если есть
    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    
    await safe_send(ctx, embed=embed)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ БЕЗОПАСНОЙ ОТПРАВКИ СООБЩЕНИЙ ==========

async def safe_send(ctx, content=None, embed=None, file=None, view=None):
    """Безопасная отправка сообщения с обработкой ошибок прав"""
    try:
        if file:
            return await ctx.send(content=content, embed=embed, file=file, view=view)
        else:
            return await ctx.send(content=content, embed=embed, view=view)
    except discord.Forbidden:
        # Нет прав на отправку сообщений в этом канале
        try:
            # Пробуем отправить в ЛС автору
            await ctx.author.send("❌ У меня нет прав на отправку сообщений в том канале, где вы использовали команду!")
        except:
            # Если и в ЛС не могу отправить, просто логируем
            logger.error(f"Не могу отправить сообщение пользователю {ctx.author} (нет прав в канале и ЛС)")
        return None
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")
        return None

async def safe_edit(message, content=None, embed=None, view=None):
    """Безопасное редактирование сообщения"""
    try:
        return await message.edit(content=content, embed=embed, view=view)
    except discord.Forbidden:
        logger.error("Нет прав на редактирование сообщения")
        return None
    except Exception as e:
        logger.error(f"Ошибка при редактировании сообщения: {e}")
        return None

# ========== ОБРАБОТКА ОШИБОК ==========

@bot.event
async def on_command_error(ctx, error):
    """Обработка ошибок команд"""
    
    # Игнорируем команды, которые не найдены
    if isinstance(error, commands.CommandNotFound):
        return
    
    # Проверяем, можем ли мы отправить сообщение об ошибке
    if not ctx.channel.permissions_for(ctx.guild.me).send_messages:
        # Нет прав на отправку сообщений - просто логируем
        logger.error(f"Ошибка команды в канале без прав на отправку: {error}")
        return
    
    try:
        if isinstance(error, commands.CheckFailure):
            # Преобразуем ID ролей в строки для отображения
            admin_role_ids_str = ', '.join(str(role_id) for role_id in ADMIN_ROLE_IDS) if ADMIN_ROLE_IDS else "не указаны"
            
            embed = discord.Embed(
                title="❌ Недостаточно прав",
                description=f"Только пользователи с административными правами или ролями с ID: **{admin_role_ids_str}** могут использовать эту команду!",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="❌ Не хватает аргументов",
                description=f"Используйте `{PREFIX}help` для справки по командам",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            
        elif isinstance(error, commands.BadArgument):
            embed = discord.Embed(
                title="❌ Неправильные аргументы",
                description="Проверьте правильность введенных данных",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            
        elif isinstance(error, commands.TooManyArguments):
            embed = discord.Embed(
                title="❌ Слишком много аргументов",
                description=f"Используйте `{PREFIX}help` для справки по командам",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            
        elif isinstance(error, commands.CommandOnCooldown):
            embed = discord.Embed(
                title="⏳ Команда на перезарядке",
                description=f"Попробуйте снова через {error.retry_after:.1f} секунд",
                color=COLORS['warning']
            )
            await safe_send(ctx, embed=embed)
            
        else:
            logger.error(f"Необработанная ошибка команды {ctx.command}: {error}")
            
            # Для некоторых ошибок не нужно показывать пользователю
            if isinstance(error, discord.Forbidden):
                # Ошибка прав - уже обработана в safe_send
                pass
            else:
                embed = discord.Embed(
                    title="❌ Неизвестная ошибка",
                    description="Произошла неизвестная ошибка. Администратор уже уведомлен.",
                    color=COLORS['error']
                )
                await safe_send(ctx, embed=embed)
                
    except Exception as e:
        # Предотвращаем бесконечный цикл ошибок в обработчике ошибок
        logger.error(f"Критическая ошибка в обработчике ошибок: {e}")

# ========== ЗАПУСК БОТА ==========

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК DISCORD POINTS BOT")
    logger.info("=" * 50)
    logger.info(f"🤖 Префикс команд: {PREFIX}")
    logger.info(f"👑 Админские роли: {ADMIN_ROLE_IDS}")
    logger.info(f"🌐 Порт веб-сервера: {PORT}")
    logger.info(f"🗄️  База данных: PostgreSQL")
    logger.info("🔄 Режим: 24/7 с веб-сервером")
    logger.info("=" * 50)
    
    # Проверяем доступность порта перед запуском
    try:
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.bind(('0.0.0.0', PORT))
        test_sock.close()
        logger.info(f"✅ Порт {PORT} доступен для привязки")
    except Exception as e:
        logger.error(f"❌ Порт {PORT} НЕДОСТУПЕН: {e}")
        logger.warning("⚠️ Бот будет запущен, но веб-сервер может не работать")
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Ошибка авторизации! Проверьте токен бота.")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
