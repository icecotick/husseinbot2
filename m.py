import discord
from discord.ext import commands
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
import asyncpg
import asyncio
from aiohttp import web
import socket
import time
import aiohttp
import secrets
from urllib.parse import urlencode
import json
import ast

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
DATABASE_URL = os.getenv('DATABASE_URL')
# Автоматическое определение порта для Render
PORT = int(os.getenv('PORT', '10000'))

# ========== OAuth2 НАСТРОЙКИ ==========
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = f"https://husseinbot2.onrender.com/oauth2/callback"
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
    'enemy': 'https://cdn.discordapp.com/attachments/1436012207606595774/1480496324179787857/jujutsu-kaisen-shibuya-arc-sukuna-domain-expansion.gif?ex=69afe325&is=69ae91a5&hm=cdfb1840b17659a4ddb9e5906c50a0217bd09e18f8b8feece11c20184b1971fc&',
    'peace': 'https://cdn.discordapp.com/attachments/1460973139474382879/1461410738697670687/razdelitelnaya-liniya-animatsionnaya-kartinka-0281.gif?ex=69a7c20f&is=69a6708f&hm=ed0667030f415d7adf07ba5b81b075a0ef8b9b192ebf119d9d39d8d479a69acc&'
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
            # Таблица пользователей (поинты)
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
            
            # Таблица для хранения данных OAuth2
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS oauth_data (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    access_token TEXT,
                    refresh_token TEXT,
                    expires_at TIMESTAMP,
                    guilds_data TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # ========== НОВЫЕ ТАБЛИЦЫ ДЛЯ НАСТРОЕК СЕРВЕРА ==========
            
            # Таблица настроек сервера (отключенные команды, роли и т.д.)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    disabled_commands TEXT DEFAULT '[]',
                    mod_role_ids TEXT DEFAULT '[]',
                    verify_role_id BIGINT,
                    unverify_role_id BIGINT,
                    default_lock_role_id BIGINT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by BIGINT
                )
            ''')
            
            # Таблица логов действий
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS action_logs (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    user_id BIGINT,
                    action TEXT,
                    details TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
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
            
            logger.info("✅ Все таблицы инициализированы")
    
    # ========== МЕТОДЫ ДЛЯ ПОИНТОВ ==========
    
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
    
    # ========== МЕТОДЫ ДЛЯ СПИСКА КАНАЛОВ ==========
    
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
    
    # ========== МЕТОДЫ ДЛЯ БЛОКИРОВОК ==========
    
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
    
    # ========== МЕТОДЫ ДЛЯ РОЛЕЙ ЗА ПОИНТЫ ==========
    
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
    
    # ========== МЕТОДЫ ДЛЯ OAuth2 ==========
    
    async def save_oauth_data(self, user_id: int, username: str, access_token: str, refresh_token: str, expires_in: int, guilds_data: list):
        async with self.pool.acquire() as conn:
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            await conn.execute('''
                INSERT INTO oauth_data (user_id, username, access_token, refresh_token, expires_at, guilds_data)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (user_id) DO UPDATE 
                SET access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at = EXCLUDED.expires_at,
                    guilds_data = EXCLUDED.guilds_data,
                    last_updated = CURRENT_TIMESTAMP
            ''', user_id, username, access_token, refresh_token, expires_at, str(guilds_data))
    
    async def get_oauth_data(self, user_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow('SELECT * FROM oauth_data WHERE user_id = $1', user_id)
    
    # ========== НОВЫЕ МЕТОДЫ ДЛЯ НАСТРОЕК СЕРВЕРА ==========
    
    async def get_guild_settings(self, guild_id: int):
        """Получить настройки сервера"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT * FROM guild_settings WHERE guild_id = $1',
                guild_id
            )
            
            if not result:
                # Создаем настройки по умолчанию
                default_settings = {
                    'guild_id': guild_id,
                    'disabled_commands': '[]',
                    'mod_role_ids': '[]',
                    'verify_role_id': None,
                    'unverify_role_id': None,
                    'default_lock_role_id': None
                }
                await conn.execute('''
                    INSERT INTO guild_settings 
                    (guild_id, disabled_commands, mod_role_ids, verify_role_id, unverify_role_id, default_lock_role_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                ''', guild_id, '[]', '[]', None, None, None)
                return default_settings
            
            return dict(result)
    
    async def update_guild_settings(self, guild_id: int, settings: dict, updated_by: int):
        """Обновить настройки сервера"""
        async with self.pool.acquire() as conn:
            # Проверяем существование
            exists = await conn.fetchval(
                'SELECT 1 FROM guild_settings WHERE guild_id = $1',
                guild_id
            )
            
            if exists:
                # Обновляем существующие
                await conn.execute('''
                    UPDATE guild_settings 
                    SET disabled_commands = $1,
                        mod_role_ids = $2,
                        verify_role_id = $3,
                        unverify_role_id = $4,
                        default_lock_role_id = $5,
                        updated_at = CURRENT_TIMESTAMP,
                        updated_by = $6
                    WHERE guild_id = $7
                ''', 
                    settings.get('disabled_commands', '[]'),
                    settings.get('mod_role_ids', '[]'),
                    settings.get('verify_role_id'),
                    settings.get('unverify_role_id'),
                    settings.get('default_lock_role_id'),
                    updated_by,
                    guild_id
                )
            else:
                # Создаем новые
                await conn.execute('''
                    INSERT INTO guild_settings 
                    (guild_id, disabled_commands, mod_role_ids, verify_role_id, unverify_role_id, default_lock_role_id, updated_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                ''',
                    guild_id,
                    settings.get('disabled_commands', '[]'),
                    settings.get('mod_role_ids', '[]'),
                    settings.get('verify_role_id'),
                    settings.get('unverify_role_id'),
                    settings.get('default_lock_role_id'),
                    updated_by
                )
            
            return True
    
    async def is_command_disabled(self, guild_id: int, command_name: str) -> bool:
        """Проверить, отключена ли команда на сервере"""
        settings = await self.get_guild_settings(guild_id)
        disabled = json.loads(settings.get('disabled_commands', '[]'))
        return command_name in disabled
    
    async def get_mod_role_ids(self, guild_id: int) -> List[int]:
        """Получить ID модераторских ролей для сервера"""
        settings = await self.get_guild_settings(guild_id)
        return json.loads(settings.get('mod_role_ids', '[]'))
    
    async def get_verify_roles(self, guild_id: int):
        """Получить роли верификации для сервера"""
        settings = await self.get_guild_settings(guild_id)
        return {
            'verify_role_id': settings.get('verify_role_id'),
            'unverify_role_id': settings.get('unverify_role_id')
        }
    
    async def get_default_lock_role(self, guild_id: int):
        """Получить роль по умолчанию для блокировки"""
        settings = await self.get_guild_settings(guild_id)
        return settings.get('default_lock_role_id')
    
    # ========== МЕТОДЫ ДЛЯ ЛОГОВ ==========
    
    async def add_action_log(self, guild_id: int, user_id: int, action: str, details: str = None):
        """Добавить запись в лог действий"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO action_logs (guild_id, user_id, action, details)
                VALUES ($1, $2, $3, $4)
            ''', guild_id, user_id, action, details)
    
    async def get_action_logs(self, guild_id: int, limit: int = 100, offset: int = 0):
        """Получить логи действий для сервера"""
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT * FROM action_logs 
                WHERE guild_id = $1 
                ORDER BY timestamp DESC 
                LIMIT $2 OFFSET $3
            ''', guild_id, limit, offset)
    
    async def get_user_action_logs(self, guild_id: int, user_id: int, limit: int = 50):
        """Получить логи действий конкретного пользователя"""
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT * FROM action_logs 
                WHERE guild_id = $1 AND user_id = $2
                ORDER BY timestamp DESC 
                LIMIT $3
            ''', guild_id, user_id, limit)
    
    # ========== МЕТОДЫ ДЛЯ КЛАНОВ ==========
    
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

# Глобальные переменные для настроек
GUILD_ROLE_SETTINGS = {}
GUILD_ROLE_COLORS = {}
last_locked_role = {}

# Создаем экземпляр БД
db = Database()

# ========== ПРОВЕРКИ ПРАВ ==========

def is_admin():
    """Проверка, является ли пользователь администратором сервера"""
    async def predicate(ctx):
        # Проверка прав администратора Discord
        if ctx.author.guild_permissions.administrator:
            return True
        
        # Проверка кастомных админских ролей из .env
        author_role_ids = [role.id for role in ctx.author.roles]
        if any(admin_role_id in author_role_ids for admin_role_id in ADMIN_ROLE_IDS):
            return True
        
        return False
    return commands.check(predicate)

def is_mod():
    """Проверка, является ли пользователь модератором (из настроек сервера или .env)"""
    async def predicate(ctx):
        # Проверка прав администратора Discord
        if ctx.author.guild_permissions.administrator:
            return True
        
        # Проверка кастомных админских ролей из .env
        author_role_ids = [role.id for role in ctx.author.roles]
        if any(admin_role_id in author_role_ids for admin_role_id in ADMIN_ROLE_IDS):
            return True
        
        # Проверка модераторских ролей из настроек сервера
        mod_role_ids = await db.get_mod_role_ids(ctx.guild.id)
        if any(mod_role_id in author_role_ids for mod_role_id in mod_role_ids):
            return True
        
        return False
    return commands.check(predicate)

