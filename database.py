import asyncpg
import os
from typing import Dict, List, Optional, Tuple
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class Database:
    """Класс для работы с PostgreSQL базой данных"""
    
    def __init__(self):
        self.pool = None
    
    async def connect(self):
        """Подключение к базе данных"""
        try:
            # Получаем URL базы данных
            database_url = os.getenv('DATABASE_URL')
            
            if not database_url:
                logger.error("❌ DATABASE_URL не найден в переменных окружения!")
                return False
            
            # Исправляем URL для asyncpg (postgresql:// вместо postgres://)
            if database_url.startswith('postgres://'):
                database_url = database_url.replace('postgres://', 'postgresql://', 1)
            
            logger.info(f"🔗 Подключаемся к базе данных...")
            
            # Создаем пул подключений
            self.pool = await asyncpg.create_pool(
                dsn=database_url,
                min_size=1,
                max_size=10,
                command_timeout=60
            )
            
            # Проверяем подключение
            async with self.pool.acquire() as conn:
                await conn.execute('SELECT 1')
            
            # Создаем таблицы если их нет
            await self.create_tables()
            
            logger.info("✅ База данных подключена успешно!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к базе данных: {e}")
            return False
    
    async def create_tables(self):
        """Создание таблиц в базе данных"""
        async with self.pool.acquire() as conn:
            # Таблица пользователей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    points INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, guild_id)
                )
            ''')
            
            # Таблица транзакций (история)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT,
                    admin_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица ролей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS role_settings (
                    guild_id BIGINT NOT NULL,
                    points_required INTEGER NOT NULL,
                    role_name TEXT NOT NULL,
                    role_color TEXT DEFAULT '#3498db',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, points_required)
                )
            ''')
            
            # Таблица каналов уведомлений
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS notification_channels (
                    guild_id BIGINT PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Создаем стандартные роли
            await self.create_default_roles()
            
            logger.info("✅ Таблицы созданы/проверены")
    
    async def create_default_roles(self):
        """Создание стандартных ролей"""
        default_roles = [
            (50, 'raider newgen', '#2ecc71'),
            (100, 'raider scout', '#3498db'),
            (150, 'raider striker', '#e67e22'),
            (350, 'raider legend', '#9b59b6'),
            (500, 'raider commander', '#f1c40f')
        ]
        
        # Эта функция будет заполняться позже, когда будем добавлять сервера
    
    # ========== МЕТОДЫ ДЛЯ РАБОТЫ С ПОИНТАМИ ==========
    
    async def get_user_points(self, user_id: int, guild_id: int) -> int:
        """Получить количество поинтов пользователя"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT points FROM users WHERE user_id = $1 AND guild_id = $2',
                user_id, guild_id
            )
            return result['points'] if result else 0
    
    async def add_points(self, user_id: int, guild_id: int, amount: int, 
                         admin_id: Optional[int] = None, reason: str = "Выдано админом") -> int:
        """Добавить поинты пользователю"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Добавляем или обновляем пользователя
                await conn.execute('''
                    INSERT INTO users (user_id, guild_id, points)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, guild_id)
                    DO UPDATE SET 
                        points = users.points + EXCLUDED.points,
                        updated_at = CURRENT_TIMESTAMP
                ''', user_id, guild_id, amount)
                
                # Записываем транзакцию
                if admin_id:
                    await conn.execute('''
                        INSERT INTO transactions (user_id, guild_id, amount, reason, admin_id)
                        VALUES ($1, $2, $3, $4, $5)
                    ''', user_id, guild_id, amount, reason, admin_id)
                
                # Получаем новое значение
                result = await conn.fetchrow(
                    'SELECT points FROM users WHERE user_id = $1 AND guild_id = $2',
                    user_id, guild_id
                )
                return result['points']
    
    async def remove_points(self, user_id: int, guild_id: int, amount: int,
                           admin_id: Optional[int] = None, reason: str = "Изъято админом") -> int:
        """Удалить поинты у пользователя"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Получаем текущие поинты
                current_points = await self.get_user_points(user_id, guild_id)
                amount_to_remove = min(amount, current_points)
                
                if amount_to_remove > 0:
                    # Обновляем поинты
                    await conn.execute('''
                        UPDATE users 
                        SET points = GREATEST(0, points - $1),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = $2 AND guild_id = $3
                    ''', amount_to_remove, user_id, guild_id)
                    
                    # Записываем транзакцию
                    if admin_id:
                        await conn.execute('''
                            INSERT INTO transactions (user_id, guild_id, amount, reason, admin_id)
                            VALUES ($1, $2, $3, $4, $5)
                        ''', user_id, guild_id, -amount_to_remove, reason, admin_id)
                
                # Получаем новое значение
                result = await conn.fetchrow(
                    'SELECT points FROM users WHERE user_id = $1 AND guild_id = $2',
                    user_id, guild_id
                )
                return result['points'] if result else 0
    
    async def set_points(self, user_id: int, guild_id: int, amount: int,
                        admin_id: Optional[int] = None, reason: str = "Установлено админом") -> int:
        """Установить точное количество поинтов"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Устанавливаем поинты
                await conn.execute('''
                    INSERT INTO users (user_id, guild_id, points)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, guild_id)
                    DO UPDATE SET 
                        points = EXCLUDED.points,
                        updated_at = CURRENT_TIMESTAMP
                ''', user_id, guild_id, max(0, amount))
                
                # Если указан админ, записываем транзакцию
                if admin_id:
                    # Получаем разницу
                    old_points = await self.get_user_points(user_id, guild_id)
                    difference = amount - old_points
                    
                    if difference != 0:
                        await conn.execute('''
                            INSERT INTO transactions (user_id, guild_id, amount, reason, admin_id)
                            VALUES ($1, $2, $3, $4, $5)
                        ''', user_id, guild_id, difference, reason, admin_id)
                
                return amount
    
    # ========== МЕТОДЫ ДЛЯ РОЛЕЙ ==========
    
    async def get_role_settings(self, guild_id: int) -> Dict[int, Dict]:
        """Получить настройки ролей для сервера"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(
                'SELECT points_required, role_name, role_color FROM role_settings WHERE guild_id = $1 ORDER BY points_required',
                guild_id
            )
            
            roles = {}
            for row in results:
                roles[row['points_required']] = {
                    'name': row['role_name'],
                    'color': row['role_color']
                }
            return roles
    
    async def set_role_setting(self, guild_id: int, points_required: int, 
                              role_name: str, role_color: str = '#3498db'):
        """Установить настройку роли"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO role_settings (guild_id, points_required, role_name, role_color)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id, points_required)
                DO UPDATE SET role_name = EXCLUDED.role_name, role_color = EXCLUDED.role_color
            ''', guild_id, points_required, role_name, role_color)
    
    async def init_default_roles(self, guild_id: int):
        """Инициализировать стандартные роли для сервера"""
        default_roles = [
            (50, 'raider newgen', '#2ecc71'),
            (100, 'raider scout', '#3498db'),
            (150, 'raider striker', '#e67e22'),
            (350, 'raider legend', '#9b59b6'),
            (500, 'raider commander', '#f1c40f')
        ]
        
        current_roles = await self.get_role_settings(guild_id)
        
        for points, name, color in default_roles:
            if points not in current_roles:
                await self.set_role_setting(guild_id, points, name, color)
    
    # ========== МЕТОДЫ ДЛЯ УВЕДОМЛЕНИЙ ==========
    
    async def get_notification_channel(self, guild_id: int) -> Optional[int]:
        """Получить ID канала для уведомлений"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT channel_id FROM notification_channels WHERE guild_id = $1',
                guild_id
            )
            return result['channel_id'] if result else None
    
    async def set_notification_channel(self, guild_id: int, channel_id: int):
        """Установить канал для уведомлений"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO notification_channels (guild_id, channel_id)
                VALUES ($1, $2)
                ON CONFLICT (guild_id)
                DO UPDATE SET channel_id = EXCLUDED.channel_id
            ''', guild_id, channel_id)
    
    async def remove_notification_channel(self, guild_id: int):
        """Удалить канал для уведомлений"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                'DELETE FROM notification_channels WHERE guild_id = $1',
                guild_id
            )
    
    # ========== МЕТОДЫ ДЛЯ ЛИДЕРБОРДА ==========
    
    async def get_leaderboard(self, guild_id: int, limit: int = 10) -> List[Tuple[int, int]]:
        """Получить таблицу лидеров"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(
                '''
                SELECT user_id, points 
                FROM users 
                WHERE guild_id = $1 
                ORDER BY points DESC 
                LIMIT $2
                ''',
                guild_id, limit
            )
            
            return [(row['user_id'], row['points']) for row in results]
    
    async def get_user_position(self, user_id: int, guild_id: int) -> int:
        """Получить позицию пользователя в рейтинге"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchval('''
                SELECT COUNT(*) + 1
                FROM users
                WHERE guild_id = $1 AND points > (
                    SELECT COALESCE(points, 0) 
                    FROM users 
                    WHERE user_id = $2 AND guild_id = $1
                )
            ''', guild_id, user_id)
            
            return result or 1
    
    # ========== ДОПОЛНИТЕЛЬНЫЕ МЕТОДЫ ==========
    
    async def get_user_transactions(self, user_id: int, guild_id: int, limit: int = 10) -> List[Dict]:
        """Получить историю транзакций пользователя"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(
                '''
                SELECT amount, reason, admin_id, created_at
                FROM transactions
                WHERE user_id = $1 AND guild_id = $2
                ORDER BY created_at DESC
                LIMIT $3
                ''',
                user_id, guild_id, limit
            )
            
            transactions = []
            for row in results:
                transactions.append({
                    'amount': row['amount'],
                    'reason': row['reason'],
                    'admin_id': row['admin_id'],
                    'created_at': row['created_at']
                })
            
            return transactions
    
    async def get_guild_stats(self, guild_id: int) -> Dict:
        """Получить статистику сервера"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow('''
                SELECT 
                    COUNT(*) as total_users,
                    COALESCE(SUM(points), 0) as total_points,
                    COALESCE(AVG(points), 0) as avg_points,
                    COALESCE(MAX(points), 0) as max_points
                FROM users
                WHERE guild_id = $1
            ''', guild_id)
            
            return {
                'total_users': result['total_users'] or 0,
                'total_points': result['total_points'] or 0,
                'avg_points': float(result['avg_points'] or 0),
                'max_points': result['max_points'] or 0
            }
    
    async def reset_guild_points(self, guild_id: int):
        """Сбросить все поинты на сервере"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Удаляем пользователей
                await conn.execute('DELETE FROM users WHERE guild_id = $1', guild_id)
                # Удаляем транзакции
                await conn.execute('DELETE FROM transactions WHERE guild_id = $1', guild_id)
    
    async def close(self):
        """Закрыть соединение с базой данных"""
        if self.pool:
            await self.pool.close()

# Создаем глобальный экземпляр базы данных
db = Database()
