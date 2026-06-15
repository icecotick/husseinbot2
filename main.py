import discord
from discord.ext import commands
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from dotenv import load_dotenv
import asyncpg
import asyncio
from aiohttp import web
import socket
import time
import aiohttp

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
# ID ролей модераторов (могут блокировать каналы, но не управлять ролями и т.д.)
MOD_ROLE_IDS = [int(role_id.strip()) for role_id in os.getenv('MOD_ROLE_IDS', '').split(',') if role_id.strip()]
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

# Настройки ролей для каждого сервера
GUILD_ROLE_SETTINGS = {}
GUILD_ROLE_COLORS = {}

# Стандартные настройки для новых серверов
DEFAULT_ROLE_SETTINGS = {
    200: 'raider newgen',
    400: 'raider scout', 
    800: 'raider striker', 
    1200: 'raider heavy', 
    1600: 'raider legend',
    2400: 'raider who lives on raids', 
    3200: 'raid moderator', 
    4000: 'raider commander'
}

DEFAULT_ROLE_COLORS = {
    'raider newgen': discord.Color.green(),
    'raider scout': discord.Color.blue(),
    'raider striker': discord.Color.orange(),
    'raider heavy': discord.Color.yellow(),
    'raider legend': discord.Color.purple(),
    'raider who lives on raids': discord.Color.blue(),
    'raid moderator': discord.Color.red(),
    'raider commander': discord.Color.gold()
}

# ========== БАЗА ДАННЫХ ==========

class Database:
    def __init__(self):
        self.pool = None
    
    async def connect(self):
        try:
            self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
            await self.init_tables()
            logger.info("✅ Подключено к базе данных")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к БД: {e}")
            return False
    
    async def init_tables(self):
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
            
            # Таблица для хранения настроек ролей за поинты
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS role_settings (
                    guild_id BIGINT,
                    points INTEGER,
                    role_name TEXT,
                    role_color TEXT,
                    PRIMARY KEY (guild_id, points)
                )
            ''')
            
            logger.info("✅ Все таблицы инициализированы")
    
    async def get_user_points(self, user_id: int, guild_id: int) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT points FROM users WHERE user_id = $1 AND guild_id = $2',
                user_id, guild_id
            )
            return result['points'] if result else 0
    
    async def add_points(self, user_id: int, guild_id: int, amount: int, admin_id: int, reason: str = "Выдано админом"):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (user_id, guild_id, points)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, guild_id) 
                DO UPDATE SET points = users.points + EXCLUDED.points
            ''', user_id, guild_id, amount)
            
            await conn.execute('''
                INSERT INTO transactions (user_id, guild_id, amount, admin_id, reason)
                VALUES ($1, $2, $3, $4, $5)
            ''', user_id, guild_id, amount, admin_id, reason)
            
            return await self.get_user_points(user_id, guild_id)
    
    async def remove_points(self, user_id: int, guild_id: int, amount: int, admin_id: int, reason: str = "Изъято админом"):
        async with self.pool.acquire() as conn:
            current = await self.get_user_points(user_id, guild_id)
            new_amount = max(0, current - amount)
            
            await conn.execute('''
                UPDATE users SET points = $1 
                WHERE user_id = $2 AND guild_id = $3
            ''', new_amount, user_id, guild_id)
            
            await conn.execute('''
                INSERT INTO transactions (user_id, guild_id, amount, admin_id, reason)
                VALUES ($1, $2, $3, $4, $5)
            ''', user_id, guild_id, -amount, admin_id, reason)
            
            return new_amount
    
    async def set_points(self, user_id: int, guild_id: int, amount: int, admin_id: int, reason: str = "Установлено админом"):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (user_id, guild_id, points)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, guild_id) 
                DO UPDATE SET points = EXCLUDED.points
            ''', user_id, guild_id, amount)
            
            current_points = await self.get_user_points(user_id, guild_id)
            difference = amount - current_points
            await conn.execute('''
                INSERT INTO transactions (user_id, guild_id, amount, admin_id, reason)
                VALUES ($1, $2, $3, $4, $5)
            ''', user_id, guild_id, difference, admin_id, reason)
            
            return amount
    
    async def get_leaderboard(self, guild_id: int, limit: int = 10):
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT user_id, points FROM users 
                WHERE guild_id = $1 AND points > 0
                ORDER BY points DESC 
                LIMIT $2
            ''', guild_id, limit)
    
    async def get_user_position(self, user_id: int, guild_id: int) -> int:
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
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM users WHERE guild_id = $1', guild_id)
            await conn.execute('DELETE FROM transactions WHERE guild_id = $1', guild_id)
    
    # Методы для списка каналов
    async def add_channel_to_list(self, guild_id: int, channel_id: int, channel_name: str, added_by: int):
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
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                'SELECT * FROM channel_list WHERE guild_id = $1 ORDER BY added_at',
                guild_id
            )
    
    async def get_channel_count(self, guild_id: int):
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT COUNT(*) as count FROM channel_list WHERE guild_id = $1',
                guild_id
            )
            return result['count'] if result else 0
    
    # Методы для блокировок
    async def add_channel_lock(self, guild_id: int, channel_id: int, role_id: int, lock_type: str, created_by: int):
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
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM locked_channels WHERE guild_id = $1', guild_id)
    
    # Методы для ролей
    async def save_role_settings(self, guild_id: int, role_settings: dict, role_colors: dict):
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM role_settings WHERE guild_id = $1', guild_id)
            
            for points, role_name in role_settings.items():
                color = str(role_colors.get(role_name, discord.Color.default()))
                await conn.execute(
                    'INSERT INTO role_settings (guild_id, points, role_name, role_color) VALUES ($1, $2, $3, $4)',
                    guild_id, points, role_name, color
                )
            logger.info(f"✅ Настройки ролей сохранены для сервера {guild_id}")
    
    async def load_role_settings(self, guild_id: int):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT points, role_name, role_color FROM role_settings WHERE guild_id = $1 ORDER BY points',
                guild_id
            )
            
            role_settings = {}
            role_colors = {}
            
            for row in rows:
                role_settings[row['points']] = row['role_name']
                role_colors[row['role_name']] = row['role_color']
            
            return role_settings, role_colors

# Создаем экземпляр БД
db = Database()

# ========== ВЕБ-СЕРВЕР (только для health check и пинга) ==========

async def handle_root(request):
    return web.Response(
        text="✅ Discord Points Bot is running!\n"
             f"📊 Status: Online\n"
             f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
             f"🌐 Port: {PORT}\n"
             f"🔄 Bot is ready to accept commands!"
    )

async def handle_ping(request):
    return web.Response(text="pong")

async def handle_health(request):
    status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "discord-points-bot",
        "port": PORT,
        "bot_ready": bot.is_ready()
    }
    
    if hasattr(db, 'pool') and db.pool:
        status["database"] = "connected"
    else:
        status["database"] = "connecting"
    
    return web.json_response(status)

async def start_web_server():
    try:
        app = web.Application()
        
        app.router.add_get('/', handle_root)
        app.router.add_get('/ping', handle_ping)
        app.router.add_get('/health', handle_health)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"🌐 Веб-сервер запущен на порту {PORT}")
        logger.info(f"🔗 Health check доступен по адресу: http://0.0.0.0:{PORT}/health")
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка запуска веб-сервера: {e}")
        return False

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С РОЛЯМИ ==========

def is_admin():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        author_role_ids = [role.id for role in ctx.author.roles]
        return any(admin_role_id in author_role_ids for admin_role_id in ADMIN_ROLE_IDS)
    return commands.check(predicate)

def is_mod():
    """Проверка, является ли пользователь модератором (имеет права на блокировку каналов)"""
    async def predicate(ctx):
        # Проверка прав администратора Discord
        if ctx.author.guild_permissions.administrator:
            return True
        
        # Проверка кастомных админских ролей
        author_role_ids = [role.id for role in ctx.author.roles]
        if any(admin_role_id in author_role_ids for admin_role_id in ADMIN_ROLE_IDS):
            return True
        
        # Проверка модераторских ролей
        if any(mod_role_id in author_role_ids for mod_role_id in MOD_ROLE_IDS):
            return True
        
        return False
    return commands.check(predicate)

def is_admin_or_mod():
    """Проверка, является ли пользователь администратором или модератором"""
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        author_role_ids = [role.id for role in ctx.author.roles]
        if any(admin_role_id in author_role_ids for admin_role_id in ADMIN_ROLE_IDS):
            return True
        if any(mod_role_id in author_role_ids for mod_role_id in MOD_ROLE_IDS):
            return True
        return False
    return commands.check(predicate)

async def check_and_assign_roles(member: discord.Member):
    try:
        guild_id = member.guild.id
        user_id = member.id
        
        role_settings, role_colors = get_guild_settings(guild_id)
        
        if not role_settings:
            return
        
        points = await db.get_user_points(user_id, guild_id)
        
        target_role_name = None
        for required_points, role_name in sorted(role_settings.items()):
            if points >= required_points:
                target_role_name = role_name
        
        if not target_role_name:
            return
        
        discord_role = discord.utils.get(member.guild.roles, name=target_role_name)
        if discord_role and discord_role in member.roles:
            return
        
        for role_name in role_settings.values():
            if role_name != target_role_name:
                old_role = discord.utils.get(member.guild.roles, name=role_name)
                if old_role and old_role in member.roles:
                    try:
                        await member.remove_roles(old_role)
                    except:
                        pass
        
        if not discord_role:
            try:
                color = role_colors.get(target_role_name, discord.Color.default())
                if isinstance(color, str):
                    try:
                        if color.startswith('#'):
                            color_int = int(color[1:], 16)
                            color = discord.Color(color_int)
                        else:
                            color = discord.Color.default()
                    except:
                        color = discord.Color.default()
                
                discord_role = await member.guild.create_role(
                    name=target_role_name,
                    color=color,
                    mentionable=True,
                    reason="Автоматическое создание роли за поинты"
                )
                logger.info(f"Создана новая роль: {target_role_name} на сервере {member.guild.name}")
            except discord.Forbidden:
                logger.error(f'Недостаточно прав для создания роли {target_role_name}')
                return
            except Exception as e:
                logger.error(f'Ошибка создания роли: {e}')
                return
        
        try:
            await member.add_roles(discord_role)
            await send_role_notification(member, target_role_name, points)
            logger.info(f'Выдана роль {target_role_name} пользователю {member.display_name}')
        except discord.Forbidden:
            logger.error(f'Недостаточно прав для выдачи роли {target_role_name}')
        except Exception as e:
            logger.error(f'Ошибка выдачи роли: {e}')
            
    except Exception as e:
        logger.error(f'Ошибка в check_and_assign_roles: {e}')