def command_enabled():
    """Проверка, включена ли команда на сервере"""
    async def predicate(ctx):
        if ctx.command:
            is_disabled = await db.is_command_disabled(ctx.guild.id, ctx.command.name)
            if is_disabled:
                embed = discord.Embed(
                    title="❌ Команда отключена",
                    description=f"Команда `{PREFIX}{ctx.command.name}` отключена на этом сервере.",
                    color=COLORS['error']
                )
                await ctx.send(embed=embed)
                return False
        return True
    return commands.check(predicate)

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

async def check_and_assign_roles(member: discord.Member):
    try:
        guild_id = member.guild.id
        
        if guild_id not in GUILD_ROLE_SETTINGS:
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
            else:
                GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
                GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
        
        role_settings = GUILD_ROLE_SETTINGS.get(guild_id, {})
        role_colors = GUILD_ROLE_COLORS.get(guild_id, {})
        
        if not role_settings:
            return
        
        points = await db.get_user_points(member.id, guild_id)
        
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
            logger.info(f'Выдана роль {target_role_name} пользователю {member.display_name}')
            
            # Логируем действие
            await db.add_action_log(
                member.guild.id,
                member.id,
                "auto_role_assigned",
                f"Получена роль {target_role_name} за {points} поинтов"
            )
        except discord.Forbidden:
            logger.error(f'Недостаточно прав для выдачи роли {target_role_name}')
        except Exception as e:
            logger.error(f'Ошибка выдачи роли: {e}')
            
    except Exception as e:
        logger.error(f'Ошибка в check_and_assign_roles: {e}')

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

# ========== ВЕБ-СЕРВЕР ==========

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

# ========== OAuth2 Обработчики ==========

async def handle_oauth_login(request):
    """Страница с кнопкой входа через Discord"""
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
    """Начать OAuth2 авторизацию"""
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
    """Обработка callback от Discord"""
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
                error_text = await resp.text()
                logger.error(f"Ошибка получения токена: {resp.status} - {error_text}")
                return web.Response(text=f"Ошибка получения токена: {resp.status}", status=400)
            token_data = await resp.json()
        
        access_token = token_data['access_token']
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data['expires_in']
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        async with session.get("https://discord.com/api/users/@me", headers=headers) as resp:
            user_data = await resp.json()
        
        async with session.get("https://discord.com/api/users/@me/guilds", headers=headers) as resp:
            guilds_data = await resp.json()
        
        # Сохраняем в базу данных
        await db.save_oauth_data(
            int(user_data['id']), 
            user_data['username'], 
            access_token, 
            refresh_token, 
            expires_in, 
            guilds_data
        )
    
    return web.Response(
        text=f"✅ Авторизация успешна! Можете вернуться в Discord и использовать команду !checkoauth {user_data['username']}",
        content_type='text/html'
    )

# ========== ВЕБ-ПАНЕЛЬ УПРАВЛЕНИЯ ==========

async def handle_admin_panel(request):
    """Главная страница панели управления"""
    guild_id = request.match_info.get('guild_id')
    
    if not guild_id:
        # Страница выбора сервера
        html = await generate_guild_selection_page(request)
    else:
        # Страница настроек конкретного сервера
        html = await generate_guild_settings_page(request, int(guild_id))
    
    return web.Response(text=html, content_type='text/html')

