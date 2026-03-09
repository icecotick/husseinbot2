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
import aiohttp
import secrets
from urllib.parse import urlencode

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

# ========== OAuth2 НАСТРОЙКИ ==========
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = f"https://ваш-бот.onrender.com/oauth2/callback"  # ЗАМЕНИТЕ!
OAUTH2_SCOPES = ["identify", "guilds"]
oauth_states = {}

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

# ========== ГИФКИ ДЛЯ КЛАНОВ ==========
GIFS = {
    'ally': 'https://cdn.discordapp.com/attachments/1436012207606595774/1480486064723595324/aniyuki-gojo-satoru-gif-23.gif?ex=69afd997&is=69ae8817&hm=d5392f0643225fb1391075e829b488e69db9386b9cfed8479ce4b48ae3cb2220&',
    'enemy': 'https://media.tenor.com/hp1qKBQclPMAAAPo/jujutsu-kaisen-shibuya-arc-sukuna-domain-expansion.mp4',
    'peace': 'https://cdn.discordapp.com/attachments/1460973139474382879/1461410738697670687/razdelitelnaya-liniya-animatsionnaya-kartinka-0281.gif?ex=69a7c20f&is=69a6708f&hm=ed0667030f415d7adf07ba5b81b075a0ef8b9b192ebf119d9d39d8d479a69acc&'
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
            
            # Таблица для ручного добавления врагов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS manual_enemies (
                    user_id BIGINT,
                    guild_id BIGINT,
                    username TEXT,
                    server_name TEXT,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    detected_by BIGINT,
                    reason TEXT,
                    PRIMARY KEY (user_id, guild_id)
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
    
    # Методы для ручного добавления врагов
    async def add_manual_enemy(self, user_id: int, guild_id: int, username: str, server_name: str, detected_by: int, reason: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO manual_enemies (user_id, guild_id, username, server_name, detected_by, reason)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (user_id, guild_id) 
                DO UPDATE SET reason = CONCAT(manual_enemies.reason, ' | ', EXCLUDED.reason),
                             detected_at = CURRENT_TIMESTAMP
            ''', user_id, guild_id, username, server_name, detected_by, reason)
    
    async def check_manual_enemy(self, user_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetch('SELECT * FROM manual_enemies WHERE user_id = $1 ORDER BY detected_at DESC', user_id)
    
    async def get_all_manual_enemies(self, limit: int = 20, offset: int = 0):
        async with self.pool.acquire() as conn:
            return await conn.fetch('SELECT * FROM manual_enemies ORDER BY detected_at DESC LIMIT $1 OFFSET $2', limit, offset)
    
    async def count_manual_enemies(self):
        async with self.pool.acquire() as conn:
            return await conn.fetchval('SELECT COUNT(*) FROM manual_enemies')

# ========== КЛАСС ДЛЯ УПРАВЛЕНИЯ КЛАНАМИ ==========

class ClanManager:
    def __init__(self, pool):
        self.pool = pool
    
    async def init_tables(self):
        async with self.pool.acquire() as conn:
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
        async with self.pool.acquire() as conn:
            try:
                existing = await conn.fetchrow(
                    'SELECT * FROM clans WHERE guild_id = $1 AND LOWER(name) = LOWER($2) AND clan_type = $3',
                    guild_id, name, clan_type
                )
                
                if existing:
                    return False, f"Клан **{name}** уже существует в категории **{self.get_type_name(clan_type)}**"
                
                await conn.execute('''
                    INSERT INTO clans (guild_id, name, tag, clan_type, description, added_by)
                    VALUES ($1, $2, $3, $4, $5, $6)
                ''', guild_id, name, tag, clan_type, description, added_by)
                
                return True, f"✅ Клан **{name}** добавлен в категорию **{self.get_type_name(clan_type)}**"
            except Exception as e:
                logger.error(f"Ошибка добавления клана: {e}")
                return False, f"❌ Ошибка при добавлении клана: {str(e)[:100]}"
    
    async def remove_clan(self, guild_id: int, name: str, clan_type: str = None):
        async with self.pool.acquire() as conn:
            try:
                if clan_type:
                    result = await conn.execute('''
                        DELETE FROM clans 
                        WHERE guild_id = $1 AND LOWER(name) = LOWER($2) AND clan_type = $3
                    ''', guild_id, name, clan_type)
                    
                    if result == "DELETE 0":
                        return False, f"❌ Клан **{name}** не найден в категории **{self.get_type_name(clan_type)}**"
                    
                    return True, f"✅ Клан **{name}** удален из категории **{self.get_type_name(clan_type)}**"
                else:
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
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT * FROM clans 
                WHERE guild_id = $1 AND clan_type = $2
                ORDER BY name
            ''', guild_id, clan_type)
    
    async def get_all_clans(self, guild_id: int):
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
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM clans WHERE guild_id = $1', guild_id)
    
    async def search_clan(self, guild_id: int, search_term: str):
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
        types = {
            'ally': '🤝 Союзники (ALLY)',
            'enemy': '⚔️ Враги (ENEMY)',
            'peace': '🕊️ Нейтральные/Пис (PEACE)'
        }
        return types.get(clan_type, clan_type)
    
    def get_type_emoji(self, clan_type: str):
        emojis = {
            'ally': '🤝',
            'enemy': '⚔️',
            'peace': '🕊️'
        }
        return emojis.get(clan_type, '📌')

# Вспомогательная функция для получения настроек сервера
def get_guild_settings(guild_id: int):
    if guild_id not in GUILD_ROLE_SETTINGS:
        GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
        GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    return GUILD_ROLE_SETTINGS[guild_id], GUILD_ROLE_COLORS[guild_id]

# Создаем экземпляры
db = Database()
clan_manager = None

# ========== ВЕБ-СЕРВЕР И OAuth2 ==========

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

# OAuth2 обработчики
async def handle_oauth_login(request):
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Проверка серверов</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #1a1a1a; color: #fff; }}
            .container {{ max-width: 600px; margin: 0 auto; }}
            h1 {{ color: #5865F2; }}
            button {{ 
                background: #5865F2; 
                color: white; 
                border: none; 
                padding: 15px 30px; 
                font-size: 18px; 
                border-radius: 5px;
                cursor: pointer;
                transition: background 0.3s;
            }}
            button:hover {{ background: #4752C4; }}
            .info {{ margin-top: 30px; color: #888; font-size: 14px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔍 Проверка серверов пользователя</h1>
            <p>Нажмите кнопку ниже, чтобы авторизоваться через Discord</p>
            <a href="/oauth2/start"><button>Войти через Discord</button></a>
            <div class="info">
                <p>После авторизации вы увидите список ВСЕХ серверов, где вы состоите</p>
                <p>(включая приватные и те, где нет бота)</p>
            </div>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def handle_oauth_start(request):
    state = secrets.token_urlsafe(16)
    oauth_states[state] = {"created_at": datetime.now()}
    
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(OAUTH2_SCOPES),
        "state": state,
        "prompt": "consent"
    }
    
    discord_auth_url = f"https://discord.com/api/oauth2/authorize?{urlencode(params)}"
    raise web.HTTPFound(location=discord_auth_url)

async def handle_oauth_callback(request):
    code = request.query.get('code')
    state = request.query.get('state')
    
    if state not in oauth_states:
        return web.Response(text="Ошибка: неверный state", status=400)
    
    del oauth_states[state]
    
    async with aiohttp.ClientSession() as session:
        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI
        }
        
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        async with session.post("https://discord.com/api/oauth2/token", data=data, headers=headers) as resp:
            if resp.status != 200:
                return web.Response(text="Ошибка получения токена", status=400)
            token_data = await resp.json()
        
        access_token = token_data['access_token']
        headers = {"Authorization": f"Bearer {access_token}"}
        
        async with session.get("https://discord.com/api/users/@me", headers=headers) as resp:
            user_data = await resp.json()
        
        async with session.get("https://discord.com/api/users/@me/guilds", headers=headers) as resp:
            guilds_data = await resp.json()
    
    return await create_results_page(user_data, guilds_data)

async def create_results_page(user_data, guilds_data):
    enemy_servers = []
    ally_servers = []
    neutral_servers = []
    other_servers = []
    
    for guild in guilds_data:
        guild_info = {
            "id": guild['id'],
            "name": guild['name'],
            "icon": guild.get('icon'),
            "owner": guild.get('owner', False),
            "permissions": guild.get('permissions', '0')
        }
        
        guild_id = int(guild['id'])
        
        if 'enemy' in guild['name'].lower() or 'враг' in guild['name'].lower():
            enemy_servers.append(guild_info)
        elif 'ally' in guild['name'].lower() or 'союз' in guild['name'].lower():
            ally_servers.append(guild_info)
        elif 'peace' in guild['name'].lower() or 'нейтр' in guild['name'].lower():
            neutral_servers.append(guild_info)
        else:
            other_servers.append(guild_info)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Результаты проверки</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; padding: 20px; background: #1a1a1a; color: #fff; }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            .user-info {{ background: #2d2d2d; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
            .server-list {{ margin-top: 20px; }}
            .server-item {{ padding: 10px; margin: 5px 0; border-radius: 5px; }}
            .enemy {{ background: #4a1e1e; border-left: 5px solid #f44336; }}
            .ally {{ background: #1e4a1e; border-left: 5px solid #4CAF50; }}
            .neutral {{ background: #1e3a4a; border-left: 5px solid #2196F3; }}
            .other {{ background: #2d2d2d; border-left: 5px solid #9e9e9e; }}
            .stats {{ display: flex; gap: 10px; margin: 20px 0; }}
            .stat-box {{ flex: 1; padding: 15px; text-align: center; border-radius: 5px; color: white; }}
            .stat-enemy {{ background: #f44336; }}
            .stat-ally {{ background: #4CAF50; }}
            .stat-neutral {{ background: #2196F3; }}
            .stat-other {{ background: #9e9e9e; }}
            h2, h3 {{ color: #fff; }}
            a {{ color: #5865F2; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔍 Результаты проверки</h1>
            
            <div class="user-info">
                <h2>👤 Информация о пользователе</h2>
                <p><strong>Имя:</strong> {user_data['username']}#{user_data.get('discriminator', '0')}</p>
                <p><strong>ID:</strong> {user_data['id']}</p>
                <p><strong>Всего серверов:</strong> {len(guilds_data)}</p>
            </div>
            
            <div class="stats">
                <div class="stat-box stat-enemy">⚠️ ВРАГИ<br>{len(enemy_servers)}</div>
                <div class="stat-box stat-ally">🤝 СОЮЗНИКИ<br>{len(ally_servers)}</div>
                <div class="stat-box stat-neutral">🕊️ НЕЙТРАЛЬНЫЕ<br>{len(neutral_servers)}</div>
                <div class="stat-box stat-other">📌 ДРУГИЕ<br>{len(other_servers)}</div>
            </div>
            
            <div class="server-list">
                <h2>📋 ВСЕ СЕРВЕРЫ ПОЛЬЗОВАТЕЛЯ</h2>
    """
    
    if enemy_servers:
        html += "<h3 style='color: #f44336;'>⚠️ ВРАЖЕСКИЕ СЕРВЕРЫ</h3>"
        for server in enemy_servers:
            html += f"""
                <div class="server-item enemy">
                    <strong>{server['name']}</strong> (ID: {server['id']})
                    {' 👑 Владелец' if server['owner'] else ''}
                </div>
            """
    
    if ally_servers:
        html += "<h3 style='color: #4CAF50;'>🤝 СОЮЗНЫЕ СЕРВЕРЫ</h3>"
        for server in ally_servers:
            html += f"""
                <div class="server-item ally">
                    <strong>{server['name']}</strong> (ID: {server['id']})
                    {' 👑 Владелец' if server['owner'] else ''}
                </div>
            """
    
    if neutral_servers:
        html += "<h3 style='color: #2196F3;'>🕊️ НЕЙТРАЛЬНЫЕ СЕРВЕРЫ</h3>"
        for server in neutral_servers:
            html += f"""
                <div class="server-item neutral">
                    <strong>{server['name']}</strong> (ID: {server['id']})
                    {' 👑 Владелец' if server['owner'] else ''}
                </div>
            """
    
    if other_servers:
        html += "<h3 style='color: #9e9e9e;'>📌 ДРУГИЕ СЕРВЕРЫ</h3>"
        for server in other_servers[:10]:
            html += f"""
                <div class="server-item other">
                    <strong>{server['name']}</strong> (ID: {server['id']})
                    {' 👑 Владелец' if server['owner'] else ''}
                </div>
            """
        if len(other_servers) > 10:
            html += f"<p>... и ещё {len(other_servers) - 10} серверов</p>"
    
    html += """
            </div>
            <p style="margin-top: 20px;">
                <a href="/oauth2/login">🔄 Проверить другого пользователя</a>
            </p>
        </div>
    </body>
    </html>
    """
    
    return web.Response(text=html, content_type='text/html')

async def start_web_server():
    try:
        app = web.Application()
        
        app.router.add_get('/', handle_root)
        app.router.add_get('/ping', handle_ping)
        app.router.add_get('/health', handle_health)
        app.router.add_get('/oauth2/login', handle_oauth_login)
        app.router.add_get('/oauth2/start', handle_oauth_start)
        app.router.add_get('/oauth2/callback', handle_oauth_callback)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"🌐 Веб-сервер запущен на порту {PORT}")
        logger.info(f"🔗 OAuth2 URL: http://0.0.0.0:{PORT}/oauth2/login")
        
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

@bot.event
async def on_ready():
    logger.info(f'✅ Бот {bot.user} запущен!')
    logger.info(f'📊 Серверов: {len(bot.guilds)}')
    logger.info(f'🌐 Порт веб-сервера: {PORT}')
    
    if await db.connect():
        logger.info("✅ База данных подключена")
        
        global clan_manager
        clan_manager = ClanManager(db.pool)
        await clan_manager.init_tables()
        logger.info("✅ Менеджер кланов инициализирован")
        
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
        for guild in bot.guilds:
            GUILD_ROLE_SETTINGS[guild.id] = DEFAULT_ROLE_SETTINGS.copy()
            GUILD_ROLE_COLORS[guild.id] = DEFAULT_ROLE_COLORS.copy()
    
    await start_web_server()
    
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

# ========== КОМАНДЫ ДЛЯ КЛАНОВ С ГИФКАМИ ==========

async def show_clans_by_type(ctx, clan_type: str, title: str):
    clans = await clan_manager.get_clans_by_type(ctx.guild.id, clan_type)
    
    if not clans:
        embed = discord.Embed(
            title=title,
            description=f"❌ В этой категории пока нет кланов.\nДобавьте с помощью `!addclan {clan_type} \"название\"`",
            color=COLORS['info']
        )
        embed.set_image(url=GIFS.get(clan_type, GIFS['peace']))
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title=title,
        color=COLORS['info']
    )
    
    embed.set_image(url=GIFS.get(clan_type, GIFS['peace']))
    
    for clan in clans:
        clan_name = clan['name']
        if clan['tag']:
            clan_name = f"[{clan['tag']}] {clan_name}"
        
        if clan['description']:
            embed.add_field(
                name=clan_name,
                value=clan['description'],
                inline=False
            )
        else:
            embed.add_field(
                name=clan_name,
                value="​",
                inline=False
            )
    
    count = len(clans)
    total_clans = await clan_manager.get_clan_count(ctx.guild.id)
    embed.set_footer(text=f"Всего в категории: {count} | Всего кланов на сервере: {total_clans}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='allyclans')
async def ally_clans(ctx):
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
    if clan_manager is None:
        embed = discord.Embed(
            title="⏳ Загрузка данных",
            description="Система кланов инициализируется. Попробуйте через несколько секунд.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    await show_clans_by_type(ctx, 'peace', '🕊️ Нейтральные кланы (PEACE)')

@bot.command(name='allclans')
async def all_clans(ctx):
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
        embed.set_image(url=GIFS['peace'])
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="📋 Все кланы на сервере",
        description=f"Всего кланов: **{len(clans)}**",
        color=COLORS['info']
    )
    
    embed.set_image(url=GIFS['peace'])
    
    ally_clans_list = [c for c in clans if c['clan_type'] == 'ally']
    enemy_clans_list = [c for c in clans if c['clan_type'] == 'enemy']
    peace_clans_list = [c for c in clans if c['clan_type'] == 'peace']
    
    if ally_clans_list:
        ally_text = []
        for clan in ally_clans_list[:10]:
            name = clan['name']
            if clan['tag']:
                name = f"[{clan['tag']}] {name}"
            ally_text.append(f"• {name}")
        
        if len(ally_clans_list) > 10:
            ally_text.append(f"*... и ещё {len(ally_clans_list) - 10}*")
        
        embed.add_field(
            name=f"🤝 Союзники ({len(ally_clans_list)})",
            value="\n".join(ally_text) if ally_text else "​",
            inline=False
        )
    
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
            value="\n".join(peace_text) if peace_text else "​",
            inline=False
        )
    
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
            value="\n".join(enemy_text) if enemy_text else "​",
            inline=False
        )
    
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

# ========== OAuth2 КОМАНДА ==========

@bot.command(name='oauth')
async def oauth_command(ctx):
    embed = discord.Embed(
        title="🔍 OAuth2 Проверка серверов",
        description="Перейдите по ссылке ниже, чтобы проверить ВСЕ серверы, где состоит пользователь (даже те, где нет бота)",
        color=COLORS['info']
    )
    
    oauth_url = f"https://ваш-бот.onrender.com/oauth2/login"
    
    embed.add_field(
        name="📋 Инструкция",
        value="1. Перейдите по ссылке\n"
              "2. Авторизуйтесь через Discord\n"
              "3. Увидите полный список всех серверов\n\n"
              f"🔗 **Ссылка**: [Нажмите для проверки]({oauth_url})",
        inline=False
    )
    
    embed.set_footer(text="Внимание: сайт запросит доступ к списку ваших серверов")
    
    await safe_send(ctx, embed=embed)

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ КЛАНАМИ ==========

@bot.command(name='addclan')
@is_admin()
async def add_clan(ctx, clan_type: str, name: str, tag: str = None, *, description: str = None):
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
    if clan_manager is None:
        embed = discord.Embed(
            title="❌ Система не готова",
            description="База данных еще подключается. Попробуйте через несколько секунд.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    if clan_type and clan_type.lower() not in ['ally', 'enemy', 'peace']:
        name = f"{clan_type} {name}"
        clan_type = None
    
    if clan_type:
        clan_type = clan_type.lower()
    
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

@bot.command(name='claninfo')
async def clan_info(ctx, clan_type: str, *, name: str):
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
    
    if clan['tag']:
        embed.add_field(name="📌 Тег", value=f"`[{clan['tag']}]`", inline=True)
    
    embed.add_field(name="📂 Категория", value=clan_manager.get_type_name(clan_type), inline=True)
    
    if clan['description']:
        embed.add_field(name="📝 Описание", value=clan['description'], inline=False)
    
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
        success, message = await clan_manager.update_clan_tag(
            ctx.guild.id, name, clan_type, value
        )
        embed = discord.Embed(
            title="✅ Тег обновлен" if success else "❌ Ошибка",
            description=message,
            color=COLORS['success'] if success else COLORS['error']
        )
    elif field == 'desc' or field == 'description':
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
    
    for clan_type in ['ally', 'peace', 'enemy']:
        type_clans = [c for c in clans if c['clan_type'] == clan_type]
        if type_clans:
            clan_names = []
            for clan in type_clans[:5]:
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
@is_admin()
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

@bot.command(name='lockchannels')
@is_admin()
async def lock_channels(ctx, role: discord.Role, lock_type: str = "send"):
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
    
    lock_info = {
        'send': "📝 Запрещено писать, ставить реакции и прикреплять файлы",
        'view': "👁️ Запрещено читать и писать (канал скрыт)",
        'both': "🚫 Полная блокировка"
    }
    
    embed.add_field(name="Тип блокировки", value=lock_info[lock_type.lower()], inline=False)
    
    message = await safe_send(ctx, embed=embed)
    if not message:
        return
    
    results = await lock_all_channels_in_list(ctx.guild, role, lock_type.lower())
    
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
    
    results = await unlock_all_channels_in_list(ctx.guild, role)
    
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
        
        await db.clear_all_locks(ctx.guild.id)
        
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
    
    clan_status = "✅ **Готов**" if clan_manager is not None else "⏳ **Инициализация**"
    embed.add_field(name="Система кланов", value=clan_status, inline=True)
    
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

# ========== БЫСТРЫЕ КОМАНДЫ ДЛЯ БЛОКИРОВКИ ==========

last_locked_role = {}

@bot.command(name='lockrole')
@is_admin()
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
@is_admin()
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
@is_admin()
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

@bot.command(name='addpoints_multi')
@is_admin()
async def add_points_multi(ctx, amount: int, *members: discord.Member, reason: str = "Выдано админом"):
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
    
    results = []
    success_count = 0
    
    for member in members:
        try:
            new_total = await db.add_points(member.id, ctx.guild.id, amount, ctx.author.id, reason)
            results.append(f"✅ {member.mention}: +{amount} поинтов (всего: {new_total})")
            success_count += 1
            await check_and_assign_roles(member)
        except Exception as e:
            results.append(f"❌ {member.mention}: Ошибка - {str(e)[:50]}")
    
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
    
    if len(results) <= 15:
        final_embed.add_field(
            name="👥 Результаты по пользователям",
            value="\n".join(results[:15]),
            inline=False
        )
    else:
        final_embed.add_field(
            name="ℹ️ Информация",
            value=f"Обработано {len(members)} пользователей",
            inline=False
        )
        
        if len(results) <= 100:
            file_content = "Результаты массовой выдачи поинтов:\n\n"
            file_content += f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            file_content += f"Количество поинтов: {amount}\n"
            file_content += f"Причина: {reason}\n"
            file_content += f"Выдал: {ctx.author.display_name} ({ctx.author.id})\n"
            file_content += f"Сервер: {ctx.guild.name} ({ctx.guild.id})\n\n"
            file_content += "Детали:\n" + "\n".join(results)
            
            filename = f"mass_addpoints_{ctx.guild.id}_{int(datetime.now().timestamp())}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(file_content)
            
            file = discord.File(filename)
            await safe_send(ctx, f"📋 Полные результаты для {len(members)} пользователей:", file=file)
            os.remove(filename)
    
    await safe_edit(message, embed=final_embed)

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

# ========== КОМАНДА RELOADROLES ==========

@bot.command(name='reloadroles')
@is_admin()
async def reload_roles_from_db(ctx):
    global GUILD_ROLE_SETTINGS, GUILD_ROLE_COLORS
    guild_id = ctx.guild.id
    
    try:
        loaded_settings, loaded_colors = await db.load_role_settings(guild_id)
        
        if loaded_settings:
            GUILD_ROLE_SETTINGS[guild_id] = loaded_settings
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
            GUILD_ROLE_COLORS[guild_id] = colors
            
            embed = discord.Embed(
                title="✅ Роли перезагружены",
                description=f"Загружено **{len(loaded_settings)}** ролей из базы данных",
                color=COLORS['success']
            )
            
            roles_text = []
            for p, name in sorted(GUILD_ROLE_SETTINGS[guild_id].items()):
                roles_text.append(f"• **{name}** - {p} поинтов")
            
            embed.add_field(
                name="📊 Загруженные роли",
                value="\n".join(roles_text) if roles_text else "Нет ролей",
                inline=False
            )
        else:
            GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
            GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
            
            embed = discord.Embed(
                title="ℹ️ Нет сохраненных ролей",
                description="В базе данных нет сохраненных настроек ролей. Используются стандартные настройки.",
                color=COLORS['info']
            )
        
        await safe_send(ctx, embed=embed)
        
    except Exception as e:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Не удалось загрузить роли из БД: {str(e)[:100]}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)


# ========== СИСТЕМА ВОУЧЕЙ (ГОЛОСОВАНИЯ) ==========

class VouchView(discord.ui.View):
    """Класс для отображения кнопок голосования"""
    
    def __init__(self, target_user: discord.Member, target_role: discord.Role, initiator: discord.Member):
        super().__init__(timeout=7200)  # 2 часа на голосование
        self.target_user = target_user
        self.target_role = target_role
        self.initiator = initiator
        self.votes_for = set()  # Множество ID пользователей, проголосовавших ЗА
        self.votes_against = set()  # Множество ID пользователей, проголосовавших ПРОТИВ
        self.message = None  # Сообщение с голосованием
        self.is_completed = False  # Флаг завершения голосования
        
        # Добавляем кнопки
        self.add_item(VouchForButton())
        self.add_item(VouchAgainstButton())
        self.add_item(VouchShowVotesButton())
    
    async def update_embed(self):
        """Обновить embed с текущей статистикой голосования"""
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
        
        # Прогресс-бар
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
        
        # Добавляем информацию о необходимом условии
        if len(self.votes_for) >= 5:  # Условие: минимум 5 голосов ЗА
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
        """Проверить количество голосов"""
        # Если набрано 5+ голосов ЗА, автоматически добавляем кнопку для админа
        if len(self.votes_for) >= 5 and not self.is_completed:
            # Проверяем, нет ли уже кнопки выдачи
            has_give_button = False
            for item in self.children:
                if isinstance(item, VouchGiveRoleButton):
                    has_give_button = True
                    break
            
            if not has_give_button:
                self.add_item(VouchGiveRoleButton())
                await self.update_embed()
                
                # Отправляем уведомление админам
                if self.message and self.message.guild:
                    # Пингуем админские роли
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
    """Кнопка для голосования ЗА"""
    
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.success, label="ЗА", emoji="✅", row=0)
    
    async def callback(self, interaction: discord.Interaction):
        view: VouchView = self.view
        
        # ПРОВЕРКА: Нельзя голосовать за самого себя
        if interaction.user.id == view.target_user.id:
            await interaction.response.send_message("❌ Вы не можете голосовать за самого себя!", ephemeral=True)
            return
        
        if view.is_completed:
            await interaction.response.send_message("❌ Голосование уже завершено!", ephemeral=True)
            return
        
        # Проверяем, не голосовал ли уже пользователь
        if interaction.user.id in view.votes_for:
            await interaction.response.send_message("❌ Вы уже проголосовали ЗА!", ephemeral=True)
            return
        if interaction.user.id in view.votes_against:
            # Перемещаем голос из ПРОТИВ в ЗА
            view.votes_against.remove(interaction.user.id)
            view.votes_for.add(interaction.user.id)
            await interaction.response.send_message("✅ Ваш голос изменен на ЗА!", ephemeral=True)
        else:
            view.votes_for.add(interaction.user.id)
            await interaction.response.send_message("✅ Вы проголосовали ЗА!", ephemeral=True)
        
        await view.update_embed()
        await view.check_votes()


class VouchAgainstButton(discord.ui.Button):
    """Кнопка для голосования ПРОТИВ"""
    
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="ПРОТИВ", emoji="❌", row=0)
    
    async def callback(self, interaction: discord.Interaction):
        view: VouchView = self.view
        
        # ПРОВЕРКА: Нельзя голосовать за самого себя
        if interaction.user.id == view.target_user.id:
            await interaction.response.send_message("❌ Вы не можете голосовать за самого себя!", ephemeral=True)
            return
        
        if view.is_completed:
            await interaction.response.send_message("❌ Голосование уже завершено!", ephemeral=True)
            return
        
        # Проверяем, не голосовал ли уже пользователь
        if interaction.user.id in view.votes_against:
            await interaction.response.send_message("❌ Вы уже проголосовали ПРОТИВ!", ephemeral=True)
            return
        if interaction.user.id in view.votes_for:
            # Перемещаем голос из ЗА в ПРОТИВ
            view.votes_for.remove(interaction.user.id)
            view.votes_against.add(interaction.user.id)
            await interaction.response.send_message("✅ Ваш голос изменен на ПРОТИВ!", ephemeral=True)
        else:
            view.votes_against.add(interaction.user.id)
            await interaction.response.send_message("✅ Вы проголосовали ПРОТИВ!", ephemeral=True)
        
        await view.update_embed()
        await view.check_votes()


class VouchShowVotesButton(discord.ui.Button):
    """Кнопка для показа списка проголосовавших"""
    
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.secondary, label="Кто проголосовал", emoji="📋", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view: VouchView = self.view
        
        embed = discord.Embed(
            title="📋 Список проголосовавших",
            color=discord.Color.blue()
        )
        
        # Формируем список ЗА
        if view.votes_for:
            for_voters = []
            for user_id in list(view.votes_for)[:20]:  # Показываем первые 20
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
        
        # Формируем список ПРОТИВ
        if view.votes_against:
            against_voters = []
            for user_id in list(view.votes_against)[:20]:  # Показываем первые 20
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
        
        # Добавляем информацию о том, что голосующий не может голосовать за себя
        embed.set_footer(text=f"Всего проголосовало: {len(view.votes_for) + len(view.votes_against)} | Нельзя голосовать за себя")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)




class VouchGiveRoleButton(discord.ui.Button):
    """Кнопка для выдачи роли (только для админов)"""
    
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="ВЫДАТЬ РОЛЬ", emoji="🎁", row=1)
    
    async def callback(self, interaction: discord.Interaction):
        view: VouchView = self.view
        
        # Проверяем, является ли пользователь админом
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin:
            # Проверяем кастомные админские роли
            user_role_ids = [role.id for role in interaction.user.roles]
            is_admin = any(admin_role_id in user_role_ids for admin_role_id in ADMIN_ROLE_IDS)
        
        if not is_admin:
            await interaction.response.send_message("❌ Только администраторы могут выдавать роли!", ephemeral=True)
            return
        
        if view.is_completed:
            await interaction.response.send_message("❌ Голосование уже завершено!", ephemeral=True)
            return
        
        # Проверяем условие (5+ голосов ЗА)
        if len(view.votes_for) < 5:
            await interaction.response.send_message(f"❌ Недостаточно голосов! Нужно минимум 5 голосов ЗА (сейчас {len(view.votes_for)})", ephemeral=True)
            return
        
        # Выдаем роль
        try:
            await view.target_user.add_roles(view.target_role, reason=f"Повышение по результатам голосования (админ: {interaction.user})")
            
            view.is_completed = True
            
            # Создаем embed с результатом
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
            
            # Отключаем все кнопки
            for item in view.children:
                item.disabled = True
            
            await view.message.edit(embed=embed, view=view)
            
            await interaction.response.send_message(f"✅ Роль {view.target_role.mention} успешно выдана пользователю {view.target_user.mention}!", ephemeral=True)
            
            # Отправляем уведомление в ЛС пользователю
            try:
                dm_embed = discord.Embed(
                    title="🎉 Вас повысили!",
                    description=f"На сервере **{interaction.guild.name}** вы получили роль **{view.target_role.name}** по результатам голосования!",
                    color=discord.Color.gold()
                )
                await view.target_user.send(embed=dm_embed)
            except:
                pass  # Игнорируем ошибки отправки в ЛС
                
        except Exception as e:
            logger.error(f"Ошибка при выдаче роли: {e}")
            await interaction.response.send_message(f"❌ Ошибка при выдаче роли: {str(e)[:100]}", ephemeral=True)


# Хранилище активных голосований
active_vouches = {}  # {channel_id: view}


@bot.command(name='vouch')
@is_admin()
async def vouch_command(ctx, member: discord.Member, role: discord.Role):
    """
    Создать голосование за повышение пользователя
    
    Примеры:
    !vouch @User @Роль
    !vouch @Игрок @Raider
    """
    # Проверка: инициатор не может создавать голосование за себя
    if member.id == ctx.author.id:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Вы не можете создать голосование за самого себя!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Проверяем, что роль существует на сервере
    if role not in ctx.guild.roles:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Указанная роль не найдена на сервере!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Проверяем, что пользователь не является ботом
    if member.bot:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Нельзя проводить голосование за бота!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Проверяем, что пользователь не админ (опционально)
    if member.guild_permissions.administrator:
        embed = discord.Embed(
            title="⚠️ Предупреждение",
            description="Вы проводите голосование за администратора сервера. Убедитесь, что это необходимо.",
            color=COLORS['warning']
        )
        await safe_send(ctx, embed=embed)
    
    # Проверяем, нет ли уже активного голосования в этом канале
    if ctx.channel.id in active_vouches:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="В этом канале уже есть активное голосование! Дождитесь его завершения.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Создаем embed для голосования
    embed = discord.Embed(
        title=f"🗳️ Голосование за повышение {member.displayName}",
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
    
    # Создаем view с кнопками
    view = VouchView(member, role, ctx.author)
    
    # Отправляем сообщение
    message = await safe_send(ctx, embed=embed, view=view)
    if message:
        view.message = message
        active_vouches[ctx.channel.id] = view
        
        # Отправляем дополнительное сообщение с пингом (опционально)
        await ctx.send(f"@everyone Начато голосование за повышение {member.mention} до роли {role.mention}!",
                      delete_after=5)
        
        # Логируем создание голосования
        logger.info(f"Создано голосование за {member} до роли {role} пользователем {ctx.author}")




@bot.command(name='endvouch')
@is_admin()
async def end_vouch_command(ctx):
    """
    Принудительно завершить активное голосование в текущем канале
    """
    if ctx.channel.id not in active_vouches:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="В этом канале нет активного голосования!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    view = active_vouches[ctx.channel.id]
    
    # Запрашиваем подтверждение
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
        
        # Отключаем все кнопки
        for item in view.children:
            item.disabled = True
        
        # Обновляем embed
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
        
        # Удаляем из активных голосований
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
    """
    Показать информацию об активном голосовании в текущем канале
    """
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
    
    # Проверяем, есть ли кнопка выдачи
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


# Добавляем обработчик timeout для очистки завершенных голосований
async def check_expired_vouches():
    """Периодическая проверка истекших голосований"""
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        try:
            current_time = datetime.now().timestamp()
            expired_channels = []
            
            for channel_id, view in list(active_vouches.items()):
                # Проверяем, не истекло ли время (2 часа)
                if view.message and (current_time - view.message.created_at.timestamp()) > 7200:
                    if not view.is_completed:
                        view.is_completed = True
                        
                        # Отключаем кнопки
                        for item in view.children:
                            item.disabled = True
                        
                        # Обновляем embed
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
            
            # Удаляем истекшие голосования из активных
            for channel_id in expired_channels:
                if channel_id in active_vouches:
                    del active_vouches[channel_id]
            
        except Exception as e:
            logger.error(f"Ошибка в проверке истекших голосований: {e}")
        
        await asyncio.sleep(60)  # Проверяем каждую минуту


@bot.command(name='setclangif')
@is_admin()
async def set_clan_gif(ctx, clan_type: str, gif_url: str):
    """
    Установить гифку для типа клана
    
    Типы: ally, enemy, peace
    
    Пример:
    !setclangif ally https://ссылка_на_гифку.gif
    """
    valid_types = ['ally', 'enemy', 'peace']
    
    if clan_type.lower() not in valid_types:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Доступные типы: {', '.join(valid_types)}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Сохраняем новую гифку
    GIFS[clan_type.lower()] = gif_url
    
    embed = discord.Embed(
        title="✅ Гифка обновлена",
        description=f"Для типа **{clan_type}** установлена новая гифка",
        color=COLORS['success']
    )
    embed.set_image(url=gif_url)
    
    await safe_send(ctx, embed=embed)


# ========== КОМАНДА HELP ==========

@bot.command(name='help')
async def help_command(ctx):
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
        description=f"**Префикс команд:** `{PREFIX}`\n"
                   f"Все команды начинаются с префикса `{PREFIX}`\n"
                   f"Пример: `{PREFIX}points @user`",
        color=COLORS['info']
    )
    
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
    
    if is_user_admin:
        embed.add_field(
            name="💰 **Управление поинтами (Админ)**",
            value=f"```{PREFIX}addpoints @user количество [причина]``` - Выдать поинты пользователю\n"
                  f"```{PREFIX}removepoints @user количество [причина]``` - Забрать поинты у пользователя\n"
                  f"```{PREFIX}setpoints @user количество [причина]``` - Установить точное количество поинтов\n"
                  f"```{PREFIX}addpoints_multi количество @user1 @user2 ...``` - Выдать поинты нескольким пользователям\n"
                  f"```{PREFIX}resetpoints``` - СБРОСИТЬ ВСЕ поинты на сервере (требует подтверждения)",
            inline=False
        )
        
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
                  f"```{PREFIX}saveroles``` - Сохранить конфигурацию ролей в файл\n"
                  f"```{PREFIX}reloadroles``` - Перезагрузить роли из базы данных",
            inline=False
        )
        
        embed.add_field(
            name="👑 **Управление админскими ролями (Админ)**",
            value=f"```{PREFIX}addadminrole @роль``` - Добавить роль в список админов бота\n"
                  f"```{PREFIX}removeadminrole @роль``` - Удалить роль из списка админов\n"
                  f"```{PREFIX}listadminroles``` - Показать все текущие админские роли\n"
                  f"```{PREFIX}clearadminroles``` - Удалить ВСЕ админские роли (требует подтверждения)",
            inline=False
        )
        
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
        
        embed.add_field(
            name="⚡ **Быстрые команды для блокировочной роли**",
            value=f"```{PREFIX}lockrole @роль``` - Установить роль для быстрых команд\n"
                  f"```{PREFIX}lock [тип]``` - Быстрая блокировка каналов для роли\n"
                  f"```{PREFIX}unlock``` - Быстрая разблокировка каналов для роли\n"
                  f"```{PREFIX}currentrole``` - Показать текущую роль\n"
                  f"```{PREFIX}resetrole``` - Сбросить установленную роль\n"
                  f"**Типы блокировки:** `send` (только писать), `view` (скрыть), `both` (полная)",
            inline=False
        )
        
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
        
        embed.add_field(
            name="🔍 **OAuth2 проверка серверов**",
            value=f"```{PREFIX}oauth``` - Получить ссылку для проверки ВСЕХ серверов пользователя\n"
                  f"(даже тех, где нет бота!)",
            inline=False
        )
        
        embed.add_field(
            name="📊 **Экспорт данных (Админ)**",
            value=f"```{PREFIX}export``` - Экспортировать все данные о поинтах в CSV файл",
            inline=False
        )
        
        embed.add_field(
            name="ℹ️ **Типы блокировки каналов**",
            value="• `send` - Запрет на отправку сообщений, реакций и файлов\n"
                  "• `view` - Полное скрытие канала (не видно)\n"
                  "• `both` - Полная блокировка (скрыто + нельзя писать)",
            inline=False
        )
        
        embed.add_field(
            name="📋 **Типы кланов**",
            value="• `ally` - Союзники (🤝)\n"
                  "• `enemy` - Враги (⚔️)\n"
                  "• `peace` - Нейтральные/Пис (🕊️)",
            inline=False
        )
        
        embed.add_field(
            name="🎨 **Доступные цвета для ролей**",
            value="**HEX коды:** `#FF0000` (красный), `#00FF00` (зеленый), `#0000FF` (синий)\n"
                  "**Названия:** `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `gold`, `pink`, `brown`, `black`, `white`",
            inline=False
        )
    
    admin_roles_text = []
    if ADMIN_ROLE_IDS:
        for role_id in ADMIN_ROLE_IDS[:5]:
            role = ctx.guild.get_role(role_id)
            if role:
                admin_roles_text.append(f"• {role.mention}")
            else:
                admin_roles_text.append(f"• Роль ID: `{role_id}`")
        
        if len(ADMIN_ROLE_IDS) > 5:
            admin_roles_text.append(f"*... и ещё {len(ADMIN_ROLE_IDS) - 5} ролей*")
    else:
        admin_roles_text = ["• Не настроены (только администраторы Discord)"]
    
    role_settings, _ = get_guild_settings(ctx.guild.id)
    roles_count = len(role_settings)
    if roles_count > 0:
        roles_range = f"от {min(role_settings.keys())} до {max(role_settings.keys())} поинтов"
    else:
        roles_range = "не настроены"
    
    embed.add_field(
        name="ℹ️ **Информация о системе**",
        value=f"**📊 Серверов:** {len(bot.guilds)}\n"
              f"**🎭 Ролей за поинты:** {roles_count} ({roles_range})\n"
              f"**👑 Админские роли:**\n" + "\n".join(admin_roles_text),
        inline=False
    )
    
    embed.add_field(
        name="📝 **Примеры использования**",
        value=f"`{PREFIX}points` - проверить свои поинты\n"
              f"`{PREFIX}points @User` - проверить поинты пользователя\n"
              f"`{PREFIX}leaderboard` - топ-10 пользователей\n"
              f"`{PREFIX}addpoints @User 100 Спасибо за активность` - выдать поинты\n"
              f"`{PREFIX}addrole 500 \"Легендарный рейдер\"` - добавить новую роль\n"
              f"`{PREFIX}addadminrole @Админ` - добавить админскую роль\n"
              f"`{PREFIX}addclan ally \"Братство\" [BR] \"Наши верные союзники\"` - добавить клан\n"
              f"`{PREFIX}oauth` - проверить все серверы пользователя",
        inline=False
    )
    
    embed.add_field(
        name="🔗 **Полезные ссылки**",
        value=f"• [Поддержка](https://t.me/Agentgnd)\n"
              f"• Версия бота: v2.0 (с OAuth2 и гифками)",
        inline=False
    )

    embed.set_footer(
        text=f"Запрошено: {ctx.author.display_name} | Всего команд: {len(bot.commands)} | {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        icon_url=ctx.author.display_avatar.url
    )
    
    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    
    await safe_send(ctx, embed=embed)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

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
    logger.info(f"🌐 Порт веб-сервера: {PORT}")
    logger.info(f"🗄️  База данных: PostgreSQL")
    logger.info("🔄 Режим: 24/7 с веб-сервером")
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
