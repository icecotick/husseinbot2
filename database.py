import os
import asyncpg
from typing import Dict, List, Optional, Tuple
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None
    
    async def connect(self):
        """Подключение к базе данных"""
        database_url = os.getenv('DATABASE_URL')
        
        if not database_url:
            # Для локального тестирования
            database_url = "postgresql://postgres:password@localhost/pointsdb"
        
        # Заменяем postgres:// на postgresql:// для asyncpg
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        
        self.pool = await asyncpg.create_pool(
            database_url,
            min_size=1,
            max_size=10,
            command_timeout=60
        )
        
        await self.init_db()
        logger.info("✅ База данных подключена")
    
    async def init_db(self):
        """Инициализация таблиц"""
        async with self.pool.acquire() as conn:
            # Таблица пользователей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    guild_id BIGINT,
                    points INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Таблица транзакций (история)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    guild_id BIGINT,
                    amount INTEGER,
                    reason TEXT,
                    admin_id BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Таблица настроек ролей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS role_settings (
                    guild_id BIGINT,
                    points_required INTEGER,
                    role_name TEXT,
                    role_color TEXT DEFAULT '#3498db',
                    PRIMARY KEY (guild_id, points_required)
                )
            ''')
            
            # Таблица выданных ролей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id BIGINT,
                    guild_id BIGINT,
                    role_name TEXT,
                    assigned_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (user_id, guild_id, role_name)
                )
            ''')
            
            # Таблица лидерборда (кеш)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS leaderboard_cache (
                    guild_id BIGINT PRIMARY KEY,
                    data JSONB,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Создаем индексы для быстрого поиска
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_guild ON users(guild_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_transactions_guild ON transactions(guild_id)')
    
    async def get_user_points(self, user_id: int, guild_id: int) -> int:
        """Получить поинты пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT points FROM users WHERE user_id = $1 AND guild_id = $2',
                user_id, guild_id
            )
            return row['points'] if row else 0
    
    async def add_points(self, user_id: int, guild_id: int, amount: int, 
                        admin_id: int = None, reason: str = "Выдано админом") -> Tuple[int, int]:
        """Добавить поинты пользователю"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Добавляем или обновляем пользователя
                await conn.execute('''
                    INSERT INTO users (user_id, guild_id, points, updated_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (user_id, guild_id) 
                    DO UPDATE SET points = users.points + EXCLUDED.points, updated_at = NOW()
                    RETURNING points
                ''', user_id, guild_id, amount)
                
                # Получаем новое значение
                row = await conn.fetchrow(
                    'SELECT points FROM users WHERE user_id = $1 AND guild_id = $2',
                    user_id, guild_id
                )
                new_total = row['points']
                
                # Записываем транзакцию
                await conn.execute('''
                    INSERT INTO transactions (user_id, guild_id, amount, reason, admin_id)
                    VALUES ($1, $2, $3, $4, $5)
                ''', user_id, guild_id, amount, reason, admin_id)
                
                return amount, new_total
    
    async def remove_points(self, user_id: int, guild_id: int, amount: int,
                           admin_id: int = None, reason: str = "Изъято админом") -> Tuple[int, int]:
        """Удалить поинты у пользователя"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Получаем текущие поинты
                current = await self.get_user_points(user_id, guild_id)
                actual_amount = min(amount, current)
                
                if actual_amount > 0:
                    # Обновляем поинты
                    await conn.execute('''
                        UPDATE users 
                        SET points = GREATEST(0, points - $1), updated_at = NOW()
                        WHERE user_id = $2 AND guild_id = $3
                    ''', actual_amount, user_id, guild_id)
                    
                    # Записываем транзакцию
                    await conn.execute('''
                        INSERT INTO transactions (user_id, guild_id, amount, reason, admin_id)
                        VALUES ($1, $2, $3, $4, $5)
                    ''', user_id, guild_id, -actual_amount, reason, admin_id)
                
                # Получаем новое значение
                new_total = await self.get_user_points(user_id, guild_id)
                return actual_amount, new_total
    
    async def set_points(self, user_id: int, guild_id: int, amount: int,
                        admin_id: int = None, reason: str = "Установлено админом") -> int:
        """Установить точное количество поинтов"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Обновляем или создаем запись
                await conn.execute('''
                    INSERT INTO users (user_id, guild_id, points, updated_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (user_id, guild_id) 
                    DO UPDATE SET points = EXCLUDED.points, updated_at = NOW()
                ''', user_id, guild_id, max(0, amount))
                
                # Записываем транзакцию
                old_points = await self.get_user_points(user_id, guild_id)
                difference = amount - old_points
                
                if difference != 0:
                    await conn.execute('''
                        INSERT INTO transactions (user_id, guild_id, amount, reason, admin_id)
                        VALUES ($1, $2, $3, $4, $5)
                    ''', user_id, guild_id, difference, reason, admin_id)
                
                return amount
    
    async def get_leaderboard(self, guild_id: int, limit: int = 10) -> List[Tuple[int, str, int]]:
        """Получить таблицу лидеров"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT u.user_id, u.points
                FROM users u
                WHERE u.guild_id = $1
                ORDER BY u.points DESC
                LIMIT $2
            ''', guild_id, limit)
            
            return [(row['user_id'], row['points']) for row in rows]
    
    async def get_user_position(self, user_id: int, guild_id: int) -> int:
        """Получить позицию пользователя в рейтинге"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT COUNT(*) as position
                FROM users u2
                WHERE u2.guild_id = $1 
                AND u2.points > (
                    SELECT COALESCE(u1.points, 0)
                    FROM users u1
                    WHERE u1.user_id = $2 AND u1.guild_id = $1
                )
            ''', guild_id, user_id)
            
            return (row['position'] + 1) if row else 0
    
    async def set_role_setting(self, guild_id: int, points_required: int, role_name: str, role_color: str = "#3498db"):
        """Установить настройку роли"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO role_settings (guild_id, points_required, role_name, role_color)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id, points_required) 
                DO UPDATE SET role_name = EXCLUDED.role_name, role_color = EXCLUDED.role_color
            ''', guild_id, points_required, role_name, role_color)
    
    async def get_role_settings(self, guild_id: int) -> Dict[int, Dict[str, str]]:
        """Получить настройки ролей для сервера"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT points_required, role_name, role_color
                FROM role_settings
                WHERE guild_id = $1
                ORDER BY points_required
            ''', guild_id)
            
            return {
                row['points_required']: {
                    'name': row['role_name'],
                    'color': row['role_color']
                } for row in rows
            }
    
    async def delete_role_setting(self, guild_id: int, points_required: int):
        """Удалить настройку роли"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                DELETE FROM role_settings 
                WHERE guild_id = $1 AND points_required = $2
            ''', guild_id, points_required)
    
    async def assign_user_role(self, user_id: int, guild_id: int, role_name: str):
        """Назначить роль пользователю"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_roles (user_id, guild_id, role_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, guild_id, role_name) 
                DO UPDATE SET assigned_at = NOW()
            ''', user_id, guild_id, role_name)
    
    async def remove_user_role(self, user_id: int, guild_id: int, role_name: str):
        """Удалить роль у пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                DELETE FROM user_roles 
                WHERE user_id = $1 AND guild_id = $2 AND role_name = $3
            ''', user_id, guild_id, role_name)
    
    async def get_user_roles(self, user_id: int, guild_id: int) -> List[str]:
        """Получить роли пользователя"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT role_name FROM user_roles
                WHERE user_id = $1 AND guild_id = $2
            ''', user_id, guild_id)
            
            return [row['role_name'] for row in rows]
    
    async def get_user_transactions(self, user_id: int, guild_id: int, limit: int = 10) -> List[Dict]:
        """Получить историю транзакций пользователя"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT amount, reason, admin_id, created_at
                FROM transactions
                WHERE user_id = $1 AND guild_id = $2
                ORDER BY created_at DESC
                LIMIT $3
            ''', user_id, guild_id, limit)
            
            return [
                {
                    'amount': row['amount'],
                    'reason': row['reason'],
                    'admin_id': row['admin_id'],
                    'created_at': row['created_at']
                } for row in rows
            ]
    
    async def get_guild_stats(self, guild_id: int) -> Dict:
        """Получить статистику сервера"""
        async with self.pool.acquire() as conn:
            # Общая статистика
            stats_row = await conn.fetchrow('''
                SELECT 
                    COUNT(*) as total_users,
                    SUM(points) as total_points,
                    AVG(points) as avg_points,
                    MAX(points) as max_points
                FROM users
                WHERE guild_id = $1
            ''', guild_id)
            
            # Топ 3 пользователя
            top_users = await conn.fetch('''
                SELECT user_id, points
                FROM users
                WHERE guild_id = $1
                ORDER BY points DESC
                LIMIT 3
            ''', guild_id)
            
            return {
                'total_users': stats_row['total_users'] or 0,
                'total_points': stats_row['total_points'] or 0,
                'avg_points': float(stats_row['avg_points'] or 0),
                'max_points': stats_row['max_points'] or 0,
                'top_users': [(row['user_id'], row['points']) for row in top_users]
            }
    
    async def reset_guild_points(self, guild_id: int):
        """Сбросить все поинты на сервере"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Удаляем пользователей
                await conn.execute('DELETE FROM users WHERE guild_id = $1', guild_id)
                # Удаляем транзакции
                await conn.execute('DELETE FROM transactions WHERE guild_id = $1', guild_id)
                # Удаляем выданные роли
                await conn.execute('DELETE FROM user_roles WHERE guild_id = $1', guild_id)

# Глобальный экземпляр базы данных
db = Database()