async def generate_guild_selection_page(request):
    """Генерация страницы выбора сервера"""
    # Получаем токен из куки или параметра
    token = request.cookies.get('access_token') or request.query.get('token')
    
    guilds = []
    user_info = None
    
    if token:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {token}"}
            
            # Получаем информацию о пользователе
            async with session.get("https://discord.com/api/users/@me", headers=headers) as resp:
                if resp.status == 200:
                    user_info = await resp.json()
            
            # Получаем серверы пользователя
            async with session.get("https://discord.com/api/users/@me/guilds", headers=headers) as resp:
                if resp.status == 200:
                    user_guilds = await resp.json()
                    
                    # Фильтруем серверы, где пользователь администратор
                    for guild in user_guilds:
                        perms = int(guild.get('permissions', 0))
                        if perms & 0x8:  # ADMINISTRATOR permission
                            guilds.append(guild)
    
    # Если нет токена, показываем кнопку входа
    if not user_info:
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Панель управления ботом</title>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; margin: 0; padding: 20px; }}
                .container {{ max-width: 800px; margin: 0 auto; text-align: center; }}
                .login-btn {{
                    background: #5865F2;
                    color: white;
                    border: none;
                    padding: 15px 30px;
                    font-size: 18px;
                    border-radius: 5px;
                    cursor: pointer;
                    text-decoration: none;
                    display: inline-block;
                    margin-top: 20px;
                }}
                .login-btn:hover {{ background: #4752C4; }}
                h1 {{ color: #5865F2; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎮 Панель управления ботом</h1>
                <p>Для доступа к панели управления необходимо авторизоваться через Discord</p>
                <a href="/oauth2/start" class="login-btn">Войти через Discord</a>
            </div>
        </body>
        </html>
        """
    
    # Генерируем список серверов
    guilds_html = ""
    for guild in guilds:
        guilds_html += f"""
        <div class="guild-card">
            <img src="https://cdn.discordapp.com/icons/{guild['id']}/{guild['icon']}.png" class="guild-icon" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
            <div class="guild-info">
                <h3>{guild['name']}</h3>
                <p>ID: {guild['id']}</p>
                <a href="/admin/{guild['id']}" class="manage-btn">Управлять</a>
            </div>
        </div>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Панель управления - Выбор сервера</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; margin: 0; padding: 20px; }}
            .container {{ max-width: 1000px; margin: 0 auto; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }}
            .user-info {{ display: flex; align-items: center; gap: 15px; }}
            .user-avatar {{ width: 50px; height: 50px; border-radius: 50%; }}
            h1 {{ color: #5865F2; margin: 0; }}
            .guilds-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }}
            .guild-card {{
                background: #2d2d2d;
                border-radius: 10px;
                padding: 15px;
                display: flex;
                align-items: center;
                gap: 15px;
                transition: transform 0.2s;
            }}
            .guild-card:hover {{ transform: translateY(-5px); background: #3d3d3d; }}
            .guild-icon {{ width: 50px; height: 50px; border-radius: 50%; }}
            .guild-info {{ flex: 1; }}
            .guild-info h3 {{ margin: 0 0 5px 0; color: #fff; }}
            .guild-info p {{ margin: 0; color: #888; font-size: 12px; }}
            .manage-btn {{
                background: #5865F2;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 3px;
                cursor: pointer;
                text-decoration: none;
                font-size: 12px;
                display: inline-block;
                margin-top: 5px;
            }}
            .manage-btn:hover {{ background: #4752C4; }}
            .logout-btn {{
                background: #f44336;
                color: white;
                border: none;
                padding: 8px 15px;
                border-radius: 5px;
                cursor: pointer;
                text-decoration: none;
            }}
            .no-guilds {{ text-align: center; padding: 50px; color: #888; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎮 Панель управления ботом</h1>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{user_info['id']}/{user_info['avatar']}.png" class="user-avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <span>{user_info['username']}#{user_info.get('discriminator', '0')}</span>
                    <a href="/logout" class="logout-btn">Выйти</a>
                </div>
            </div>
            
            <h2>Выберите сервер для управления</h2>
            
            <div class="guilds-grid">
                {guilds_html if guilds_html else '<div class="no-guilds">У вас нет серверов с правами администратора, на которых есть бот</div>'}
            </div>
        </div>
    </body>
    </html>
    """

async def generate_guild_settings_page(request, guild_id: int):
    """Генерация страницы настроек для конкретного сервера"""
    # Получаем настройки сервера
    settings = await db.get_guild_settings(guild_id)
    
    # Получаем информацию о сервере из Discord API
    guild_info = None
    for g in bot.guilds:
        if g.id == guild_id:
            guild_info = g
            break
    
    if not guild_info:
        return "<h1>Сервер не найден</h1><p>Бот не находится на этом сервере</p>"
    
    # Получаем все роли сервера
    roles = sorted(guild_info.roles, key=lambda r: r.position, reverse=True)
    
    # Получаем список отключенных команд
    disabled_commands = json.loads(settings.get('disabled_commands', '[]'))
    
    # Получаем список модераторских ролей
    mod_role_ids = json.loads(settings.get('mod_role_ids', '[]'))
    
    # Получаем роли верификации
    verify_role_id = settings.get('verify_role_id')
    unverify_role_id = settings.get('unverify_role_id')
    
    # Получаем роль по умолчанию для блокировки
    default_lock_role_id = settings.get('default_lock_role_id')
    
    # Получаем список всех команд
    all_commands = [
        'addpoints', 'removepoints', 'setpoints', 'resetpoints', 'points', 'leaderboard',
        'addrole', 'removerole', 'editrole', 'setrolecolor', 'reorderroles', 'updateroles',
        'addchannel', 'removechannel', 'listchannels', 'lock', 'unlock', 'lockrole', 'currentrole', 'resetrole', 'lockinfo', 'clearlocks',
        'addclan', 'removeclan', 'allyclans', 'enemyclans', 'peaceclans', 'allclans', 'claninfo', 'editclan', 'searchclan', 'clearclans', 'setclangif',
        'vouch', 'endvouch', 'vouchinfo',
        'verify', 'verifyrole', 'clearverifyroles', 'verifyinfo',
        'oauth', 'checkoauth',
        'export', 'ping', 'help'
    ]
    
    # Генерируем HTML для страницы
    roles_options = ""
    for role in roles:
        if role.name != "@everyone":
            selected = "selected" if role.id in mod_role_ids else ""
            roles_options += f'<option value="{role.id}" {selected}>{role.name}</option>'
    
    verify_roles_options = '<option value="">Не выбрано</option>'
    for role in roles:
        if role.name != "@everyone":
            selected_verify = "selected" if role.id == verify_role_id else ""
            selected_unverify = "selected" if role.id == unverify_role_id else ""
            verify_roles_options += f'<option value="{role.id}" data-verify="{selected_verify}" data-unverify="{selected_unverify}">{role.name}</option>'
    
    lock_roles_options = '<option value="">Не выбрано</option>'
    for role in roles:
        if role.name != "@everyone":
            selected = "selected" if role.id == default_lock_role_id else ""
            lock_roles_options += f'<option value="{role.id}" {selected}>{role.name}</option>'
    
    commands_checkboxes = ""
    for cmd in all_commands:
        checked = "checked" if cmd not in disabled_commands else ""
        commands_checkboxes += f"""
        <label class="command-item">
            <input type="checkbox" name="command_{cmd}" {checked}> {PREFIX}{cmd}
        </label>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Настройки сервера {guild_info.name}</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; margin: 0; padding: 20px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }}
            .back-btn {{
                background: #4d4d4d;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
                cursor: pointer;
                text-decoration: none;
            }}
            h1 {{ color: #5865F2; margin: 0; }}
            .guild-icon {{ width: 60px; height: 60px; border-radius: 50%; }}
            
            .tabs {{ display: flex; gap: 10px; margin-bottom: 20px; }}
            .tab {{
                padding: 10px 20px;
                background: #2d2d2d;
                border: none;
                color: #fff;
                cursor: pointer;
                border-radius: 5px 5px 0 0;
            }}
            .tab.active {{ background: #5865F2; }}
            .tab-content {{ display: none; background: #2d2d2d; padding: 20px; border-radius: 0 5px 5px 5px; }}
            .tab-content.active {{ display: block; }}
            
            .settings-section {{
                background: #3d3d3d;
                border-radius: 5px;
                padding: 20px;
                margin-bottom: 20px;
            }}
            .settings-section h3 {{ margin-top: 0; color: #5865F2; }}
            
            .commands-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                gap: 10px;
                margin-top: 15px;
            }}
            .command-item {{
                background: #4d4d4d;
                padding: 8px;
                border-radius: 3px;
                cursor: pointer;
            }}
            .command-item:hover {{ background: #5d5d5d; }}
            
            select, input[type="text"] {{
                width: 100%;
                padding: 8px;
                margin: 5px 0 15px;
                border: 1px solid #4d4d4d;
                background: #3d3d3d;
                color: #fff;
                border-radius: 3px;
            }}
            
            .role-select {{
                height: 200px;
            }}
            
            button {{
                background: #5865F2;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
            }}
            button:hover {{ background: #4752C4; }}
            button.danger {{ background: #f44336; }}
            button.danger:hover {{ background: #d32f2f; }}
            
            .success-message {{
                background: #4CAF50;
                color: white;
                padding: 10px;
                border-radius: 5px;
                margin-bottom: 20px;
                display: none;
            }}
            
            .logs-table {{
                width: 100%;
                border-collapse: collapse;
            }}
            .logs-table th, .logs-table td {{
                padding: 10px;
                text-align: left;
                border-bottom: 1px solid #4d4d4d;
            }}
            .logs-table th {{ background: #3d3d3d; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div style="display: flex; align-items: center; gap: 15px;">
                    <img src="https://cdn.discordapp.com/icons/{guild_id}/{guild_info.icon}.png" class="guild-icon" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <h1>Настройки сервера {guild_info.name}</h1>
                </div>
                <a href="/admin" class="back-btn">← Назад к серверам</a>
            </div>
            
            <div id="successMessage" class="success-message">✅ Настройки успешно сохранены!</div>
            
            <div class="tabs">
                <button class="tab active" onclick="showTab('commands')">⚙️ Команды</button>
                <button class="tab" onclick="showTab('roles')">👥 Роли</button>
                <button class="tab" onclick="showTab('verify')">🔐 Верификация</button>
                <button class="tab" onclick="showTab('lock')">🔒 Блокировка</button>
                <button class="tab" onclick="showTab('logs')">📋 Логи</button>
            </div>
            
            <div id="commandsTab" class="tab-content active">
                <div class="settings-section">
                    <h3>Отключенные команды</h3>
                    <p>Снимите галочки с команд, которые должны быть доступны на сервере</p>
                    <form onsubmit="saveSettings(event, 'commands')">
                        <div class="commands-grid">
                            {commands_checkboxes}
                        </div>
                        <button type="submit">Сохранить настройки команд</button>
                    </form>
                </div>
            </div>
            
            <div id="rolesTab" class="tab-content">
                <div class="settings-section">
                    <h3>Модераторские роли</h3>
                    <p>Выберите роли, которые будут иметь доступ к модераторским командам</p>
                    <form onsubmit="saveSettings(event, 'mod_roles')">
                        <select name="mod_roles" class="role-select" multiple>
                            {roles_options}
                        </select>
                        <button type="submit">Сохранить модераторские роли</button>
                    </form>
                </div>
            </div>
            
            <div id="verifyTab" class="tab-content">
                <div class="settings-section">
                    <h3>Роли верификации</h3>
                    <p>Настройте роли для команды !verify</p>
                    <form onsubmit="saveSettings(event, 'verify')">
                        <label>Роль для выдачи (verify):</label>
                        <select name="verify_role">
                            {verify_roles_options}
                        </select>
                        
                        <label>Роль для снятия (unverify):</label>
                        <select name="unverify_role">
                            {verify_roles_options}
                        </select>
                        
                        <button type="submit">Сохранить роли верификации</button>
                    </form>
                </div>
            </div>
            
            <div id="lockTab" class="tab-content">
                <div class="settings-section">
                    <h3>Роль по умолчанию для блокировки</h3>
                    <p>Роль, которая будет использоваться в командах !lock и !unlock</p>
                    <form onsubmit="saveSettings(event, 'lock_role')">
                        <select name="default_lock_role">
                            {lock_roles_options}
                        </select>
                        <button type="submit">Сохранить роль для блокировки</button>
                    </form>
                </div>
            </div>
            
            <div id="logsTab" class="tab-content">
                <div class="settings-section">
                    <h3>Последние действия</h3>
                    <table class="logs-table" id="logsTable">
                        <thead>
                            <tr>
                                <th>Время</th>
                                <th>Пользователь</th>
                                <th>Действие</th>
                                <th>Детали</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr><td colspan="4">Загрузка логов...</td></tr>
                        </tbody>
                    </table>
                    <button onclick="loadLogs()" style="margin-top: 10px;">Обновить логи</button>
                </div>
            </div>
        </div>
        
        <script>
            function showTab(tabName) {{
                const tabs = document.querySelectorAll('.tab');
                const contents = document.querySelectorAll('.tab-content');
                
                tabs.forEach(t => t.classList.remove('active'));
                contents.forEach(c => c.classList.remove('active'));
                
                document.querySelector(`.tab[onclick="showTab('${{tabName}}')"]`).classList.add('active');
                document.getElementById(tabName + 'Tab').classList.add('active');
            }}
            
            async function saveSettings(event, type) {{
                event.preventDefault();
                
                let data = {{}};
                
                if (type === 'commands') {{
                    const commands = [];
                    document.querySelectorAll('input[type="checkbox"]').forEach(cb => {{
                        if (!cb.checked) {{
                            commands.push(cb.name.replace('command_', ''));
                        }}
                    }});
                    data.disabled_commands = commands;
                }}
                else if (type === 'mod_roles') {{
                    const select = document.querySelector('select[name="mod_roles"]');
                    const selected = Array.from(select.selectedOptions).map(opt => opt.value);
                    data.mod_role_ids = selected;
                }}
                else if (type === 'verify') {{
                    data.verify_role_id = document.querySelector('select[name="verify_role"]').value;
                    data.unverify_role_id = document.querySelector('select[name="unverify_role"]').value;
                }}
                else if (type === 'lock_role') {{
                    data.default_lock_role_id = document.querySelector('select[name="default_lock_role"]').value;
                }}
                
                try {{
                    const response = await fetch('/api/save_settings/{guild_id}', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(data)
                    }});
                    
                    if (response.ok) {{
                        const message = document.getElementById('successMessage');
                        message.style.display = 'block';
                        setTimeout(() => message.style.display = 'none', 3000);
                    }}
                }} catch (e) {{
                    alert('Ошибка при сохранении: ' + e);
                }}
            }}
            
            async function loadLogs() {{
                try {{
                    const response = await fetch('/api/logs/{guild_id}');
                    const logs = await response.json();
                    
                    let html = '';
                    logs.forEach(log => {{
                        html += `<tr>
                            <td>${{new Date(log.timestamp).toLocaleString()}}</td>
                            <td><code>${{log.user_id}}</code></td>
                            <td>${{log.action}}</td>
                            <td>${{log.details || ''}}</td>
                        </tr>`;
                    }});
                    
                    document.querySelector('#logsTable tbody').innerHTML = html;
                }} catch (e) {{
                    console.error('Ошибка загрузки логов:', e);
                }}
            }}
            
            // Загружаем логи при открытии вкладки
            document.querySelector('.tab[onclick="showTab(\'logs\')"]').addEventListener('click', loadLogs);
            
            // Предзаполняем select для верификации
            document.addEventListener('DOMContentLoaded', function() {{
                const verifySelect = document.querySelector('select[name="verify_role"]');
                const unverifySelect = document.querySelector('select[name="unverify_role"]');
                
                if (verifySelect) {{
                    Array.from(verifySelect.options).forEach(opt => {{
                        if (opt.getAttribute('data-verify') === 'selected') {{
                            opt.selected = true;
                        }}
                    }});
                }}
                
                if (unverifySelect) {{
                    Array.from(unverifySelect.options).forEach(opt => {{
                        if (opt.getAttribute('data-unverify') === 'selected') {{
                            opt.selected = true;
                        }}
                    }});
                }}
            }});
        </script>
    </body>
    </html>
    """

async def handle_save_settings(request):
    """Сохранение настроек сервера"""
    guild_id = int(request.match_info['guild_id'])
    
    try:
        data = await request.json()
        
        # Получаем текущие настройки
        settings = await db.get_guild_settings(guild_id)
        
        # Обновляем настройки
        if 'disabled_commands' in data:
            settings['disabled_commands'] = json.dumps(data['disabled_commands'])
        
        if 'mod_role_ids' in data:
            settings['mod_role_ids'] = json.dumps([int(id) for id in data['mod_role_ids'] if id])
        
        if 'verify_role_id' in data:
            settings['verify_role_id'] = int(data['verify_role_id']) if data['verify_role_id'] else None
        
        if 'unverify_role_id' in data:
            settings['unverify_role_id'] = int(data['unverify_role_id']) if data['unverify_role_id'] else None
        
        if 'default_lock_role_id' in data:
            settings['default_lock_role_id'] = int(data['default_lock_role_id']) if data['default_lock_role_id'] else None
        
        # Сохраняем в БД
        await db.update_guild_settings(guild_id, settings, 0)  # user_id 0 означает веб-панель
        
        return web.json_response({'status': 'success'})
    except Exception as e:
        logger.error(f"Ошибка сохранения настроек: {e}")
        return web.json_response({'status': 'error', 'message': str(e)}, status=500)

async def handle_get_logs(request):
    """Получение логов для сервера"""
    guild_id = int(request.match_info['guild_id'])
    
    try:
        logs = await db.get_action_logs(guild_id, 50)
        
        result = []
        for log in logs:
            result.append({
                'id': log['id'],
                'user_id': log['user_id'],
                'action': log['action'],
                'details': log['details'],
                'timestamp': log['timestamp'].isoformat() if log['timestamp'] else None
            })
        
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Ошибка получения логов: {e}")
        return web.json_response([], status=500)

async def handle_logout(request):
    """Выход из системы"""
    response = web.HTTPFound('/admin')
    response.del_cookie('access_token')
    return response

async def start_web_server():
    try:
        app = web.Application()
        
        # Основные маршруты
        app.router.add_get('/', handle_root)
        app.router.add_get('/ping', handle_ping)
        app.router.add_get('/health', handle_health)
        
        # OAuth2 маршруты
        app.router.add_get('/oauth2/login', handle_oauth_login)
        app.router.add_get('/oauth2/start', handle_oauth_start)
        app.router.add_get('/oauth2/callback', handle_oauth_callback)
        
        # Панель управления
        app.router.add_get('/admin', handle_admin_panel)
        app.router.add_get('/admin/{guild_id}', handle_admin_panel)
        app.router.add_post('/api/save_settings/{guild_id}', handle_save_settings)
        app.router.add_get('/api/logs/{guild_id}', handle_get_logs)
        app.router.add_get('/logout', handle_logout)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"🌐 Веб-сервер запущен на порту {PORT}")
        logger.info(f"🔗 Панель управления: http://0.0.0.0:{PORT}/admin")
        logger.info(f"🔗 OAuth2 URL: http://0.0.0.0:{PORT}/oauth2/login")
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка запуска веб-сервера: {e}")
        return False

# ========== СОБЫТИЯ БОТА ==========

@bot.event
async def on_ready():
    logger.info(f'✅ Бот {bot.user} запущен!')
    logger.info(f'📊 Серверов: {len(bot.guilds)}')
    logger.info(f'🌐 Порт веб-сервера: {PORT}')
    
    if await db.connect():
        logger.info("✅ База данных подключена")
        
        # Загружаем настройки ролей для всех серверов
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
    
    # Запускаем проверку истекших голосований
    bot.loop.create_task(check_expired_vouches())
    logger.info("✅ Запущена проверка истекших голосований")
    
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

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    
    if not ctx.channel.permissions_for(ctx.guild.me).send_messages:
        logger.error(f"Ошибка команды в канале без прав на отправку: {error}")
        return
    
    try:
        if isinstance(error, commands.CheckFailure):
            # Проверяем, отключена ли команда
            if ctx.command and await db.is_command_disabled(ctx.guild.id, ctx.command.name):
                return  # Сообщение уже отправлено в проверке
            
            embed = discord.Embed(
                title="❌ Недостаточно прав",
                description=f"У вас нет прав для использования этой команды!",
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

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ ПОИНТАМИ ==========

@bot.command(name='addpoints')
@is_mod()
@command_enabled()
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "add_points",
        f"{amount} поинтов пользователю {member.id} (причина: {reason})"
    )
    
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
@is_mod()
@command_enabled()
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "remove_points",
        f"{amount} поинтов у пользователя {member.id} (причина: {reason})"
    )
    
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
@is_mod()
@command_enabled()
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "set_points",
        f"Установлено {amount} поинтов пользователю {member.id} (причина: {reason})"
    )
    
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
@is_mod()
@command_enabled()
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
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "reset_points",
            "Сброс всех поинтов на сервере"
        )
        
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
@command_enabled()
async def check_points(ctx, member: Optional[discord.Member] = None):
    if member is None:
        member = ctx.author
    
    guild_id = ctx.guild.id
    user_id = member.id
    
    if guild_id not in GUILD_ROLE_SETTINGS:
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
        else:
            GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
            GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    
    role_settings = GUILD_ROLE_SETTINGS.get(guild_id, {})
    
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
@command_enabled()
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
        
        if guild_id not in GUILD_ROLE_SETTINGS:
            role_settings = {}
        else:
            role_settings = GUILD_ROLE_SETTINGS.get(guild_id, {})
        
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
              f"• Среднее: **{stats['avg_points']}**\n"
              f"• Максимум: **{stats['max_points']}**",
        inline=False
    )
    
    embed.set_footer(text=f"Всего участников: {stats['total_users']}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='export')
@is_mod()
@command_enabled()
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

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ РОЛЯМИ ЗА ПОИНТЫ ==========

@bot.command(name='addrole')
@is_mod()
@command_enabled()
async def add_role_for_points(ctx, points: int, *, role_name: str):
    guild_id = ctx.guild.id
    
    if guild_id not in GUILD_ROLE_SETTINGS:
        GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
        GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    
    role_settings = GUILD_ROLE_SETTINGS[guild_id]
    role_colors = GUILD_ROLE_COLORS[guild_id]
    
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "add_role",
        f"Добавлена роль {role_name} за {points} поинтов"
    )
    
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

@bot.command(name='removerole')
@is_mod()
@command_enabled()
async def remove_role_for_points(ctx, points: int):
    guild_id = ctx.guild.id
    
    if guild_id not in GUILD_ROLE_SETTINGS:
        GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
        GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    
    role_settings = GUILD_ROLE_SETTINGS[guild_id]
    role_colors = GUILD_ROLE_COLORS[guild_id]
    
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
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "remove_role",
            f"Удалена роль {role_name} за {points} поинтов"
        )
        
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

@bot.command(name='editrole')
@is_mod()
@command_enabled()
async def edit_role_for_points(ctx, old_points: int, new_points: int = None, *, new_name: str = None):
    guild_id = ctx.guild.id
    
    if guild_id not in GUILD_ROLE_SETTINGS:
        GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
        GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    
    role_settings = GUILD_ROLE_SETTINGS[guild_id]
    role_colors = GUILD_ROLE_COLORS[guild_id]
    
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "edit_role",
        f"Изменена роль {old_name} -> {new_name or old_name} (поинты: {old_points} -> {new_points or old_points})"
    )
    
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
@is_mod()
@command_enabled()
async def set_role_color(ctx, points: int, color: str):
    guild_id = ctx.guild.id
    
    if guild_id not in GUILD_ROLE_SETTINGS:
        GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
        GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    
    role_settings = GUILD_ROLE_SETTINGS[guild_id]
    role_colors = GUILD_ROLE_COLORS[guild_id]
    
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "set_role_color",
        f"Установлен цвет для роли {role_name}"
    )
    
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
    
    await safe_send(ctx, embed=embed)

@bot.command(name='reorderroles')
@is_mod()
@command_enabled()
async def reorder_roles(ctx):
    guild_id = ctx.guild.id
    
    if guild_id not in GUILD_ROLE_SETTINGS:
        GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
        GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    
    role_settings = GUILD_ROLE_SETTINGS[guild_id]
    
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
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "reorder_roles",
            "Реконструкция всех ролей"
        )
        
        status_embed = discord.Embed(
            title="⏳ Реконструкция ролей...",
            description="Начинаю пересоздание ролей...",
            color=COLORS['info']
        )
        status_msg = await interaction.followup.send(embed=status_embed)
        
        # Сохраняем текущие роли участников
        member_roles = {}
        for member in ctx.guild.members:
            for role_name in role_settings.values():
                role = discord.utils.get(ctx.guild.roles, name=role_name)
                if role and role in member.roles:
                    if member.id not in member_roles:
                        member_roles[member.id] = []
                    member_roles[member.id].append(role_name)
        
        # Удаляем старые роли
        deleted_count = 0
        for role_name in role_settings.values():
            role = discord.utils.get(ctx.guild.roles, name=role_name)
            if role:
                try:
                    await role.delete(reason="Реконструкция системы ролей")
                    deleted_count += 1
                except:
                    pass
        
        # Создаем новые роли
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
        
        # Восстанавливаем роли участникам
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
        
        # Сортируем роли
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

@bot.command(name='updateroles')
@is_mod()
@command_enabled()
async def update_all_roles(ctx):
    embed = discord.Embed(
        title="⏳ Обновление ролей",
        description=f"Начинаю обновление ролей для {len(ctx.guild.members)} участников...",
        color=COLORS['info']
    )
    
    message = await safe_send(ctx, embed=embed)
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "update_roles",
        "Массовое обновление ролей"
    )
    
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

@bot.command(name='roles')
@command_enabled()
async def show_roles(ctx):
    guild_id = ctx.guild.id
    
    if guild_id not in GUILD_ROLE_SETTINGS:
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
        else:
            GUILD_ROLE_SETTINGS[guild_id] = DEFAULT_ROLE_SETTINGS.copy()
            GUILD_ROLE_COLORS[guild_id] = DEFAULT_ROLE_COLORS.copy()
    
    role_settings = GUILD_ROLE_SETTINGS.get(guild_id, {})
    role_colors = GUILD_ROLE_COLORS.get(guild_id, {})
    
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
    
    await safe_send(ctx, embed=embed)

@bot.command(name='reloadroles')
@is_mod()
@command_enabled()
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
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "reload_roles",
            "Перезагрузка ролей из БД"
        )
        
        await safe_send(ctx, embed=embed)
        
    except Exception as e:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Не удалось загрузить роли из БД: {str(e)[:100]}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)

# ========== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ КАНАЛАМИ ==========

@bot.command(name='addchannel')
@is_mod()
@command_enabled()
async def add_channel(ctx, channel: discord.TextChannel):
    success = await db.add_channel_to_list(
        ctx.guild.id, channel.id, channel.name, ctx.author.id
    )
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "add_channel",
        f"Добавлен канал {channel.name} ({channel.id})"
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
@is_mod()
@command_enabled()
async def remove_channel(ctx, channel: Optional[discord.TextChannel] = None):
    if channel:
        success = await db.remove_channel_from_list(ctx.guild.id, channel.id)
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "remove_channel",
            f"Удален канал {channel.name} ({channel.id})"
        )
        
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
            
            # Логируем действие
            await db.add_action_log(
                ctx.guild.id,
                ctx.author.id,
                "remove_all_channels",
                "Удалены все каналы из списка"
            )
            
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
@is_mod()
@command_enabled()
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
@is_mod()
@command_enabled()
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

@bot.command(name='clearlocks')
@is_mod()
@command_enabled()
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
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "clear_locks",
            f"Удалены все блокировки ({len(locks)} шт.)"
        )
        
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

# ========== БЫСТРЫЕ КОМАНДЫ ДЛЯ БЛОКИРОВКИ ==========

@bot.command(name='lockrole')
@is_mod()
@command_enabled()
async def lockrole_command(ctx, role: discord.Role):
    global last_locked_role
    last_locked_role[ctx.guild.id] = role.id
    
    # Сохраняем в настройки сервера
    settings = await db.get_guild_settings(ctx.guild.id)
    settings['default_lock_role_id'] = role.id
    await db.update_guild_settings(ctx.guild.id, settings, ctx.author.id)
    
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

@bot.command(name='lock')
@is_mod()
@command_enabled()
async def lock_command(ctx, lock_type: str = "send"):
    global last_locked_role
    
    role_id = None
    
    # Проверяем, есть ли сохраненная роль
    if ctx.guild.id in last_locked_role:
        role_id = last_locked_role[ctx.guild.id]
    else:
        # Проверяем настройки сервера
        settings = await db.get_guild_settings(ctx.guild.id)
        role_id = settings.get('default_lock_role_id')
    
    if not role_id:
        embed = discord.Embed(
            title="❌ Роль не установлена",
            description=f"Сначала используйте `{PREFIX}lockrole @роль` чтобы указать, с какой ролью работать!\n"
                       f"Или установите роль по умолчанию в веб-панели: http://0.0.0.0:{PORT}/admin/{ctx.guild.id}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    target_role = ctx.guild.get_role(role_id)
    
    if not target_role:
        if ctx.guild.id in last_locked_role:
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "lock_channels",
        f"Блокировка для роли {target_role.name} ({target_role.id}), тип: {lock_type}"
    )
    
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

@bot.command(name='unlock')
@is_mod()
@command_enabled()
async def unlock_command(ctx):
    global last_locked_role
    
    role_id = None
    
    # Проверяем, есть ли сохраненная роль
    if ctx.guild.id in last_locked_role:
        role_id = last_locked_role[ctx.guild.id]
    else:
        # Проверяем настройки сервера
        settings = await db.get_guild_settings(ctx.guild.id)
        role_id = settings.get('default_lock_role_id')
    
    if not role_id:
        embed = discord.Embed(
            title="❌ Роль не установлена",
            description=f"Сначала используйте `{PREFIX}lockrole @роль` чтобы указать, с какой ролью работать!\n"
                       f"Или установите роль по умолчанию в веб-панели: http://0.0.0.0:{PORT}/admin/{ctx.guild.id}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    target_role = ctx.guild.get_role(role_id)
    
    if not target_role:
        if ctx.guild.id in last_locked_role:
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "unlock_channels",
        f"Разблокировка для роли {target_role.name} ({target_role.id})"
    )
    
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

@bot.command(name='currentrole')
@is_mod()
@command_enabled()
async def current_role_command(ctx):
    global last_locked_role
    
    role_id = None
    
    # Проверяем, есть ли сохраненная роль
    if ctx.guild.id in last_locked_role:
        role_id = last_locked_role[ctx.guild.id]
    else:
        # Проверяем настройки сервера
        settings = await db.get_guild_settings(ctx.guild.id)
        role_id = settings.get('default_lock_role_id')
    
    if not role_id:
        embed = discord.Embed(
            title="ℹ️ Роль не установлена",
            description=f"Сейчас не выбрана ни одна роль.\n"
                       f"Используйте `{PREFIX}lockrole @роль` чтобы установить роль для команд `!lock` и `!unlock`.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
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
@is_mod()
@command_enabled()
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

# ========== КОМАНДЫ ДЛЯ КЛАНОВ ==========

@bot.command(name='allyclans')
@command_enabled()
async def ally_clans(ctx):
    clans = await db.get_clans_by_type(ctx.guild.id, 'ally')
    
    if not clans:
        embed = discord.Embed(
            title="🤝 Союзные кланы (ALLY)",
            description=f"❌ В этой категории пока нет кланов.\nДобавьте с помощью `!addclan ally \"название\"`",
            color=COLORS['info']
        )
        embed.set_image(url=GIFS.get('ally', GIFS['peace']))
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="🤝 Союзные кланы (ALLY)",
        color=COLORS['info']
    )
    
    embed.set_image(url=GIFS.get('ally', GIFS['peace']))
    
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
    total_clans = await db.get_clan_count(ctx.guild.id)
    embed.set_footer(text=f"Всего в категории: {count} | Всего кланов на сервере: {total_clans}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='enemyclans')
@command_enabled()
async def enemy_clans(ctx):
    clans = await db.get_clans_by_type(ctx.guild.id, 'enemy')
    
    if not clans:
        embed = discord.Embed(
            title="⚔️ Вражеские кланы (ENEMY)",
            description=f"❌ В этой категории пока нет кланов.\nДобавьте с помощью `!addclan enemy \"название\"`",
            color=COLORS['info']
        )
        embed.set_image(url=GIFS.get('enemy', GIFS['peace']))
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="⚔️ Вражеские кланы (ENEMY)",
        color=COLORS['info']
    )
    
    embed.set_image(url=GIFS.get('enemy', GIFS['peace']))
    
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
    total_clans = await db.get_clan_count(ctx.guild.id)
    embed.set_footer(text=f"Всего в категории: {count} | Всего кланов на сервере: {total_clans}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='peaceclans')
@command_enabled()
async def peace_clans(ctx):
    clans = await db.get_clans_by_type(ctx.guild.id, 'peace')
    
    if not clans:
        embed = discord.Embed(
            title="🕊️ Нейтральные кланы (PEACE)",
            description=f"❌ В этой категории пока нет кланов.\nДобавьте с помощью `!addclan peace \"название\"`",
            color=COLORS['info']
        )
        embed.set_image(url=GIFS.get('peace', GIFS['peace']))
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title="🕊️ Нейтральные кланы (PEACE)",
        color=COLORS['info']
    )
    
    embed.set_image(url=GIFS.get('peace', GIFS['peace']))
    
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
    total_clans = await db.get_clan_count(ctx.guild.id)
    embed.set_footer(text=f"Всего в категории: {count} | Всего кланов на сервере: {total_clans}")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='allclans')
@command_enabled()
async def all_clans(ctx):
    clans = await db.get_all_clans(ctx.guild.id)
    
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

@bot.command(name='addclan')
@is_mod()
@command_enabled()
async def add_clan(ctx, clan_type: str, name: str, tag: str = None, *, description: str = None):
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
    
    success, message = await db.add_clan(
        ctx.guild.id, name, clan_type, tag, description, ctx.author.id
    )
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "add_clan",
        f"Добавлен клан {name} (тип: {clan_type})"
    )
    
    if success:
        embed = discord.Embed(
            title=f"{db.get_type_emoji(clan_type)} Клан добавлен",
            description=message,
            color=COLORS['success']
        )
        
        embed.add_field(name="🏷️ Название", value=f"**{name}**", inline=True)
        if tag:
            embed.add_field(name="📌 Тег", value=f"`[{tag}]`", inline=True)
        embed.add_field(name="📂 Категория", value=db.get_type_name(clan_type), inline=True)
        
        if description:
            embed.add_field(name="📝 Описание", value=description, inline=False)
        
        embed.add_field(name="👤 Добавил", value=ctx.author.mention, inline=True)
        
        count = await db.get_clan_count(ctx.guild.id)
        embed.set_footer(text=f"Всего кланов в базе: {count}")
    else:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=message,
            color=COLORS['error']
        )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='removeclan')
@is_mod()
@command_enabled()
async def remove_clan(ctx, clan_type: str = None, *, name: str):
    if clan_type and clan_type.lower() not in ['ally', 'enemy', 'peace']:
        name = f"{clan_type} {name}"
        clan_type = None
    
    if clan_type:
        clan_type = clan_type.lower()
    
    embed = discord.Embed(
        title="⚠️ Подтверждение удаления",
        description=f"Вы уверены, что хотите удалить клан **{name}**" + 
                   (f" из категории **{db.get_type_name(clan_type)}**?" if clan_type else " из всех категорий?"),
        color=COLORS['warning']
    )
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        success, message = await db.remove_clan(ctx.guild.id, name, clan_type)
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "remove_clan",
            f"Удален клан {name}" + (f" (тип: {clan_type})" if clan_type else "")
        )
        
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
@command_enabled()
async def clan_info(ctx, clan_type: str, *, name: str):
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
    
    clans = await db.get_clans_by_type(ctx.guild.id, clan_type)
    clan = None
    for c in clans:
        if c['name'].lower() == name.lower():
            clan = c
            break
    
    if not clan:
        embed = discord.Embed(
            title="❌ Клан не найден",
            description=f"Клан **{name}** не найден в категории **{db.get_type_name(clan_type)}**",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    embed = discord.Embed(
        title=f"{db.get_type_emoji(clan_type)} Информация о клане {clan['name']}",
        color=COLORS['info']
    )
    
    if clan['tag']:
        embed.add_field(name="📌 Тег", value=f"`[{clan['tag']}]`", inline=True)
    
    embed.add_field(name="📂 Категория", value=db.get_type_name(clan_type), inline=True)
    
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
@is_mod()
@command_enabled()
async def edit_clan(ctx, clan_type: str, name: str, field: str, *, value: str):
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
        success, message = await db.update_clan_tag(
            ctx.guild.id, name, clan_type, value
        )
        embed = discord.Embed(
            title="✅ Тег обновлен" if success else "❌ Ошибка",
            description=message,
            color=COLORS['success'] if success else COLORS['error']
        )
    elif field == 'desc' or field == 'description':
        success, message = await db.update_clan_description(
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
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "edit_clan",
        f"Изменен клан {name}, поле {field}"
    )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='searchclan')
@command_enabled()
async def search_clan(ctx, *, search_term: str):
    clans = await db.search_clan(ctx.guild.id, search_term)
    
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
                name=f"{db.get_type_emoji(clan_type)} {db.get_type_name(clan_type)} ({len(type_clans)})",
                value="\n".join(clan_names),
                inline=False
            )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='clearclans')
@is_mod()
@command_enabled()
async def clear_clans(ctx):
    count = await db.get_clan_count(ctx.guild.id)
    
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
        
        await db.clear_all_clans(ctx.guild.id)
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "clear_clans",
            f"Удалены все кланы ({count} шт.)"
        )
        
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

@bot.command(name='setclangif')
@is_mod()
@command_enabled()
async def set_clan_gif(ctx, clan_type: str, gif_url: str):
    valid_types = ['ally', 'enemy', 'peace']
    
    if clan_type.lower() not in valid_types:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Доступные типы: {', '.join(valid_types)}",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    GIFS[clan_type.lower()] = gif_url
    
    embed = discord.Embed(
        title="✅ Гифка обновлена",
        description=f"Для типа **{clan_type}** установлена новая гифка",
        color=COLORS['success']
    )
    embed.set_image(url=gif_url)
    
    await safe_send(ctx, embed=embed)

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
                    # Получаем админские роли из настроек
                    mod_role_ids = await db.get_mod_role_ids(self.message.guild.id)
                    admin_mentions = []
                    
                    for role_id in mod_role_ids:
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
        
        # Проверяем права (модератор или админ)
        is_mod = False
        if interaction.user.guild_permissions.administrator:
            is_mod = True
        else:
            user_role_ids = [role.id for role in interaction.user.roles]
            mod_role_ids = await db.get_mod_role_ids(interaction.guild.id)
            if any(mod_role_id in user_role_ids for mod_role_id in mod_role_ids):
                is_mod = True
        
        if not is_mod:
            await interaction.response.send_message("❌ Только модераторы и администраторы могут выдавать роли!", ephemeral=True)
            return
        
        if view.is_completed:
            await interaction.response.send_message("❌ Голосование уже завершено!", ephemeral=True)
            return
        
        if len(view.votes_for) < 5:
            await interaction.response.send_message(f"❌ Недостаточно голосов! Нужно минимум 5 голосов ЗА (сейчас {len(view.votes_for)})", ephemeral=True)
            return
        
        try:
            await view.target_user.add_roles(view.target_role, reason=f"Повышение по результатам голосования (модератор: {interaction.user})")
            
            view.is_completed = True
            
            # Логируем действие
            await db.add_action_log(
                interaction.guild.id,
                interaction.user.id,
                "vouch_give_role",
                f"Выдана роль {view.target_role.name} пользователю {view.target_user.id} по голосованию"
            )
            
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
@is_mod()
@command_enabled()
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
                   f"• После набора 5+ голосов появится кнопка для модератора\n"
                   f"• Только модераторы могут выдать роль\n"
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
@is_mod()
@command_enabled()
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
        
        # Логируем действие
        await db.add_action_log(
            ctx.guild.id,
            ctx.author.id,
            "end_vouch",
            f"Принудительное завершение голосования за {view.target_user.id}"
        )
        
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
@command_enabled()
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
            value="✅ Условие выполнено! Модератор может выдать роль.",
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

# ========== СИСТЕМА ВЕРИФИКАЦИИ ==========

@bot.command(name='verifyrole')
@is_mod()
@command_enabled()
async def set_verify_roles(ctx, verify_role: discord.Role, unverify_role: discord.Role):
    """
    Установить роли для системы верификации
    
    Пример: !verifyrole @Верифицирован @Неверифицирован
    """
    
    # Получаем текущие настройки
    settings = await db.get_guild_settings(ctx.guild.id)
    settings['verify_role_id'] = verify_role.id
    settings['unverify_role_id'] = unverify_role.id
    
    # Сохраняем в БД
    await db.update_guild_settings(ctx.guild.id, settings, ctx.author.id)
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "set_verify_roles",
        f"Verify: {verify_role.id}, Unverify: {unverify_role.id}"
    )
    
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
@is_mod()
@command_enabled()
async def unset_verify_roles(ctx):
    """Сбросить настройки верификации на сервере"""
    
    # Получаем текущие настройки
    settings = await db.get_guild_settings(ctx.guild.id)
    old_verify = settings.get('verify_role_id')
    old_unverify = settings.get('unverify_role_id')
    
    if not old_verify and not old_unverify:
        embed = discord.Embed(
            title="ℹ️ Роли не настроены",
            description="На этом сервере не настроены роли верификации.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Удаляем настройки
    settings['verify_role_id'] = None
    settings['unverify_role_id'] = None
    await db.update_guild_settings(ctx.guild.id, settings, ctx.author.id)
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "clear_verify_roles",
        f"Удалены роли верификации"
    )
    
    embed = discord.Embed(
        title="✅ Настройки сброшены",
        description="Роли верификации удалены. Теперь команда `!verify` не будет работать до новой настройки.",
        color=COLORS['success']
    )
    
    await safe_send(ctx, embed=embed)

@bot.command(name='verify')
@is_mod()
@command_enabled()
async def verify_user(ctx, member: discord.Member):
    """
    Верифицировать пользователя (выдать verify роль и снять unverify)
    
    Пример: !verify @user
    """
    
    # Получаем настройки
    settings = await db.get_guild_settings(ctx.guild.id)
    verify_role_id = settings.get('verify_role_id')
    unverify_role_id = settings.get('unverify_role_id')
    
    if not verify_role_id or not unverify_role_id:
        embed = discord.Embed(
            title="❌ Роли не настроены",
            description=f"Сначала используйте `{PREFIX}verifyrole @роль1 @роль2` чтобы установить роли верификации.",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Получаем объекты ролей
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
    
    # Проверяем права бота
    if not ctx.guild.me.guild_permissions.manage_roles:
        embed = discord.Embed(
            title="❌ Недостаточно прав",
            description="У бота нет прав на управление ролями!",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Проверяем иерархию ролей
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
    
    # Снимаем unverify роль (если есть)
    if unverify_role in member.roles:
        try:
            await member.remove_roles(unverify_role, reason=f"Верификация пользователя (модератор: {ctx.author})")
            results.append(f"✅ Снята роль {unverify_role.mention}")
        except discord.Forbidden:
            errors.append(f"❌ Нет прав для снятия роли {unverify_role.mention}")
        except Exception as e:
            errors.append(f"❌ Ошибка при снятии роли: {str(e)[:50]}")
    else:
        results.append(f"ℹ️ У пользователя нет роли {unverify_role.mention}")
    
    # Выдаем verify роль (если ещё нет)
    if verify_role not in member.roles:
        try:
            await member.add_roles(verify_role, reason=f"Верификация пользователя (модератор: {ctx.author})")
            results.append(f"✅ Выдана роль {verify_role.mention}")
        except discord.Forbidden:
            errors.append(f"❌ Нет прав для выдачи роли {verify_role.mention}")
        except Exception as e:
            errors.append(f"❌ Ошибка при выдаче роли: {str(e)[:50]}")
    else:
        results.append(f"ℹ️ У пользователя уже есть роль {verify_role.mention}")
    
    # Логируем действие
    await db.add_action_log(
        ctx.guild.id,
        ctx.author.id,
        "verify_user",
        f"Верификация пользователя {member.id}"
    )
    
    # Создаем embed с результатом
    embed = discord.Embed(
        title="🔐 Верификация пользователя",
        description=f"**Пользователь:** {member.mention}\n**Модератор:** {ctx.author.mention}",
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
@is_mod()
@command_enabled()
async def verify_info(ctx):
    """
    Показать текущие настройки верификации на сервере
    """
    
    settings = await db.get_guild_settings(ctx.guild.id)
    verify_role_id = settings.get('verify_role_id')
    unverify_role_id = settings.get('unverify_role_id')
    
    if not verify_role_id or not unverify_role_id:
        embed = discord.Embed(
            title="ℹ️ Настройки верификации",
            description=f"На этом сервере не настроены роли верификации.\n"
                       f"Используйте `{PREFIX}verifyrole @роль1 @роль2` чтобы настроить.",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    verify_role = ctx.guild.get_role(verify_role_id)
    unverify_role = ctx.guild.get_role(unverify_role_id)
    
    embed = discord.Embed(
        title="🔐 Настройки верификации",
        color=COLORS['info']
    )
    
    embed.add_field(
        name="✅ Verify роль",
        value=verify_role.mention if verify_role else f"Роль не найдена (ID: {verify_role_id})",
        inline=True
    )
    
    embed.add_field(
        name="❌ Unverify роль",
        value=unverify_role.mention if unverify_role else f"Роль не найдена (ID: {unverify_role_id})",
        inline=True
    )
    
    embed.add_field(
        name="📋 Команды",
        value=f"`{PREFIX}verify @user` - верифицировать\n"
              f"`{PREFIX}clearverifyroles` - сбросить настройки",
        inline=False
    )
    
    await safe_send(ctx, embed=embed)

# ========== OAuth2 КОМАНДЫ ==========

@bot.command(name='oauth')
@command_enabled()
async def oauth_command(ctx):
    """Получить ссылку для OAuth2 проверки всех серверов пользователя"""
    embed = discord.Embed(
        title="🔍 OAuth2 Проверка серверов",
        description="Перейдите по ссылке ниже, чтобы проверить ВСЕ серверы, где состоит пользователь (даже те, где нет бота)",
        color=COLORS['info']
    )
    
    oauth_url = f"https://husseinbot2.onrender.com/oauth2/login"
    
    embed.add_field(
        name="📋 Инструкция",
        value="1. Перейдите по ссылке\n"
              "2. Авторизуйтесь через Discord\n"
              "3. Вернитесь в Discord и используйте !checkoauth @user\n\n"
              f"🔗 **Ссылка**: [Нажмите для проверки]({oauth_url})",
        inline=False
    )
    
    embed.set_footer(text="Внимание: сайт запросит доступ к списку ваших серверов")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='checkoauth')
@is_mod()
@command_enabled()
async def check_oauth_user(ctx, user: discord.User):
    """
    Проверить данные OAuth2 пользователя (если он авторизовался)
    Показывает ВСЕ серверы пользователя
    
    Пример: !checkoauth @user
    """
    data = await db.get_oauth_data(user.id)
    
    if not data:
        embed = discord.Embed(
            title="❌ Данные не найдены",
            description=f"Пользователь {user.mention} еще не авторизовался через OAuth2",
            color=COLORS['error']
        )
        embed.add_field(
            name="📋 Инструкция",
            value=f"Попросите пользователя перейти по ссылке `{PREFIX}oauth` и авторизоваться",
            inline=False
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Парсим сохраненные данные о серверах
    try:
        guilds_data = ast.literal_eval(data['guilds_data'])
        if not isinstance(guilds_data, list):
            guilds_data = []
    except:
        guilds_data = []
    
    if not guilds_data:
        embed = discord.Embed(
            title="ℹ️ Нет данных о серверах",
            description=f"У пользователя {user.mention} нет сохраненных серверов",
            color=COLORS['info']
        )
        await safe_send(ctx, embed=embed)
        return
    
    # Создаем embed с результатами
    embed = discord.Embed(
        title=f"🔍 OAuth2 данные {user.display_name}",
        description=f"**Последнее обновление:** {data['last_updated'].strftime('%d.%m.%Y %H:%M')}\n"
                   f"**Всего серверов:** {len(guilds_data)}",
        color=COLORS['info']
    )
    
    embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)
    
    # Сортируем серверы по названию
    sorted_guilds = sorted(guilds_data, key=lambda x: x.get('name', '').lower())
    
    # Показываем первые 20 серверов
    servers_text = []
    for guild in sorted_guilds[:20]:
        name = guild.get('name', 'Неизвестно')
        owner = "👑" if guild.get('owner', False) else ""
        servers_text.append(f"{owner} {name}")
    
    if len(sorted_guilds) > 20:
        servers_text.append(f"... и ещё {len(sorted_guilds) - 20} серверов")
    
    embed.add_field(
        name="📋 Список серверов",
        value="\n".join(servers_text) if servers_text else "Нет серверов",
        inline=False
    )
    
    embed.set_footer(text=f"ID: {user.id} | Для полного списка используйте !checkoauthfull")
    
    await safe_send(ctx, embed=embed)

@bot.command(name='checkoauthfull')
@is_mod()
@command_enabled()
async def check_oauth_full(ctx, user: discord.User):
    """
    Показать ПОЛНЫЙ список серверов пользователя (в файле, если много)
    
    Пример: !checkoauthfull @user
    """
    data = await db.get_oauth_data(user.id)
    
    if not data:
        embed = discord.Embed(
            title="❌ Данные не найдены",
            description=f"Пользователь {user.mention} еще не авторизовался",
            color=COLORS['error']
        )
        await safe_send(ctx, embed=embed)
        return
    
    try:
        guilds_data = ast.literal_eval(data['guilds_data'])
    except:
        guilds_data = []
    
    if not guilds_data:
        await safe_send(ctx, "ℹ️ Нет данных о серверах")
        return
    
    # Сортируем серверы по названию
    sorted_guilds = sorted(guilds_data, key=lambda x: x.get('name', '').lower())
    
    # Если серверов меньше 50, показываем в embed
    if len(sorted_guilds) <= 50:
        embed = discord.Embed(
            title=f"📋 Все серверы {user.display_name}",
            description=f"Всего: **{len(sorted_guilds)}**",
            color=COLORS['info']
        )
        
        servers_text = []
        for guild in sorted_guilds:
            name = guild.get('name', 'Неизвестно')
            owner = "👑" if guild.get('owner', False) else ""
            servers_text.append(f"{owner} {name}")
        
        # Разбиваем на несколько полей если нужно
        chunk_size = 20
        for i in range(0, len(servers_text), chunk_size):
            chunk = servers_text[i:i+chunk_size]
            embed.add_field(
                name=f"Серверы {i+1}-{i+len(chunk)}",
                value="\n".join(chunk),
                inline=False
            )
        
        await safe_send(ctx, embed=embed)
        return
    
    # Если серверов больше 50, отправляем файлом
    filename = f"servers_{user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"Список серверов пользователя {user.display_name} (ID: {user.id})\n")
        f.write(f"Всего серверов: {len(sorted_guilds)}\n")
        f.write("=" * 50 + "\n\n")
        
        for i, guild in enumerate(sorted_guilds, 1):
            name = guild.get('name', 'Неизвестно')
            guild_id = guild.get('id', '?')
            owner = "👑 ВЛАДЕЛЕЦ" if guild.get('owner', False) else "участник"
            f.write(f"{i}. {name}\n")
            f.write(f"   ID: {guild_id} | {owner}\n\n")
    
    file = discord.File(filename)
    embed = discord.Embed(
        title=f"📁 Полный список серверов {user.display_name}",
        description=f"Всего серверов: **{len(sorted_guilds)}**\nФайл отправлен ниже",
        color=COLORS['success']
    )
    await safe_send(ctx, embed=embed, file=file)
    
    # Удаляем временный файл
    os.remove(filename)

# ========== КОМАНДА PING ==========

@bot.command(name='ping')
@command_enabled()
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

# ========== КОМАНДА HELP ==========

@bot.command(name='help')
async def help_command(ctx):
    """Показать все команды"""
    # Проверяем, является ли пользователь модератором
    is_user_mod = False
    try:
        if ctx.author.guild_permissions.administrator:
            is_user_mod = True
        else:
            author_role_ids = [role.id for role in ctx.author.roles]
            mod_role_ids = await db.get_mod_role_ids(ctx.guild.id)
            if any(mod_role_id in author_role_ids for mod_role_id in mod_role_ids):
                is_user_mod = True
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
        f"`{PREFIX}ping` - Статус бота\n"
        f"`{PREFIX}allyclans` - Союзные кланы\n"
        f"`{PREFIX}enemyclans` - Вражеские кланы\n"
        f"`{PREFIX}peaceclans` - Нейтральные кланы\n"
        f"`{PREFIX}allclans` - Все кланы\n"
        f"`{PREFIX}claninfo` - Инфо о клане\n"
        f"`{PREFIX}searchclan` - Поиск клана\n"
        f"`{PREFIX}vouchinfo` - Инфо о голосовании\n"
        f"`{PREFIX}oauth` - Ссылка для проверки серверов"
    )
    embed.add_field(name="👤 Для всех", value=user_commands, inline=False)
    
    if is_user_mod:
        # Поинты (модератор)
        points_commands = (
            f"`{PREFIX}addpoints @user кол-во [причина]` - Выдать поинты\n"
            f"`{PREFIX}removepoints @user кол-во [причина]` - Забрать поинты\n"
            f"`{PREFIX}setpoints @user кол-во [причина]` - Установить поинты\n"
            f"`{PREFIX}resetpoints` - Сброс ВСЕХ поинтов\n"
            f"`{PREFIX}export` - Экспорт в CSV"
        )
        embed.add_field(name="💰 Поинты (модератор)", value=points_commands, inline=False)
        
        # Роли за поинты (модератор)
        role_commands = (
            f"`{PREFIX}addrole кол-во \"название\"` - Добавить роль\n"
            f"`{PREFIX}removerole кол-во` - Удалить роль\n"
            f"`{PREFIX}editrole старое_кол-во новое_кол-во [название]` - Изменить\n"
            f"`{PREFIX}setrolecolor кол-во цвет` - Установить цвет\n"
            f"`{PREFIX}reorderroles` - Пересоздать роли\n"
            f"`{PREFIX}updateroles` - Обновить роли у всех\n"
            f"`{PREFIX}reloadroles` - Загрузить из БД"
        )
        embed.add_field(name="🎭 Роли за поинты (модератор)", value=role_commands, inline=False)
        
        # Блокировка каналов
        lock_commands = (
            f"`{PREFIX}addchannel #канал` - Добавить канал в список\n"
            f"`{PREFIX}removechannel [#канал]` - Удалить канал\n"
            f"`{PREFIX}listchannels` - Список каналов\n"
            f"`{PREFIX}lockrole @роль` - Установить роль для блокировки\n"
            f"`{PREFIX}lock [тип]` - Заблокировать каналы\n"
            f"`{PREFIX}unlock` - Разблокировать каналы\n"
            f"`{PREFIX}currentrole` - Текущая роль\n"
            f"`{PREFIX}resetrole` - Сбросить роль\n"
            f"`{PREFIX}lockinfo` - Инфо о блокировках\n"
            f"`{PREFIX}clearlocks` - Очистить все блокировки"
        )
        embed.add_field(name="🔒 Блокировка каналов", value=lock_commands, inline=False)
        
        # Управление кланами
        clan_commands = (
            f"`{PREFIX}addclan ally \"название\" [тег] [опис]` - Добавить союзника\n"
            f"`{PREFIX}addclan enemy \"название\" [тег] [опис]` - Добавить врага\n"
            f"`{PREFIX}addclan peace \"название\" [тег] [опис]` - Добавить нейтрального\n"
            f"`{PREFIX}removeclan [тип] \"название\"` - Удалить клан\n"
            f"`{PREFIX}editclan тип \"название\" поле значение` - Изменить\n"
            f"`{PREFIX}setclangif тип ссылка` - Установить гифку\n"
            f"`{PREFIX}clearclans` - Удалить ВСЕ кланы"
        )
        embed.add_field(name="🏰 Управление кланами", value=clan_commands, inline=False)
        
        # Голосование
        vouch_commands = (
            f"`{PREFIX}vouch @user @роль` - Создать голосование\n"
            f"`{PREFIX}endvouch` - Завершить голосование"
        )
        embed.add_field(name="🗳️ Голосование", value=vouch_commands, inline=False)
        
        # Верификация
        verify_commands = (
            f"`{PREFIX}verifyrole @verify @unverify` - Установить роли\n"
            f"`{PREFIX}verify @user` - Верифицировать\n"
            f"`{PREFIX}verifyinfo` - Инфо о настройках\n"
            f"`{PREFIX}clearverifyroles` - Сбросить настройки"
        )
        embed.add_field(name="🔐 Верификация", value=verify_commands, inline=False)
        
        # OAuth2
        oauth_commands = (
            f"`{PREFIX}checkoauth @user` - Проверить данные\n"
            f"`{PREFIX}checkoauthfull @user` - Полный список (файл)"
        )
        embed.add_field(name="🔍 OAuth2 (модератор)", value=oauth_commands, inline=False)
        
        # Веб-панель
        embed.add_field(
            name="🌐 Веб-панель управления",
            value=f"Перейдите по ссылке для настройки бота через веб-интерфейс:\n"
                  f"http://0.0.0.0:{PORT}/admin",
            inline=False
        )
    
    # Информация о системе
    role_settings, _ = await db.load_role_settings(ctx.guild.id)
    roles_count = len(role_settings)
    
    # Получаем модераторские роли
    mod_role_ids = await db.get_mod_role_ids(ctx.guild.id)
    mod_roles_text = []
    for role_id in mod_role_ids[:3]:
        role = ctx.guild.get_role(role_id)
        if role:
            mod_roles_text.append(f"• {role.mention}")
    if len(mod_role_ids) > 3:
        mod_roles_text.append(f"*... и ещё {len(mod_role_ids)-3}*")
    
    embed.add_field(
        name="ℹ️ Система",
        value=f"**Серверов:** {len(bot.guilds)} | **Ролей за поинты:** {roles_count}\n"
              + ("\n".join(mod_roles_text) if mod_roles_text else "• Модераторские роли не настроены"),
        inline=False
    )
    
    embed.set_footer(
        text=f"Запрошено: {ctx.author.display_name} | {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None
    )
    
    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    
    await safe_send(ctx, embed=embed)

# ========== ЗАПУСК БОТА ==========

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК DISCORD POINTS BOT")
    logger.info("=" * 50)
    logger.info(f"🤖 Префикс команд: {PREFIX}")
    logger.info(f"👑 Глобальные админские роли: {ADMIN_ROLE_IDS}")
    logger.info(f"🌐 Порт веб-сервера: {PORT}")
    logger.info(f"🗄️  База данных: PostgreSQL")
    logger.info("🔄 Режим: 24/7 с веб-сервером и панелью управления")
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