async def send_role_notification(member: discord.Member, role_name: str, points: int):
    try:
        embed = discord.Embed(
            title="🎉 Новая роль получена!",
            description=f"**{member.display_name}** получил(а) новую роль!",
            color=discord.Color.green()
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
        
        if member.guild.system_channel:
            await member.guild.system_channel.send(member.mention, embed=embed)
        else:
            try:
                await member.send(embed=embed)
            except:
                pass
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")

# ========== ФУНКЦИИ ДЛЯ БЛОКИРОВКИ КАНАЛОВ ==========

async def apply_channel_lock(channel: discord.TextChannel, role: discord.Role, lock_type: str):
    try:
        overwrites = channel.overwrites_for(role)
        
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
        
        await channel.set_permissions(role, overwrite=overwrites)
        return True
    except Exception as e:
        logger.error(f"Ошибка блокировки канала: {e}")
        return False

async def remove_channel_lock(channel: discord.TextChannel, role: discord.Role):
    try:
        await channel.set_permissions(role, overwrite=None)
        return True
    except Exception as e:
        logger.error(f"Ошибка разблокировки канала: {e}")
        return False

async def lock_all_channels_in_list(guild: discord.Guild, role: discord.Role, lock_type: str):
    try:
        results = []
        channels_list = await db.get_channel_list(guild.id)
        
        if not channels_list:
            return ["❌ Список каналов пуст. Добавьте каналы с помощью !addchannel"]
        
        for channel_data in channels_list:
            try:
                channel = guild.get_channel(channel_data['channel_id'])
                if not channel:
                    try:
                        channel = await guild.fetch_channel(channel_data['channel_id'])
                    except:
                        channel = None
                
                if channel:
                    await db.add_channel_lock(
                        guild.id, channel.id, role.id, 
                        lock_type, guild.me.id
                    )
                    
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
                    await db.remove_channel_lock(guild.id, channel.id, role.id)
                    success = await remove_channel_lock(channel, role)
                    
                    if success:
                        results.append(f"✅ {channel.mention} - разблокирован для {role.mention}")
                    else:
                        results.append(f"⚠️ {channel.mention} - ошибка прав")
                else:
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

async def self_ping():
    """Периодический пинг веб-сервера, чтобы бот не засыпал"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'http://localhost:{PORT}/health', timeout=10) as resp:
                    if resp.status == 200:
                        logger.debug("Self-ping successful")
                    else:
                        logger.warning(f"Self-ping returned {resp.status}")
        except Exception as e:
            logger.error(f"Self-ping error: {e}")
        await asyncio.sleep(300)  # каждые 5 минут

@bot.event
async def on_ready():
    logger.info(f'✅ Бот {bot.user} запущен!')
    logger.info(f'📊 Серверов: {len(bot.guilds)}')
    logger.info(f'🌐 Порт веб-сервера: {PORT}')
    
    if await db.connect():
        logger.info("✅ База данных подключена")
        
        global GUILD_ROLE_SETTINGS, GUILD_ROLE_COLORS
        for guild in bot.guilds:
            try:
                loaded_settings, loaded_colors = await db.load_role_settings(guild.id)
                if loaded_settings:
                    GUILD_ROLE_SETTINGS[guild.id] = loaded_settings
                    colors = {}
                    for role_name, color_str in loaded_colors.items():
                        try:
                            if color_str and color_str.startswith('#'):
                                color_int = int(color_str[1:], 16)
                                colors[role_name] = discord.Color(color_int)
                            else:
                                colors[role_name] = discord.Color.default()
                        except:
                            colors[role_name] = discord.Color.default()
                    GUILD_ROLE_COLORS[guild.id] = colors
                    logger.info(f"✅ Загружены настройки ролей для сервера {guild.name} ({len(loaded_settings)} ролей)")
                else:
                    GUILD_ROLE_SETTINGS[guild.id] = DEFAULT_ROLE_SETTINGS.copy()
                    GUILD_ROLE_COLORS[guild.id] = DEFAULT_ROLE_COLORS.copy()
                    logger.info(f"ℹ️ Используются стандартные настройки ролей для сервера {guild.name}")
            except Exception as e:
                logger.error(f"❌ Ошибка загрузки ролей для сервера {guild.name}: {e}")
                GUILD_ROLE_SETTINGS[guild.id] = DEFAULT_ROLE_SETTINGS.copy()
                GUILD_ROLE_COLORS[guild.id] = DEFAULT_ROLE_COLORS.copy()
    else:
        logger.error("❌ Не удалось подключиться к базе данных!")
        logger.warning("⚠️ Бот будет работать без функций базы данных!")
        for guild in bot.guilds:   # <-- ИСПРАВЛЕНО: bot.guilds, а не bot.groups
            GUILD_ROLE_SETTINGS[guild.id] = DEFAULT_ROLE_SETTINGS.copy()
            GUILD_ROLE_COLORS[guild.id] = DEFAULT_ROLE_COLORS.copy()
    
    await start_web_server()
    
    # Запускаем проверку истекших голосований
    bot.loop.create_task(check_expired_vouches())
    logger.info("✅ Запущена проверка истекших голосований")
    
    # Запускаем self-ping для поддержания активности
    bot.loop.create_task(self_ping())
    logger.info("✅ Запущен self-ping (каждые 5 минут)")
    
    activity_text = f"{PREFIX}help | {len(bot.guilds)} серв."
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=activity_text
        )
    )
    
    logger.info("🚀 Бот полностью готов к работе!")
    logger.info(f"📡 Веб-сервер доступен по адресу: http://0.0.0.0:{PORT}/")

@bot.event
async def on_guild_join(guild):
    GUILD_ROLE_SETTINGS[guild.id] = DEFAULT_ROLE_SETTINGS.copy()
    GUILD_ROLE_COLORS[guild.id] = DEFAULT_ROLE_COLORS.copy()
    
    try:
        await db.save_role_settings(guild.id, GUILD_ROLE_SETTINGS[guild.id], GUILD_ROLE_COLORS[guild.id])
        logger.info(f"✅ Созданы стандартные настройки ролей для нового сервера {guild.name}")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения настроек для нового сервера {guild.name}: {e}")

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ КАНАЛАМИ ==========

@bot.command(name='addchannel')
@is_admin()
async def add_channel(ctx, channel: discord.TextChannel):
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
@is_admin_or_mod()
async def list_channels(ctx):
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

@bot.command(name='lockinfo')
@is_admin_or_mod()
async def lock_info(ctx):
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
    
    roles_dict = {}
    for lock in locks:
        role_id = lock['role_id']
        if role_id not in roles_dict:
            roles_dict[role_id] = []
        roles_dict[role_id].append(lock)
    
    for role_id, role_locks in list(roles_dict.items())[:5]:
        role = ctx.guild.get_role(role_id)
        role_name = role.mention if role else f"Роль {role_id}"
        
        lock_types = {}
        for lock in role_locks:
            lock_type = lock['lock_type']
            if lock_type not in lock_types:
                lock_types[lock_type] = []
            
            channel = ctx.guild.get_channel(lock['channel_id'])
            channel_name = channel.mention if channel else f"Канал {lock['channel_id']}"
            lock_types[lock_type].append(channel_name)
        
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
    
    channel_count = await db.get_channel_count(ctx.guild.id)
    embed.add_field(
        name="📋 Список каналов",
        value=f"Каналов в списке для блокировки: **{channel_count}**",
        inline=False
    )
    
    if len(roles_dict) > 5:
        embed.set_footer(text=f"Показано 5 из {len(roles_dict)} ролей. Используйте !listchannels для полного списка")
    
    await safe_send(ctx, embed=embed)

# ========== БЫСТРЫЕ КОМАНДЫ ДЛЯ БЛОКИРОВКИ ==========

last_locked_role = {}

@bot.command(name='lockrole')
@is_admin_or_mod()
async def lockrole_command(ctx, role: discord.Role):
    global last_locked_role
    last_locked_role[ctx.guild.id] = role.id
    
    embed = discord.Embed(
        title="✅ Роль установлена",
        description=f"Теперь команды `!lock` и `!unlock` будут работать с ролью {role.mention}",
        color=COLORS['success']
    )
    
    embed.add_field(name="ID роли", value=f"`{role.id}`", inline=True)
    embed.add_field(name="Название роли", value=f"`{role.name}`", inline=True)
    embed.add_field(
        name="Как использовать", 
        value=f"• `{PREFIX}lock [тип]` - заблокировать каналы для этой роли\n"
              f"• `{PREFIX}unlock` - разблокировать каналы для этой роли",
        inline=False
    )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='unlock')
@is_admin_or_mod()
async def unlock_command(ctx):
    global last_locked_role
    
    if ctx.guild.id not in last_locked_role:
        embed = discord.Embed(
            title="❌ Роль не установлена",
            description=f"Сначала используйте `{PREFIX}lockrole @роль` чтобы указать, с какой ролью работать!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    role_id = last_locked_role[ctx.guild.id]
    target_role = ctx.guild.get_role(role_id)
    
    if not target_role:
        del last_locked_role[ctx.guild.id]
        embed = discord.Embed(
            title="❌ Роль не найдена",
            description=f"Роль с ID `{role_id}` больше не существует на сервере.\n"
                       f"Используйте `{PREFIX}lockrole @роль` чтобы установить новую роль.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="🔓 Разблокировка каналов",
        description=f"Начинаю разблокировку каналов для роли {target_role.mention}...",
        color=COLORS['info']
    )
    
    embed.add_field(name="ID роли", value=f"`{target_role.id}`", inline=True)
    embed.add_field(name="Название роли", value=f"`{target_role.name}`", inline=True)
    
    message = await safe_send(ctx, embed=embed)
    if not message:
        return
    
    results = await unlock_all_channels_in_list(ctx.guild, target_role)
    
    success_count = sum(1 for r in results if "✅" in r)
    warning_count = sum(1 for r in results if "⚠️" in r)
    error_count = sum(1 for r in results if "❌" in r)
    
    final_embed = discord.Embed(
        title="✅ Разблокировка завершена",
        color=COLORS['success'] if error_count == 0 else COLORS['warning']
    )
    
    final_embed.description = f"Каналы разблокированы для роли {target_role.mention}"
    
    final_embed.add_field(
        name="📊 Результаты",
        value=f"✅ Успешно: {success_count} каналов\n"
              f"⚠️ С предупреждениями: {warning_count} каналов\n"
              f"❌ Ошибки: {error_count} каналов",
        inline=False
    )
    
    if len(results) <= 10:
        final_embed.add_field(
            name="📝 Детали",
            value="\n".join(results[:10]),
            inline=False
        )
    
    final_embed.add_field(
        name="🎯 Для роли",
        value=f"{target_role.mention} (ID: `{target_role.id}`)",
        inline=True
    )
    
    final_embed.add_field(
        name="👤 Разблокировал",
        value=ctx.author.mention,
        inline=True
    )
    
    await safe_edit(message, embed=final_embed)

@bot.command(name='lock')
@is_admin_or_mod()
async def lock_command(ctx, lock_type: str = "send"):
    global last_locked_role
    
    if ctx.guild.id not in last_locked_role:
        embed = discord.Embed(
            title="❌ Роль не установлена",
            description=f"Сначала используйте `{PREFIX}lockrole @роль` чтобы указать, с какой ролью работать!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    role_id = last_locked_role[ctx.guild.id]
    target_role = ctx.guild.get_role(role_id)
    
    if not target_role:
        del last_locked_role[ctx.guild.id]
        embed = discord.Embed(
            title="❌ Роль не найдена",
            description=f"Роль с ID `{role_id}` больше не существует на сервере.\n"
                       f"Используйте `{PREFIX}lockrole @роль` чтобы установить новую роль.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
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
        description=f"Начинаю блокировку каналов для роли {target_role.mention}...",
        color=COLORS['warning']
    )
    
    lock_info = {
        'send': "📝 Запрещено писать, ставить реакции и прикреплять файлы",
        'view': "👁️ Запрещено читать и писать (канал скрыт)",
        'both': "🚫 Полная блокировка"
    }
    
    embed.add_field(name="Тип блокировки", value=lock_info[lock_type.lower()], inline=False)
    embed.add_field(name="ID роли", value=f"`{target_role.id}`", inline=True)
    embed.add_field(name="Название роли", value=f"`{target_role.name}`", inline=True)
    
    message = await safe_send(ctx, embed=embed)
    if not message:
        return
    
    results = await lock_all_channels_in_list(ctx.guild, target_role, lock_type.lower())
    
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
    
    if len(results) <= 10:
        final_embed.add_field(
            name="📝 Детали",
            value="\n".join(results[:10]),
            inline=False
        )
    else:
        final_embed.add_field(
            name="ℹ️ Информация",
            value=f"Обработано {len(results)} каналов",
            inline=False
        )
    
    final_embed.add_field(
        name="🎯 Для роли",
        value=f"{target_role.mention} (ID: `{target_role.id}`)",
        inline=True
    )
    
    final_embed.add_field(
        name="👤 Заблокировал",
        value=ctx.author.mention,
        inline=True
    )
    
    final_embed.set_footer(text=f"Тип блокировки: {lock_type.upper()}")
    
    await safe_edit(message, embed=final_embed)

@bot.command(name='currentrole')
@is_admin()
async def current_role_command(ctx):
    global last_locked_role
    
    if ctx.guild.id not in last_locked_role:
        embed = discord.Embed(
            title="ℹ️ Роль не установлена",
            description=f"Сейчас не выбрана ни одна роль.\n"
                       f"Используйте `{PREFIX}lockrole @роль` чтобы установить роль для команд `!lock` и `!unlock`.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    role_id = last_locked_role[ctx.guild.id]
    target_role = ctx.guild.get_role(role_id)
    
    if not target_role:
        embed = discord.Embed(
            title="⚠️ Роль не найдена",
            description=f"Сохранена роль с ID `{role_id}`, но она больше не существует на сервере.\n"
                       f"Используйте `{PREFIX}lockrole @роль` чтобы установить новую роль.",
            color=COLORS['warning']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="🎯 Текущая роль",
        description=f"Команды `!lock` и `!unlock` сейчас работают с ролью {target_role.mention}",
        color=COLORS['success']
    )
    
    embed.add_field(name="ID роли", value=f"`{target_role.id}`", inline=True)
    embed.add_field(name="Название роли", value=f"`{target_role.name}`", inline=True)
    embed.add_field(
        name="Доступные команды",
        value=f"• `{PREFIX}lock [тип]` - заблокировать каналы\n"
              f"• `{PREFIX}unlock` - разблокировать каналы\n"
              f"• `{PREFIX}lockrole @другая_роль` - сменить роль",
        inline=False
    )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='resetrole')
@is_admin()
async def reset_role_command(ctx):
    global last_locked_role
    
    if ctx.guild.id not in last_locked_role:
        embed = discord.Embed(
            title="ℹ️ Роль не установлена",
            description="Нет установленной роли для сброса.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    del last_locked_role[ctx.guild.id]
    
    embed = discord.Embed(
        title="✅ Роль сброшена",
        description=f"Теперь команды `!lock` и `!unlock` не будут работать, пока вы не установите новую роль с помощью `{PREFIX}lockrole @роль`.",
        color=COLORS['success']
    )
    
    await safe_send(ctx, embed=embed)

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ ПОИНТАМИ ==========

@bot.command(name='addpoints')
@is_admin()
async def add_points(ctx, member: discord.Member, amount: int, *, reason: str = "Выдано админом"):
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
    
    await check_and_assign_roles(member)

@bot.command(name='removepoints')
@is_admin()
async def remove_points(ctx, member: discord.Member, amount: int, *, reason: str = "Изъято админом"):
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
    
    await check_and_assign_roles(member)

@bot.command(name='setpoints')
@is_admin()
async def set_points(ctx, member: discord.Member, amount: int, *, reason: str = "Установлено админом"):
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
    
    await check_and_assign_roles(member)

@bot.command(name='resetpoints')
@is_admin()
async def reset_points(ctx):
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
    if member is None:
        member = ctx.author
    
    guild_id = ctx.guild.id
    user_id = member.id
    
    role_settings, _ = get_guild_settings(guild_id)
    
    points = await db.get_user_points(user_id, guild_id)
    position = await db.get_user_position(user_id, guild_id)
    
    embed = discord.Embed(
        title=f"🏆 Поинты {member.display_name}",
        color=COLORS['points']
    )
    
    embed.add_field(name="Баланс", value=f"**{points}** поинтов", inline=True)
    embed.add_field(name="Позиция в рейтинге", value=f"**#{position}**", inline=True)
    
    roles_text = []
    sorted_roles = sorted(role_settings.items())
    
    if sorted_roles:
        for required_points, role_name in sorted_roles:
            status = "✅" if points >= required_points else "⏳"
            roles_text.append(f"{status} **{role_name}** - {required_points} поинтов")
        
        embed.add_field(
            name="🏅 Система ролей",
            value="\n".join(roles_text),
            inline=False
        )
        
        next_role = None
        points_needed = 0
        for required_points, role_name in sorted_roles:
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
        elif points > 0 and sorted_roles:
            embed.add_field(
                name="🎉 Поздравляем!",
                value="Вы достигли максимальной роли!",
                inline=False
            )
    else:
        embed.add_field(
            name="🏅 Система ролей",
            value="⚙️ Система ролей еще не настроена администратором",
            inline=False
        )
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"ID: {user_id}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='leaderboard')
async def leaderboard(ctx, page: int = 1):
    guild_id = ctx.guild.id
    
    leaderboard_data = await db.get_leaderboard(guild_id, 20)
    
    if not leaderboard_data:
        embed = discord.Embed(
            title="📊 Таблица лидеров",
            description="Пока никто не имеет поинтов!",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    stats = await db.get_guild_stats(guild_id)
    
    embed = discord.Embed(
        title="🏆 Таблица лидеров",
        color=COLORS['points']
    )
    
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, record in enumerate(leaderboard_data, start=1):
        try:
            member = await ctx.guild.fetch_member(record['user_id'])
            username = member.display_name
        except:
            username = f"Пользователь ({record['user_id']})"
        
        medal = medals[i-1] if i <= len(medals) else f"{i}."
        
        role_settings, _ = get_guild_settings(guild_id)
        
        user_role = "Нет роли"
        for required_points, role_name in sorted(role_settings.items(), reverse=True):
            if record['points'] >= required_points:
                user_role = role_name
                break
        
        embed.add_field(
            name=f"{medal} {username}",
            value=f"**{record['points']}** поинтов | 🏅 {user_role}",
            inline=False
        )
    
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
    guild_id = ctx.guild.id
    role_settings, role_colors = get_guild_settings(guild_id)
    
    embed = discord.Embed(
        title="🏅 Система ролей",
        description="Роли выдаются автоматически при достижении определенного количества поинтов",
        color=COLORS['points']
    )
    
    if role_settings:
        for required_points, role_name in sorted(role_settings.items()):
            color = role_colors.get(role_name, discord.Color.default())
            color_block = f"`{str(color)}`"
            
            embed.add_field(
                name=f"🎖️ {role_name}",
                value=f"**{required_points}** поинтов\nЦвет: {color_block}",
                inline=True
            )
    else:
        embed.add_field(
            name="ℹ️ Информация",
            value="Система ролей еще не настроена",
            inline=False
        )
    
    admin_role_ids_str = ', '.join(str(role_id) for role_id in ADMIN_ROLE_IDS) if ADMIN_ROLE_IDS else "не указаны"
    embed.set_footer(text="Админские ID ролей: " + admin_role_ids_str)
    await safe_send(ctx, embed=embed)

@bot.command(name='ping')
async def ping_command(ctx):
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
    
    embed.add_field(name="Режим работы", value="✅ **24/7 Активен**", inline=False)
    embed.set_footer(text=f"Префикс команд: {PREFIX}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='export')
@is_admin()
async def export_command(ctx):
    guild_id = ctx.guild.id
    
    users = await db.get_leaderboard(guild_id, 1000)
    
    csv_data = "ID пользователя,Ник,Поинты,Позиция\n"
    
    for i, user in enumerate(users, 1):
        try:
            member = await ctx.guild.fetch_member(user['user_id'])
            username = member.display_name
        except:
            username = f"User_{user['user_id']}"
        
        csv_data += f"{user['user_id']},{username},{user['points']},{i}\n"
    
    filename = f"export_{guild_id}_{int(datetime.now().timestamp())}.csv"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(csv_data)
    
    file = discord.File(filename)
    await safe_send(ctx, "📁 Экспорт данных о поинтах:", file=file)
    os.remove(filename)

@bot.command(name='raid')
@is_admin()
async def raid_command(ctx, clan: str, link: str):
    if not ctx.channel.permissions_for(ctx.guild.me).mention_everyone:
        embed = discord.Embed(
            title="❌ Недостаточно прав",
            description="Мне нужны права на упоминание @everyone для этой команды!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if not link.startswith(('http://', 'https://', 'discord.gg/')):
        await safe_send(ctx, "❌ Ссылка должна начинаться с http://, https:// или discord.gg/")
        return
    
    embed = discord.Embed(
        title="РЕЙД!",
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
    
    await safe_send(ctx, content="@everyone", embed=embed)
    
    confirm_embed = discord.Embed(
        title="✅ Рейд-оповещение отправлено!",
        description=f"Клан: **{clan}**\nСсылка: {link}",
        color=COLORS['success']
    )
    await safe_send(ctx, embed=confirm_embed)

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ РОЛЯМИ ЗА ПОИНТЫ ==========

@bot.command(name='addrole')
@is_admin()
async def add_role_for_points(ctx, points: int, *, role_name: str):
    guild_id = ctx.guild.id
    
    role_settings, role_colors = get_guild_settings(guild_id)
    
    if points <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным числом!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if role_name in role_settings.values():
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Роль **{role_name}** уже существует в системе!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if points in role_settings:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"За {points} поинтов уже есть роль **{role_settings[points]}**!\n"
                       f"Используйте `{PREFIX}removerole {points}` чтобы удалить её сначала.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    role_settings[points] = role_name
    role_colors[role_name] = discord.Color.random()
    
    GUILD_ROLE_SETTINGS[guild_id] = role_settings
    GUILD_ROLE_COLORS[guild_id] = role_colors
    
    try:
        await db.save_role_settings(guild_id, role_settings, role_colors)
        logger.info(f"✅ Настройки ролей сохранены в БД для сервера {guild_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения ролей в БД: {e}")
        embed = discord.Embed(
            title="⚠️ Предупреждение",
            description="Роль добавлена, но не сохранилась в базу данных!",
            color=COLORS['warning']
        )
        await safe_send(ctx, embed=embed)
    
    embed = discord.Embed(
        title="✅ Роль добавлена",
        description=f"Новая роль за поинты успешно добавлена!",
        color=COLORS['success']
    )
    
    embed.add_field(name="🎭 Название роли", value=f"**{role_name}**", inline=True)
    embed.add_field(name="💰 Требуемые поинты", value=f"**{points}**", inline=True)
    embed.add_field(name="🎨 Цвет", value=f"`{role_colors[role_name]}`", inline=True)
    
    roles_text = []
    for p, name in sorted(role_settings.items()):
        roles_text.append(f"• **{name}** - {p} поинтов")
    
    embed.add_field(
        name="📊 Текущая система ролей",
        value="\n".join(roles_text),
        inline=False
    )
    
    await safe_send(ctx, embed=embed)
    
    await update_all_member_roles(ctx.guild)

async def update_all_member_roles(guild):
    try:
        logger.info(f"Начинаю массовое обновление ролей на сервере {guild.name}")
        
        for member in guild.members:
            try:
                await check_and_assign_roles(member)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Ошибка обновления ролей для {member.display_name}: {e}")
        
        logger.info(f"Массовое обновление ролей завершено на сервере {guild.name}")
    except Exception as e:
        logger.error(f"Ошибка массового обновления ролей: {e}")

@bot.command(name='removerole')
@is_admin()
async def remove_role_for_points(ctx, points: int):
    guild_id = ctx.guild.id
    
    role_settings, role_colors = get_guild_settings(guild_id)
    
    if points not in role_settings:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Роль за {points} поинтов не найдена!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    role_name = role_settings[points]
    
    embed = discord.Embed(
        title="⚠️ Подтверждение удаления",
        description=f"Вы уверены, что хотите удалить роль **{role_name}** за {points} поинтов?",
        color=COLORS['warning']
    )
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        del role_settings[points]
        if role_name in role_colors:
            del role_colors[role_name]
        
        GUILD_ROLE_SETTINGS[guild_id] = role_settings
        GUILD_ROLE_COLORS[guild_id] = role_colors
        
        try:
            await db.save_role_settings(guild_id, role_settings, role_colors)
            logger.info(f"✅ Настройки ролей обновлены в БД для сервера {guild_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения ролей в БД: {e}")
        
        discord_role = discord.utils.get(ctx.guild.roles, name=role_name)
        if discord_role:
            try:
                await discord_role.delete(reason="Роль удалена из системы поинтов")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления роли с сервера: {e}")
        
        confirm_embed = discord.Embed(
            title="✅ Роль удалена",
            description=f"Роль **{role_name}** за {points} поинтов успешно удалена из системы.",
            color=COLORS['success']
        )
        
        if role_settings:
            roles_text = []
            for p, name in sorted(role_settings.items()):
                roles_text.append(f"• **{name}** - {p} поинтов")
            confirm_embed.add_field(
                name="📊 Обновленная система ролей",
                value="\n".join(roles_text),
                inline=False
            )
        else:
            confirm_embed.add_field(
                name="📊 Обновленная система ролей",
                value="Система ролей пуста",
                inline=False
            )
        
        await interaction.response.edit_message(embed=confirm_embed, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Удаление отменено",
            description="Роль не была удалена.",
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

@bot.command(name='removerolebyname')
@is_admin()
async def remove_role_by_name(ctx, *, role_name: str):
    guild_id = ctx.guild.id
    role_settings, _ = get_guild_settings(guild_id)
    
    points_to_remove = None
    for points, name in role_settings.items():
        if name.lower() == role_name.lower():
            points_to_remove = points
            break
    
    if points_to_remove is None:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Роль **{role_name}** не найдена в системе!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    ctx.command = bot.get_command('removerole')
    await ctx.invoke(bot.get_command('removerole'), points=points_to_remove)

@bot.command(name='editrole')
@is_admin()
async def edit_role_for_points(ctx, old_points: int, new_points: int = None, *, new_name: str = None):
    guild_id = ctx.guild.id
    role_settings, role_colors = get_guild_settings(guild_id)
    
    if old_points not in role_settings:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Роль за {old_points} поинтов не найдена!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    old_name = role_settings[old_points]
    
    if new_points is not None:
        if new_points <= 0:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Количество поинтов должно быть положительным числом!",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            return
        
        if new_points != old_points and new_points in role_settings:
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"За {new_points} поинтов уже есть роль **{role_settings[new_points]}**!",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            return
    
    if new_name is not None:
        if new_name in role_settings.values() and new_name != old_name:
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Роль **{new_name}** уже существует в системе!",
                color=COLORS['error']
            )
            await safe_send(ctx, embed=embed)
            return
    
    color = role_colors.get(old_name, discord.Color.default())
    
    if new_points is not None and new_name is not None:
        del role_settings[old_points]
        role_settings[new_points] = new_name
        if old_name in role_colors:
            del role_colors[old_name]
        role_colors[new_name] = color
        
        embed = discord.Embed(
            title="✅ Роль обновлена",
            description=f"Роль успешно изменена!",
            color=COLORS['success']
        )
        embed.add_field(name="🔄 Было", value=f"**{old_name}** - {old_points} поинтов", inline=False)
        embed.add_field(name="✨ Стало", value=f"**{new_name}** - {new_points} поинтов", inline=False)
        
    elif new_points is not None:
        del role_settings[old_points]
        role_settings[new_points] = old_name
        
        embed = discord.Embed(
            title="✅ Роль обновлена",
            description=f"Количество поинтов для роли **{old_name}** изменено!",
            color=COLORS['success']
        )
        embed.add_field(name="🔄 Было", value=f"{old_points} поинтов", inline=True)
        embed.add_field(name="✨ Стало", value=f"{new_points} поинтов", inline=True)
        
    elif new_name is not None:
        role_settings[old_points] = new_name
        if old_name in role_colors:
            del role_colors[old_name]
        role_colors[new_name] = color
        
        embed = discord.Embed(
            title="✅ Роль обновлена",
            description=f"Название роли изменено!",
            color=COLORS['success']
        )
        embed.add_field(name="🔄 Было", value=f"**{old_name}**", inline=True)
        embed.add_field(name="✨ Стало", value=f"**{new_name}**", inline=True)
        embed.add_field(name="💰 Поинты", value=f"{old_points}", inline=True)
    
    else:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Укажите новые поинты или новое название!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    GUILD_ROLE_SETTINGS[guild_id] = role_settings
    GUILD_ROLE_COLORS[guild_id] = role_colors
    
    try:
        await db.save_role_settings(guild_id, role_settings, role_colors)
        logger.info(f"✅ Настройки ролей обновлены в БД для сервера {guild_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения ролей в БД: {e}")
    
    roles_text = []
    for p, name in sorted(role_settings.items()):
        roles_text.append(f"• **{name}** - {p} поинтов")
    
    embed.add_field(
        name="📊 Обновленная система ролей",
        value="\n".join(roles_text),
        inline=False
    )
    
    await safe_send(ctx, embed=embed)
    
    await update_all_member_roles(ctx.guild)

@bot.command(name='setrolecolor')
@is_admin()
async def set_role_color(ctx, points: int, color: str):
    guild_id = ctx.guild.id
    role_settings, role_colors = get_guild_settings(guild_id)
    
    if points not in role_settings:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Роль за {points} поинтов не найдена!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    role_name = role_settings[points]
    
    try:
        if color.startswith('#'):
            color = color[1:]
        
        if color.lower() in ['red', 'blue', 'green', 'yellow', 'purple', 'orange', 'gold', 'pink', 'brown', 'black', 'white']:
            color_map = {
                'red': discord.Color.red(),
                'blue': discord.Color.blue(),
                'green': discord.Color.green(),
                'yellow': discord.Color.gold(),
                'purple': discord.Color.purple(),
                'orange': discord.Color.orange(),
                'gold': discord.Color.gold(),
                'pink': discord.Color.magenta(),
                'brown': discord.Color.dark_orange(),
                'black': discord.Color.dark_grey(),
                'white': discord.Color.lighter_grey()
            }
            new_color = color_map.get(color.lower(), discord.Color.default())
        else:
            new_color = discord.Color(int(color, 16))
    except:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Неверный формат цвета. Используйте HEX код (например, #FF0000) или название цвета (red, blue, green и т.д.)",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    role_colors[role_name] = new_color
    GUILD_ROLE_COLORS[guild_id] = role_colors
    
    try:
        await db.save_role_settings(guild_id, role_settings, role_colors)
        logger.info(f"✅ Настройки ролей обновлены в БД для сервера {guild_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения ролей в БД: {e}")
    
    discord_role = discord.utils.get(ctx.guild.roles, name=role_name)
    if discord_role:
        try:
            await discord_role.edit(color=new_color, reason="Изменение цвета роли")
        except:
            pass
    
    embed = discord.Embed(
        title="✅ Цвет роли изменен",
        description=f"Для роли **{role_name}** установлен новый цвет!",
        color=new_color
    )
    
    embed.add_field(name="🎭 Роль", value=f"**{role_name}**", inline=True)
    embed.add_field(name="💰 Поинты", value=f"{points}", inline=True)
    embed.add_field(name="🎨 Цвет", value=f"`{new_color}`", inline=True)
    embed.add_field(name="👁️ Пример", value="████████", inline=False)
    
    await safe_send(ctx, embed=embed)

@bot.command(name='reorderroles')
@is_admin()
async def reorder_roles(ctx):
    guild_id = ctx.guild.id
    role_settings, role_colors = get_guild_settings(guild_id)
    
    if not role_settings:
        embed = discord.Embed(
            title="ℹ️ Нет ролей",
            description="На сервере нет настроенных ролей за поинты.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="⚠️ Реконструкция ролей",
        description="Это действие пересоздаст все роли за поинты в правильном порядке иерархии.",
        color=COLORS['warning']
    )
    
    embed.add_field(
        name="Что будет сделано:",
        value="• Все существующие роли за поинты будут удалены\n"
              "• Роли будут созданы заново в правильном порядке\n"
              "• Цвета ролей будут сохранены\n"
              "• Все участники получат свои роли обратно",
        inline=False
    )
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        status_embed = discord.Embed(
            title="⏳ Реконструкция ролей...",
            description="Начинаю пересоздание ролей...",
            color=COLORS['info']
        )
        status_msg = await interaction.followup.send(embed=status_embed)
        
        member_roles = {}
        for member in ctx.guild.members:
            for role_name in role_settings.values():
                role = discord.utils.get(ctx.guild.roles, name=role_name)
                if role and role in member.roles:
                    if member.id not in member_roles:
                        member_roles[member.id] = []
                    member_roles[member.id].append(role_name)
        
        deleted_count = 0
        for role_name in role_settings.values():
            role = discord.utils.get(ctx.guild.roles, name=role_name)
            if role:
                try:
                    await role.delete(reason="Реконструкция системы ролей")
                    deleted_count += 1
                except:
                    pass
        
        created_roles = {}
        for points, role_name in sorted(role_settings.items()):
            color = role_colors.get(role_name, discord.Color.default())
            if isinstance(color, str):
                try:
                    if color.startswith('#'):
                        color_int = int(color[1:], 16)
                        color = discord.Color(color_int)
                    else:
                        color = discord.Color.default()
                except:
                    color = discord.Color.default()
            
            try:
                new_role = await ctx.guild.create_role(
                    name=role_name,
                    color=color,
                    mentionable=True,
                    reason="Реконструкция системы ролей"
                )
                created_roles[role_name] = new_role
            except Exception as e:
                logger.error(f"Ошибка создания роли {role_name}: {e}")
        
        assigned_count = 0
        for member in ctx.guild.members:
            if member.id in member_roles:
                roles_to_add = []
                for role_name in member_roles[member.id]:
                    if role_name in created_roles:
                        roles_to_add.append(created_roles[role_name])
                
                if roles_to_add:
                    try:
                        await member.add_roles(*roles_to_add, reason="Восстановление ролей после реконструкции")
                        assigned_count += 1
                    except:
                        pass
        
        try:
            all_roles = ctx.guild.roles
            role_order = []
            for points, role_name in sorted(role_settings.items(), reverse=True):
                if role_name in created_roles:
                    role_order.append(created_roles[role_name])
            
            other_roles = [r for r in all_roles if r.name not in role_settings.values() and not r.managed and r != ctx.guild.default_role]
            role_order.extend(other_roles)
            
            if ctx.guild.me.guild_permissions.manage_roles:
                await ctx.guild.edit_role_positions(positions={role: i for i, role in enumerate(role_order)})
        except:
            pass
        
        final_embed = discord.Embed(
            title="✅ Реконструкция завершена",
            description="Система ролей успешно пересоздана!",
            color=COLORS['success']
        )
        
        final_embed.add_field(
            name="📊 Статистика",
            value=f"• Удалено ролей: {deleted_count}\n"
                  f"• Создано ролей: {len(created_roles)}\n"
                  f"• Восстановлено ролей у участников: {assigned_count}",
            inline=False
        )
        
        await status_msg.edit(embed=final_embed)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Реконструкция отменена",
            description="Система ролей не была изменена.",
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

@bot.command(name='saveroles')
@is_admin()
async def save_roles_config(ctx):
    guild_id = ctx.guild.id
    role_settings, role_colors = get_guild_settings(guild_id)
    
    filename = f"roles_config_{ctx.guild.id}_{int(datetime.now().timestamp())}.txt"
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("=== КОНФИГУРАЦИЯ РОЛЕЙ ЗА ПОИНТЫ ===\n\n")
        f.write(f"Сервер: {ctx.guild.name} (ID: {ctx.guild.id})\n")
        f.write(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Экспортировал: {ctx.author.display_name} (ID: {ctx.author.id})\n\n")
        
        f.write("=== РОЛИ ===\n")
        for points, role_name in sorted(role_settings.items()):
            color = role_colors.get(role_name, discord.Color.default())
            f.write(f"{points}: {role_name} (цвет: {color})\n")
        
        f.write("\n=== КОМАНДЫ ДЛЯ ВОССТАНОВЛЕНИЯ ===\n")
        for points, role_name in sorted(role_settings.items()):
            color = role_colors.get(role_name, discord.Color.default())
            f.write(f"!addrole {points} {role_name}\n")
            if color != discord.Color.default():
                f.write(f"!setrolecolor {points} {str(color)}\n")
    
    file = discord.File(filename)
    embed = discord.Embed(
        title="✅ Конфигурация ролей сохранена",
        description="Текущая конфигурация ролей экспортирована в файл.",
        color=COLORS['success']
    )
    
    await safe_send(ctx, embed=embed, file=file)
    os.remove(filename)

@bot.command(name='updateroles')
@is_admin()
async def update_all_roles(ctx):
    embed = discord.Embed(
        title="⏳ Обновление ролей",
        description=f"Начинаю обновление ролей для {len(ctx.guild.members)} участников...",
        color=COLORS['info']
    )
    
    message = await safe_send(ctx, embed=embed)
    
    await update_all_member_roles(ctx.guild)
    
    final_embed = discord.Embed(
        title="✅ Обновление завершено",
        description=f"Роли всех участников сервера обновлены в соответствии с их поинтами.",
        color=COLORS['success']
    )
    
    if message:
        await safe_edit(message, embed=final_embed)
    else:
        await safe_send(ctx, embed=final_embed)

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ АДМИНСКИМИ РОЛЯМИ ==========

@bot.command(name='addadminrole')
@is_admin()
async def add_admin_role(ctx, role: discord.Role):
    global ADMIN_ROLE_IDS
    
    if role.id in ADMIN_ROLE_IDS:
        embed = discord.Embed(
            title="⚠️ Роль уже в списке",
            description=f"Роль {role.mention} уже есть в списке админских ролей!",
            color=COLORS['warning']
        )
        await safe_send(ctx, embed=embed)
        return
    
    ADMIN_ROLE_IDS.append(role.id)
    
    try:
        env_path = '.env'
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
            
            admin_roles_str = ','.join(str(id) for id in ADMIN_ROLE_IDS)
            found = False
            for i, line in enumerate(lines):
                if line.startswith('ADMIN_ROLE_IDS='):
                    lines[i] = f'ADMIN_ROLE_IDS={admin_roles_str}\n'
                    found = True
                    break
            
            if not found:
                lines.append(f'ADMIN_ROLE_IDS={admin_roles_str}\n')
            
            with open(env_path, 'w') as f:
                f.writelines(lines)
            
            load_dotenv(override=True)
    except Exception as e:
        logger.warning(f"Не удалось обновить .env файл: {e}")
    
    embed = discord.Embed(
        title="✅ Роль добавлена",
        description=f"Роль {role.mention} добавлена в список админских ролей!",
        color=COLORS['success']
    )
    
    embed.add_field(
        name="📊 Текущие админские роли",
        value="\n".join([f"• <@&{role_id}>" for role_id in ADMIN_ROLE_IDS]) or "Нет ролей",
        inline=False
    )
    
    embed.set_footer(text=f"Всего ролей: {len(ADMIN_ROLE_IDS)}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='removeadminrole')
@is_admin()
async def remove_admin_role(ctx, role: discord.Role):
    global ADMIN_ROLE_IDS
    
    if role.id not in ADMIN_ROLE_IDS:
        embed = discord.Embed(
            title="❌ Роль не найдена",
            description=f"Роль {role.mention} не найдена в списке админских ролей!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    ADMIN_ROLE_IDS.remove(role.id)
    
    try:
        env_path = '.env'
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
            
            admin_roles_str = ','.join(str(id) for id in ADMIN_ROLE_IDS)
            for i, line in enumerate(lines):
                if line.startswith('ADMIN_ROLE_IDS='):
                    lines[i] = f'ADMIN_ROLE_IDS={admin_roles_str}\n'
                    break
            
            with open(env_path, 'w') as f:
                f.writelines(lines)
            
            load_dotenv(override=True)
    except Exception as e:
        logger.warning(f"Не удалось обновить .env файл: {e}")
    
    embed = discord.Embed(
        title="✅ Роль удалена",
        description=f"Роль {role.mention} удалена из списка админских ролей!",
        color=COLORS['success']
    )
    
    embed.add_field(
        name="📊 Текущие админские роли",
        value="\n".join([f"• <@&{role_id}>" for role_id in ADMIN_ROLE_IDS]) or "Нет ролей",
        inline=False
    )
    
    embed.set_footer(text=f"Всего ролей: {len(ADMIN_ROLE_IDS)}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='listadminroles')
async def list_admin_roles(ctx):
    if not ADMIN_ROLE_IDS:
        embed = discord.Embed(
            title="📋 Список админских ролей",
            description="В данный момент нет назначенных админских ролей.\n"
                       f"Используйте `{PREFIX}addadminrole @роль` чтобы добавить роль.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="📋 Список админских ролей",
        description=f"Всего ролей: **{len(ADMIN_ROLE_IDS)}**",
        color=COLORS['info']
    )
    
    roles_info = []
    for role_id in ADMIN_ROLE_IDS:
        role = ctx.guild.get_role(role_id)
        if role:
            roles_info.append(f"• {role.mention} (ID: `{role_id}`)")
        else:
            roles_info.append(f"• Роль с ID `{role_id}` (не найдена на сервере)")
    
    embed.add_field(
        name="🎭 Роли",
        value="\n".join(roles_info) if roles_info else "Нет доступных ролей",
        inline=False
    )
    
    embed.add_field(
        name="ℹ️ Информация",
        value="Владельцы этих ролей имеют доступ ко всем админским командам бота.\n"
              f"Используйте `{PREFIX}addadminrole @роль` чтобы добавить новую роль.\n"
              f"Используйте `{PREFIX}removeadminrole @роль` чтобы удалить роль.",
        inline=False
    )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='clearadminroles')
@is_admin()
async def clear_admin_roles(ctx):
    global ADMIN_ROLE_IDS
    
    if not ADMIN_ROLE_IDS:
        embed = discord.Embed(
            title="ℹ️ Нет ролей",
            description="Список админских ролей уже пуст!",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="⚠️ ОПАСНОЕ ДЕЙСТВИЕ",
        description=f"Вы уверены, что хотите удалить ВСЕ админские роли (**{len(ADMIN_ROLE_IDS)}** шт.)?\n"
                   "После этого только пользователи с правами администратора Discord смогут использовать админские команды!",
        color=COLORS['error']
    )
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        old_count = len(ADMIN_ROLE_IDS)
        ADMIN_ROLE_IDS = []
        
        try:
            env_path = '.env'
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    lines = f.readlines()
                
                for i, line in enumerate(lines):
                    if line.startswith('ADMIN_ROLE_IDS='):
                        lines[i] = 'ADMIN_ROLE_IDS=\n'
                        break
                
                with open(env_path, 'w') as f:
                    f.writelines(lines)
                
                load_dotenv(override=True)
        except Exception as e:
            logger.warning(f"Не удалось обновить .env файл: {e}")
        
        confirm_embed = discord.Embed(
            title="✅ Все админские роли удалены",
            description=f"Удалено {old_count} админских ролей.\n"
                       "Только пользователи с правами администратора Discord могут использовать админские команды.",
            color=COLORS['success']
        )
        await interaction.response.edit_message(embed=confirm_embed, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Удаление отменено",
            description="Список админских ролей не был изменен.",
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

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ МОДЕРАТОРСКИМИ РОЛЯМИ ==========

@bot.command(name='addmodrole')
@is_admin()
async def add_mod_role(ctx, role: discord.Role):
    global MOD_ROLE_IDS
    
    if role.id in MOD_ROLE_IDS:
        embed = discord.Embed(
            title="⚠️ Роль уже в списке",
            description=f"Роль {role.mention} уже есть в списке модераторских ролей!",
            color=COLORS['warning']
        )
        await safe_send(ctx, embed=embed)
        return
    
    MOD_ROLE_IDS.append(role.id)
    
    try:
        env_path = '.env'
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
            
            mod_roles_str = ','.join(str(id) for id in MOD_ROLE_IDS)
            found = False
            for i, line in enumerate(lines):
                if line.startswith('MOD_ROLE_IDS='):
                    lines[i] = f'MOD_ROLE_IDS={mod_roles_str}\n'
                    found = True
                    break
            
            if not found:
                lines.append(f'MOD_ROLE_IDS={mod_roles_str}\n')
            
            with open(env_path, 'w') as f:
                f.writelines(lines)
            
            load_dotenv(override=True)
    except Exception as e:
        logger.warning(f"Не удалось обновить .env файл: {e}")
    
    embed = discord.Embed(
        title="✅ Модераторская роль добавлена",
        description=f"Роль {role.mention} добавлена в список модераторских ролей!",
        color=COLORS['success']
    )
    
    embed.add_field(
        name="📊 Текущие модераторские роли",
        value="\n".join([f"• <@&{role_id}>" for role_id in MOD_ROLE_IDS]) or "Нет ролей",
        inline=False
    )
    
    embed.set_footer(text=f"Всего ролей: {len(MOD_ROLE_IDS)}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='removemodrole')
@is_admin()
async def remove_mod_role(ctx, role: discord.Role):
    global MOD_ROLE_IDS
    
    if role.id not in MOD_ROLE_IDS:
        embed = discord.Embed(
            title="❌ Роль не найдена",
            description=f"Роль {role.mention} не найдена в списке модераторских ролей!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    MOD_ROLE_IDS.remove(role.id)
    
    try:
        env_path = '.env'
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
            
            mod_roles_str = ','.join(str(id) for id in MOD_ROLE_IDS)
            for i, line in enumerate(lines):
                if line.startswith('MOD_ROLE_IDS='):
                    lines[i] = f'MOD_ROLE_IDS={mod_roles_str}\n'
                    break
            
            with open(env_path, 'w') as f:
                f.writelines(lines)
            
            load_dotenv(override=True)
    except Exception as e:
        logger.warning(f"Не удалось обновить .env файл: {e}")
    
    embed = discord.Embed(
        title="✅ Модераторская роль удалена",
        description=f"Роль {role.mention} удалена из списка модераторских ролей!",
        color=COLORS['success']
    )
    
    embed.add_field(
        name="📊 Текущие модераторские роли",
        value="\n".join([f"• <@&{role_id}>" for role_id in MOD_ROLE_IDS]) or "Нет ролей",
        inline=False
    )
    
    embed.set_footer(text=f"Всего ролей: {len(MOD_ROLE_IDS)}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='listmodroles')
async def list_mod_roles(ctx):
    if not MOD_ROLE_IDS:
        embed = discord.Embed(
            title="📋 Список модераторских ролей",
            description="В данный момент нет назначенных модераторских ролей.\n"
                       f"Используйте `{PREFIX}addmodrole @роль` чтобы добавить роль.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="📋 Список модераторских ролей",
        description=f"Всего ролей: **{len(MOD_ROLE_IDS)}**",
        color=COLORS['info']
    )
    
    roles_info = []
    for role_id in MOD_ROLE_IDS:
        role = ctx.guild.get_role(role_id)
        if role:
            roles_info.append(f"• {role.mention} (ID: `{role_id}`)")
        else:
            roles_info.append(f"• Роль с ID `{role_id}` (не найдена на сервере)")
    
    embed.add_field(
        name="🎭 Роли",
        value="\n".join(roles_info) if roles_info else "Нет доступных ролей",
        inline=False
    )
    
    embed.add_field(
        name="ℹ️ Информация",
        value="Владельцы этих ролей имеют доступ к командам блокировки каналов (lock, unlock, lockrole, lockinfo).\n"
              f"Используйте `{PREFIX}addmodrole @роль` чтобы добавить новую роль.\n"
              f"Используйте `{PREFIX}removemodrole @роль` чтобы удалить роль.",
        inline=False
    )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='clearmodroles')
@is_admin()
async def clear_mod_roles(ctx):
    global MOD_ROLE_IDS
    
    if not MOD_ROLE_IDS:
        embed = discord.Embed(
            title="ℹ️ Нет ролей",
            description="Список модераторских ролей уже пуст!",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="⚠️ ОПАСНОЕ ДЕЙСТВИЕ",
        description=f"Вы уверены, что хотите удалить ВСЕ модераторские роли (**{len(MOD_ROLE_IDS)}** шт.)?",
        color=COLORS['error']
    )
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        old_count = len(MOD_ROLE_IDS)
        MOD_ROLE_IDS = []
        
        try:
            env_path = '.env'
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    lines = f.readlines()
                
                for i, line in enumerate(lines):
                    if line.startswith('MOD_ROLE_IDS='):
                        lines[i] = 'MOD_ROLE_IDS=\n'
                        break
                
                with open(env_path, 'w') as f:
                    f.writelines(lines)
                
                load_dotenv(override=True)
        except Exception as e:
            logger.warning(f"Не удалось обновить .env файл: {e}")
        
        confirm_embed = discord.Embed(
            title="✅ Все модераторские роли удалены",
            description=f"Удалено {old_count} модераторских ролей.",
            color=COLORS['success']
        )
        await interaction.response.edit_message(embed=confirm_embed, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Удаление отменено",
            description="Список модераторских ролей не был изменен.",
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

# ========== СИСТЕМА ВОУЧЕЙ (ГОЛОСОВАНИЯ) ==========

class VouchView(discord.ui.View):
    def __init__(self, target_user: discord.Member, target_role: discord.Role, initiator: discord.Member):
        super().__init__(timeout=7200)
        self.target_user = target_user
        self.target_role = target_role
        self.initiator = initiator
        self.votes_for = set()
        self.votes_against = set()
        self.message = None
        self.is_completed = False
        
        self.add_item(VouchForButton())
        self.add_item(VouchAgainstButton())
        self.add_item(VouchShowVotesButton())
    
    async def update_embed(self):
        embed = discord.Embed(
            title=f"🗳️ Голосование за повышение {self.target_user.display_name}",
            description=f"**Предложил:** {self.initiator.mention}\n"
                       f"**Пользователь:** {self.target_user.mention}\n"
                       f"**Роль:** {self.target_role.mention}",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="✅ ЗА",
            value=f"**{len(self.votes_for)}** голос(ов)",
            inline=True
        )
        
        embed.add_field(
            name="❌ ПРОТИВ",
            value=f"**{len(self.votes_against)}** голос(ов)",
            inline=True
        )
        
        embed.add_field(
            name="📊 Всего проголосовало",
            value=f"**{len(self.votes_for) + len(self.votes_against)}**",
            inline=True
        )
        
        total_votes = len(self.votes_for) + len(self.votes_against)
        if total_votes > 0:
            for_percent = (len(self.votes_for) / total_votes) * 100
            bar_length = 20
            filled = int(bar_length * for_percent / 100)
            bar = "█" * filled + "░" * (bar_length - filled)
            embed.add_field(
                name="📈 Прогресс",
                value=f"`{bar}` {for_percent:.1f}%",
                inline=False
            )
        
        if len(self.votes_for) >= 5:
            embed.add_field(
                name="✅ Условие выполнено",
                value="Набрано 5+ голосов ЗА! Администратор может выдать роль.",
                inline=False
            )
        else:
            embed.add_field(
                name="⏳ Условие для выдачи",
                value=f"Нужно **5** голосов ЗА (сейчас {len(self.votes_for)})",
                inline=False
            )
        
        embed.set_footer(text=f"Голосование создано: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        
        if self.message:
            await self.message.edit(embed=embed, view=self)
    
    async def check_votes(self):
        if len(self.votes_for) >= 5 and not self.is_completed:
            has_give_button = False
            for item in self.children:
                if isinstance(item, VouchGiveRoleButton):
                    has_give_button = True
                    break
            
            if not has_give_button:
                self.add_item(VouchGiveRoleButton())
                await self.update_embed()
                
                if self.message and self.message.guild:
                    admin_mentions = []
                    for role_id in ADMIN_ROLE_IDS:
                        role = self.message.guild.get_role(role_id)
                        if role:
                            admin_mentions.append(role.mention)
                    
                    if admin_mentions:
                        await self.message.channel.send(
                            f"{' '.join(admin_mentions)} Набрано 5+ голосов за повышение {self.target_user.mention} до роли {self.target_role.mention}!",
                            delete_after=10
                        )

class VouchForButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.success, label="ЗА", emoji="✅", row=0)
    
    async def callback(self, interaction: discord.Interaction):
        view: VouchView = self.view
        
        if interaction.user.id == view.target_user.id:
            await interaction.response.send_message("❌ Вы не можете голосовать за самого себя!", ephemeral=True)
            return
        
        if view.is_completed:
            await interaction.response.send_message("❌ Голосование уже завершено!", ephemeral=True)
            return
        
        if interaction.user.id in view.votes_for:
            await interaction.response.send_message("❌ Вы уже проголосовали ЗА!", ephemeral=True)
            return
        if interaction.user.id in view.votes_against:
            view.votes_against.remove(interaction.user.id)
            view.votes_for.add(interaction.user.id)
            await interaction.response.send_message("✅ Ваш голос изменен на ЗА!", ephemeral=True)
        else:
            view.votes_for.add(interaction.user.id)
            await interaction.response.send_message("✅ Вы проголосовали ЗА!", ephemeral=True)
        
        await view.update_embed()
        await view.check_votes()

class VouchAgainstButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="ПРОТИВ", emoji="❌", row=0)
    
    async def callback(self, interaction: discord.Interaction):
        view: VouchView = self.view
        
        if interaction.user.id == view.target_user.id:
            await interaction.response.send_message("❌ Вы не можете голосовать за самого себя!", ephemeral=True)
            return
        
        if view.is_completed:
            await interaction.response.send_message("❌ Голосование уже завершено!", ephemeral=True)
            return
        
        if interaction.user.id in view.votes_against:
            await interaction.response.send_message("❌ Вы уже проголосовали ПРОТИВ!", ephemeral=True)
            return
        if interaction.user.id in view.votes_for:
            view.votes_for.remove(interaction.user.id)
            view.votes_against.add(interaction.user.id)
            await interaction.response.send_message("✅ Ваш голос изменен на ПРОТИВ!", ephemeral=True)
        else:
            view.votes_against.add(interaction.user.id)
            await interaction.response.send_message("✅ Вы проголосовали ПРОТИВ!", ephemeral=True)
        
        await view.update_embed()
        await view.check_votes()

class VouchShowVotesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.secondary, label="Кто проголосовал", emoji="📋", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view: VouchView = self.view
        
        embed = discord.Embed(
            title="📋 Список проголосовавших",
            color=discord.Color.blue()
        )
        
        if view.votes_for:
            for_voters = []
            for user_id in list(view.votes_for)[:20]:
                user = interaction.guild.get_member(user_id)
                if user:
                    for_voters.append(f"• {user.mention}")
                else:
                    for_voters.append(f"• Пользователь {user_id}")
            
            embed.add_field(
                name=f"✅ ЗА ({len(view.votes_for)})",
                value="\n".join(for_voters) if for_voters else "Нет голосов",
                inline=False
            )
        else:
            embed.add_field(name="✅ ЗА", value="Нет голосов", inline=False)
        
        if view.votes_against:
            against_voters = []
            for user_id in list(view.votes_against)[:20]:
                user = interaction.guild.get_member(user_id)
                if user:
                    against_voters.append(f"• {user.mention}")
                else:
                    against_voters.append(f"• Пользователь {user_id}")
            
            embed.add_field(
                name=f"❌ ПРОТИВ ({len(view.votes_against)})",
                value="\n".join(against_voters) if against_voters else "Нет голосов",
                inline=False
            )
        else:
            embed.add_field(name="❌ ПРОТИВ", value="Нет голосов", inline=False)
        
        embed.set_footer(text=f"Всего проголосовало: {len(view.votes_for) + len(view.votes_against)} | Нельзя голосовать за себя")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class VouchGiveRoleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="ВЫДАТЬ РОЛЬ", emoji="🎁", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view: VouchView = self.view
        
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin:
            user_role_ids = [role.id for role in interaction.user.roles]
            is_admin = any(admin_role_id in user_role_ids for admin_role_id in ADMIN_ROLE_IDS)
        
        if not is_admin:
            await interaction.response.send_message("❌ Только администраторы могут выдавать роли!", ephemeral=True)
            return
        
        if view.is_completed:
            await interaction.response.send_message("❌ Голосование уже завершено!", ephemeral=True)
            return
        
        if len(view.votes_for) < 5:
            await interaction.response.send_message(f"❌ Недостаточно голосов! Нужно минимум 5 голосов ЗА (сейчас {len(view.votes_for)})", ephemeral=True)
            return
        
        try:
            await view.target_user.add_roles(view.target_role, reason=f"Повышение по результатам голосования (админ: {interaction.user})")
            
            view.is_completed = True
            
            embed = discord.Embed(
                title="✅ РОЛЬ ВЫДАНА",
                description=f"**{view.target_user.mention} повышен до {view.target_role.mention}!**",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="📊 Итоги голосования",
                value=f"✅ ЗА: {len(view.votes_for)}\n"
                      f"❌ ПРОТИВ: {len(view.votes_against)}",
                inline=False
            )
            
            embed.add_field(
                name="👤 Выдал",
                value=interaction.user.mention,
                inline=True
            )
            
            embed.set_footer(text=f"Голосование инициировано: {view.initiator.display_name}")
            
            for item in view.children:
                item.disabled = True
            
            await view.message.edit(embed=embed, view=view)
            
            await interaction.response.send_message(f"✅ Роль {view.target_role.mention} успешно выдана пользователю {view.target_user.mention}!", ephemeral=True)
            
            try:
                dm_embed = discord.Embed(
                    title="🎉 Вас повысили!",
                    description=f"На сервере **{interaction.guild.name}** вы получили роль **{view.target_role.name}** по результатам голосования!",
                    color=discord.Color.gold()
                )
                await view.target_user.send(embed=dm_embed)
            except:
                pass
                
        except Exception as e:
            logger.error(f"Ошибка при выдаче роли: {e}")
            await interaction.response.send_message(f"❌ Ошибка при выдаче роли: {str(e)[:100]}", ephemeral=True)

active_vouches = {}

@bot.command(name='vouch')
@is_admin()
async def vouch_command(ctx, member: discord.Member, role: discord.Role):
    if member.id == ctx.author.id:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Вы не можете создать голосование за самого себя!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if role not in ctx.guild.roles:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Указанная роль не найдена на сервере!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Нельзя проводить голосование за бота!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if member.guild_permissions.administrator:
        embed = discord.Embed(
            title="⚠️ Предупреждение",
            description="Вы проводите голосование за администратора сервера. Убедитесь, что это необходимо.",
            color=COLORS['warning']
        )
        await safe_send(ctx, embed=embed)
    
    if ctx.channel.id in active_vouches:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="В этом канале уже есть активное голосование! Дождитесь его завершения.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title=f"🗳️ Голосование за повышение {member.display_name}",
        description=f"**Предложил:** {ctx.author.mention}\n"
                   f"**Пользователь:** {member.mention}\n"
                   f"**Роль:** {role.mention}\n\n"
                   f"**Правила голосования:**\n"
                   f"• Голосовать могут все участники сервера\n"
                   f"• **Нельзя голосовать за самого себя**\n"
                   f"• Для выдачи роли нужно **минимум 5 голосов ЗА**\n"
                   f"• После набора 5+ голосов появится кнопка для администратора\n"
                   f"• Только администратор может выдать роль\n"
                   f"• Голосование длится **2 часа**",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="✅ ЗА",
        value="**0** голосов",
        inline=True
    )
    
    embed.add_field(
        name="❌ ПРОТИВ",
        value="**0** голосов",
        inline=True
    )
    
    embed.add_field(
        name="📊 Всего",
        value="**0**",
        inline=True
    )
    
    embed.add_field(
        name="⏳ Условие для выдачи",
        value="Нужно **5** голосов ЗА",
        inline=False
    )
    
    embed.set_footer(text=f"Голосование создано: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Нельзя голосовать за себя")
    
    view = VouchView(member, role, ctx.author)
    
    message = await safe_send(ctx, embed=embed, view=view)
    if message:
        view.message = message
        active_vouches[ctx.channel.id] = view
        
        await ctx.send(f"@everyone Начато голосование за повышение {member.mention} до роли {role.mention}!",
                      delete_after=5)
        
        logger.info(f"Создано голосование за {member} до роли {role} пользователем {ctx.author}")

@bot.command(name='endvouch')
@is_admin()
async def end_vouch_command(ctx):
    if ctx.channel.id not in active_vouches:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="В этом канале нет активного голосования!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    view = active_vouches[ctx.channel.id]
    
    embed = discord.Embed(
        title="⚠️ Подтверждение",
        description="Вы уверены, что хотите принудительно завершить голосование?",
        color=COLORS['warning']
    )
    
    view_confirm = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        view.is_completed = True
        
        for item in view.children:
            item.disabled = True
        
        embed_result = discord.Embed(
            title="🗳️ Голосование завершено",
            description=f"Голосование за {view.target_user.mention} было принудительно завершено.",
            color=COLORS['warning']
        )
        
        embed_result.add_field(
            name="📊 Итоги",
            value=f"✅ ЗА: {len(view.votes_for)}\n"
                  f"❌ ПРОТИВ: {len(view.votes_against)}",
            inline=False
        )
        
        embed_result.add_field(
            name="👤 Завершил",
            value=interaction.user.mention,
            inline=True
        )
        
        await view.message.edit(embed=embed_result, view=view)
        
        del active_vouches[ctx.channel.id]
        
        await interaction.response.edit_message(content="✅ Голосование завершено!", embed=None, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        await interaction.response.edit_message(content="❌ Отменено", embed=None, view=None)
    
    confirm_button = discord.ui.Button(label="✅ Подтвердить", style=discord.ButtonStyle.danger)
    cancel_button = discord.ui.Button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    
    confirm_button.callback = confirm_callback
    cancel_button.callback = cancel_callback
    
    view_confirm.add_item(confirm_button)
    view_confirm.add_item(cancel_button)
    
    await safe_send(ctx, embed=embed, view=view_confirm)

@bot.command(name='vouchinfo')
async def vouch_info_command(ctx):
    if ctx.channel.id not in active_vouches:
        embed = discord.Embed(
            title="ℹ️ Информация",
            description="В этом канале нет активного голосования.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    view = active_vouches[ctx.channel.id]
    
    embed = discord.Embed(
        title="📊 Информация об активном голосовании",
        color=COLORS['info']
    )
    
    embed.add_field(name="👤 Пользователь", value=view.target_user.mention, inline=True)
    embed.add_field(name="🎭 Роль", value=view.target_role.mention, inline=True)
    embed.add_field(name="👑 Инициатор", value=view.initiator.mention, inline=True)
    
    embed.add_field(name="✅ ЗА", value=str(len(view.votes_for)), inline=True)
    embed.add_field(name="❌ ПРОТИВ", value=str(len(view.votes_against)), inline=True)
    embed.add_field(name="📊 Всего", value=str(len(view.votes_for) + len(view.votes_against)), inline=True)
    
    has_give_button = False
    for item in view.children:
        if isinstance(item, VouchGiveRoleButton):
            has_give_button = True
            break
    
    if has_give_button:
        embed.add_field(
            name="🎁 Статус",
            value="✅ Условие выполнено! Администратор может выдать роль.",
            inline=False
        )
    else:
        embed.add_field(
            name="⏳ Статус",
            value=f"Нужно ещё {max(0, 5 - len(view.votes_for))} голосов ЗА",
            inline=False
        )
    
    await safe_send(ctx, embed=embed)

async def check_expired_vouches():
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        try:
            current_time = datetime.now().timestamp()
            expired_channels = []
            
            for channel_id, view in list(active_vouches.items()):
                if view.message and (current_time - view.message.created_at.timestamp()) > 7200:
                    if not view.is_completed:
                        view.is_completed = True
                        
                        for item in view.children:
                            item.disabled = True
                        
                        embed = discord.Embed(
                            title="⏰ Время истекло",
                            description=f"Голосование за {view.target_user.mention} автоматически завершено по истечении времени.",
                            color=COLORS['warning']
                        )
                        
                        embed.add_field(
                            name="📊 Итоги",
                            value=f"✅ ЗА: {len(view.votes_for)}\n"
                                  f"❌ ПРОТИВ: {len(view.votes_against)}",
                            inline=False
                        )
                        
                        try:
                            await view.message.edit(embed=embed, view=view)
                        except:
                            pass
                    
                    expired_channels.append(channel_id)
            
            for channel_id in expired_channels:
                if channel_id in active_vouches:
                    del active_vouches[channel_id]
            
        except Exception as e:
            logger.error(f"Ошибка в проверке истекших голосований: {e}")
        
        await asyncio.sleep(60)

# ========== СИСТЕМА ВЕРИФИКАЦИИ РОЛЕЙ ==========

# Хранилище настроек верификации для каждого сервера
# {guild_id: {"verify_role_id": int, "unverify_role_id": int}}
verify_settings = {}

@bot.command(name='verifyrole')
@is_admin()
async def set_verify_roles(ctx, verify_role: discord.Role, unverify_role: discord.Role):
    """
    Установить роли для системы верификации
    
    Пример: !verifyrole @Верифицирован @Неверифицирован
    
    Первая роль - будет выдаваться при !verify
    Вторая роль - будет сниматься при !verify
    """
    global verify_settings
    
    verify_settings[ctx.guild.id] = {
        "verify_role_id": verify_role.id,
        "unverify_role_id": unverify_role.id
    }
    
    embed = discord.Embed(
        title="✅ Роли верификации установлены",
        description=f"Теперь команда `!verify` будет работать с этими ролями:",
        color=COLORS['success']
    )
    
    embed.add_field(
        name="📌 Выдаваемая роль",
        value=verify_role.mention,
        inline=True
    )
    
    embed.add_field(
        name="🗑️ Снимаемая роль",
        value=unverify_role.mention,
        inline=True
    )
    
    embed.add_field(
        name="📋 Пример использования",
        value=f"`{PREFIX}verify @пользователь`",
        inline=False
    )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='clearverifyroles')
@is_admin()
async def unset_verify_roles(ctx):
    global verify_settings
    
    if ctx.guild.id not in verify_settings:
        embed = discord.Embed(
            title="ℹ️ Роли не настроены",
            description="На этом сервере не настроены роли верификации.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    del verify_settings[ctx.guild.id]
    
    embed = discord.Embed(
        title="✅ Настройки сброшены",
        description="Роли верификации удалены. Теперь команда `!verify` не будет работать до новой настройки.",
        color=COLORS['success']
    )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='verify')
@is_admin_or_mod()
async def verify_user(ctx, member: discord.Member):
    global verify_settings
    
    if ctx.guild.id not in verify_settings:
        embed = discord.Embed(
            title="❌ Роли не настроены",
            description=f"Сначала используйте `{PREFIX}verifyrole @роль1 @роль2` чтобы установить роли верификации.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    settings = verify_settings[ctx.guild.id]
    verify_role_id = settings["verify_role_id"]
    unverify_role_id = settings["unverify_role_id"]
    
    verify_role = ctx.guild.get_role(verify_role_id)
    unverify_role = ctx.guild.get_role(unverify_role_id)
    
    if not verify_role:
        embed = discord.Embed(
            title="❌ Роль не найдена",
            description=f"Роль для верификации (ID: {verify_role_id}) больше не существует на сервере.\n"
                       f"Используйте `{PREFIX}verifyrole` чтобы настроить заново.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if not unverify_role:
        embed = discord.Embed(
            title="❌ Роль не найдена",
            description=f"Роль для снятия (ID: {unverify_role_id}) больше не существует на сервере.\n"
                       f"Используйте `{PREFIX}verifyrole` чтобы настроить заново.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if not ctx.guild.me.guild_permissions.manage_roles:
        embed = discord.Embed(
            title="❌ Недостаточно прав",
            description="У бота нет прав на управление ролями!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if verify_role.position >= ctx.guild.me.top_role.position:
        embed = discord.Embed(
            title="❌ Ошибка иерархии",
            description=f"Роль {verify_role.mention} находится выше или на том же уровне, что и роль бота.\n"
                       f"Переместите роль бота выше в списке ролей.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    results = []
    errors = []
    
    if unverify_role in member.roles:
        try:
            await member.remove_roles(unverify_role, reason=f"Верификация пользователя (админ: {ctx.author})")
            results.append(f"✅ Снята роль {unverify_role.mention}")
        except discord.Forbidden:
            errors.append(f"❌ Нет прав для снятия роли {unverify_role.mention}")
        except Exception as e:
            errors.append(f"❌ Ошибка при снятии роли: {str(e)[:50]}")
    else:
        results.append(f"ℹ️ У пользователя нет роли {unverify_role.mention}")
    
    if verify_role not in member.roles:
        try:
            await member.add_roles(verify_role, reason=f"Верификация пользователя (админ: {ctx.author})")
            results.append(f"✅ Выдана роль {verify_role.mention}")
        except discord.Forbidden:
            errors.append(f"❌ Нет прав для выдачи роли {verify_role.mention}")
        except Exception as e:
            errors.append(f"❌ Ошибка при выдаче роли: {str(e)[:50]}")
    else:
        results.append(f"ℹ️ У пользователя уже есть роль {verify_role.mention}")
    
    embed = discord.Embed(
        title="🔐 Верификация пользователя",
        description=f"**Пользователь:** {member.mention}\n**Админ:** {ctx.author.mention}",
        color=COLORS['success'] if not errors else COLORS['warning']
    )
    
    if results:
        embed.add_field(
            name="📋 Результаты",
            value="\n".join(results),
            inline=False
        )
    
    if errors:
        embed.add_field(
            name="❌ Ошибки",
            value="\n".join(errors),
            inline=False
        )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='verifyinfo')
@is_admin_or_mod()
async def verify_info(ctx):
    global verify_settings
    
    if ctx.guild.id not in verify_settings:
        embed = discord.Embed(
            title="ℹ️ Настройки верификации",
            description=f"На этом сервере не настроены роли верификации.\n"
                       f"Используйте `{PREFIX}verifyrole @роль1 @роль2` чтобы настроить.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    settings = verify_settings[ctx.guild.id]
    verify_role = ctx.guild.get_role(settings["verify_role_id"])
    unverify_role = ctx.guild.get_role(settings["unverify_role_id"])
    
    embed = discord.Embed(
        title="🔐 Настройки верификации",
        color=COLORS['info']
    )
    
    embed.add_field(
        name="✅ Verify роль",
        value=verify_role.mention if verify_role else f"Роль не найдена (ID: {settings['verify_role_id']})",
        inline=True
    )
    
    embed.add_field(
        name="❌ Unverify роль",
        value=unverify_role.mention if unverify_role else f"Роль не найдена (ID: {settings['unverify_role_id']})",
        inline=True
    )
    
    embed.add_field(
        name="📋 Команды",
        value=f"`{PREFIX}verify @user` - верифицировать\n"
              f"`{PREFIX}clearverifyroles` - сбросить настройки",
        inline=False
    )
    
    await safe_send(ctx, embed=embed)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def get_guild_settings(guild_id: int):
    if guild_id not in GUILD_ROLE_SETTINGS:
        GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
        GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    return GUILD_ROLE_SETTINGS[guild_id], GUILD_ROLE_COLORS[guild_id]

async def safe_send(ctx, content=None, embed=None, file=None, view=None):
    try:
        if file:
            return await ctx.send(content=content, embed=embed, file=file, view=view)
        else:
            return await ctx.send(content=content, embed=embed, view=view)
    except discord.Forbidden:
        try:
            await ctx.author.send("❌ У меня нет прав на отправку сообщений в том канале, где вы использовали команду!")
        except:
            logger.error(f"Не могу отправить сообщение пользователю {ctx.author}")
        return None
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")
        return None

async def safe_edit(message, content=None, embed=None, view=None):
    try:
        return await message.edit(content=content, embed=embed, view=view)
    except Exception as e:
        logger.error(f"Ошибка при редактировании сообщения: {e}")
        return None

# ========== ОБРАБОТКА ОШИБОК ==========

@bot.command(name='help')
async def help_command(ctx):
    """Показать все команды"""
    is_user_admin = False
    try:
        is_user_admin = ctx.author.guild_permissions.administrator or any(
            admin_role_id in [role.id for role in ctx.author.roles] 
            for admin_role_id in ADMIN_ROLE_IDS
        )
    except:
        pass
    
    embed = discord.Embed(
        title="📚 Помощь по командам бота",
        description=f"**Префикс команд:** `{PREFIX}`\nВсе команды начинаются с префикса `{PREFIX}`\nПример: `{PREFIX}points @user`",
        color=COLORS['info']
    )
    
    # Команды для всех пользователей
    user_commands = (
        f"`{PREFIX}points [@user]` - Проверить поинты\n"
        f"`{PREFIX}leaderboard` - Топ пользователей\n"
        f"`{PREFIX}roles` - Система ролей\n"
        f"`{PREFIX}ping` - Статус бота"
    )
    embed.add_field(name="👤 Для всех", value=user_commands, inline=False)
    
    if is_user_admin:
        # Поинты (админ)
        points_commands = (
            f"`{PREFIX}addpoints @user кол-во [причина]` - Выдать поинты\n"
            f"`{PREFIX}removepoints @user кол-во [причина]` - Забрать поинты\n"
            f"`{PREFIX}setpoints @user кол-во [причина]` - Установить поинты\n"
            f"`{PREFIX}addpoints_multi кол-во @user1 @user2...` - Массовая выдача\n"
            f"`{PREFIX}resetpoints` - Сброс ВСЕХ поинтов"
        )
        embed.add_field(name="💰 Поинты (админ)", value=points_commands, inline=False)
        
        # Роли за поинты (админ)
        role_commands = (
            f"`{PREFIX}addrole кол-во \"название\"` - Добавить роль\n"
            f"`{PREFIX}removerole кол-во` - Удалить роль\n"
            f"`{PREFIX}removerolebyname \"название\"` - Удалить по названию\n"
            f"`{PREFIX}editrole старое_кол-во новое_кол-во [название]` - Изменить\n"
            f"`{PREFIX}setrolecolor кол-во цвет` - Установить цвет\n"
            f"`{PREFIX}reorderroles` - Пересоздать роли\n"
            f"`{PREFIX}updateroles` - Обновить роли у всех\n"
            f"`{PREFIX}saveroles` - Сохранить конфиг\n"
            f"`{PREFIX}reloadroles` - Загрузить из БД"
        )
        embed.add_field(name="🎭 Роли за поинты (админ)", value=role_commands, inline=False)
        
        # Админские роли
        admin_role_commands = (
            f"`{PREFIX}addadminrole @роль` - Добавить админ-роль\n"
            f"`{PREFIX}removeadminrole @роль` - Удалить админ-роль\n"
            f"`{PREFIX}listadminroles` - Список админ-ролей\n"
            f"`{PREFIX}clearadminroles` - Очистить админ-роли"
        )
        embed.add_field(name="👑 Админские роли", value=admin_role_commands, inline=False)
        
        # Модераторские роли (управление)
        mod_role_commands = (
            f"`{PREFIX}addmodrole @роль` - Добавить мод-роль\n"
            f"`{PREFIX}removemodrole @роль` - Удалить мод-роль\n"
            f"`{PREFIX}listmodroles` - Список мод-ролей\n"
            f"`{PREFIX}clearmodroles` - Очистить мод-роли"
        )
        embed.add_field(name="🛡️ Модераторские роли", value=mod_role_commands, inline=False)
        
        # Блокировка каналов (админ/мод)
        lock_commands = (
            f"`{PREFIX}addchannel #канал` - Добавить канал в список\n"
            f"`{PREFIX}removechannel [#канал]` - Удалить канал из списка\n"
            f"`{PREFIX}listchannels` - Список каналов\n"
            f"`{PREFIX}lockrole @роль` - Установить роль для блокировки\n"
            f"`{PREFIX}lock [send/view/both]` - Заблокировать каналы\n"
            f"`{PREFIX}unlock` - Разблокировать каналы\n"
            f"`{PREFIX}currentrole` - Текущая установленная роль\n"
            f"`{PREFIX}resetrole` - Сбросить роль\n"
            f"`{PREFIX}lockinfo` - Информация о текущих блокировках"
        )
        embed.add_field(name="🔒 Блокировка каналов (админ/мод)", value=lock_commands, inline=False)
        
        # Голосование
        vouch_commands = (
            f"`{PREFIX}vouch @user @роль` - Создать голосование\n"
            f"`{PREFIX}endvouch` - Завершить голосование\n"
            f"`{PREFIX}vouchinfo` - Инфо о голосовании"
        )
        embed.add_field(name="🗳️ Голосование (админ)", value=vouch_commands, inline=False)
        
        # Верификация
        verify_commands = (
            f"`{PREFIX}verifyrole @роль1 @роль2` - Установить роли\n"
            f"`{PREFIX}verify @user` - Верифицировать пользователя\n"
            f"`{PREFIX}verifyinfo` - Текущие настройки\n"
            f"`{PREFIX}clearverifyroles` - Сбросить настройки"
        )
        embed.add_field(name="✅ Верификация (админ/мод)", value=verify_commands, inline=False)
        
        # Экспорт
        embed.add_field(name="📊 Экспорт", value=f"`{PREFIX}export` - CSV файл", inline=False)
    
    # Информация о системе
    admin_roles_text = []
    if ADMIN_ROLE_IDS:
        for role_id in ADMIN_ROLE_IDS[:3]:
            role = ctx.guild.get_role(role_id)
            if role:
                admin_roles_text.append(f"• {role.mention}")
        if len(ADMIN_ROLE_IDS) > 3:
            admin_roles_text.append(f"*... и ещё {len(ADMIN_ROLE_IDS)-3}*")
    else:
        admin_roles_text = ["• Не настроены"]
    
    mod_roles_text = []
    if MOD_ROLE_IDS:
        for role_id in MOD_ROLE_IDS[:3]:
            role = ctx.guild.get_role(role_id)
            if role:
                mod_roles_text.append(f"• {role.mention}")
        if len(MOD_ROLE_IDS) > 3:
            mod_roles_text.append(f"*... и ещё {len(MOD_ROLE_IDS)-3}*")
    else:
        mod_roles_text = ["• Не настроены"]
    
    role_settings, _ = get_guild_settings(ctx.guild.id)
    roles_count = len(role_settings)
    
    embed.add_field(
        name="ℹ️ Система",
        value=f"**Серверов:** {len(bot.guilds)} | **Ролей за поинты:** {roles_count}\n"
              f"**Админ роли:**\n{chr(10).join(admin_roles_text)}\n"
              f"**Модераторские роли:**\n{chr(10).join(mod_roles_text)}",
        inline=False
    )
    
    embed.set_footer(
        text=f"Запрошено: {ctx.author.display_name} | {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None
    )
    
    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    
    await safe_send(ctx, embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    
    if not ctx.channel.permissions_for(ctx.guild.me).send_messages:
        logger.error(f"Ошибка команды в канале без прав на отправку: {error}")
        return
    
    try:
        if isinstance(error, commands.CheckFailure):
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
            
            if isinstance(error, discord.Forbidden):
                pass
            else:
                embed = discord.Embed(
                    title="❌ Неизвестная ошибка",
                    description="Произошла неизвестная ошибка. Администратор уже уведомлен.",
                    color=COLORS['error']
                )
                await safe_send(ctx, embed=embed)
                
    except Exception as e:
        logger.error(f"Критическая ошибка в обработчике ошибок: {e}")

# ========== ЗАПУСК БОТА ==========

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК DISCORD POINTS BOT")
    logger.info("=" * 50)
    logger.info(f"🤖 Префикс команд: {PREFIX}")
    logger.info(f"👑 Админские роли: {ADMIN_ROLE_IDS}")
    logger.info(f"🛡️ Модераторские роли: {MOD_ROLE_IDS}")
    logger.info(f"🌐 Порт веб-сервера: {PORT}")
    logger.info(f"🗄️  База данных: PostgreSQL")
    logger.info("🔄 Режим: 24/7 с веб-сервером и self-ping")
    logger.info("=" * 50)
    
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
