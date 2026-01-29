import discord
from discord.ext import commands, tasks
import os
import json
import asyncio
from datetime import datetime
from typing import Optional  # ⬅️ ДОБАВИЛИ ИМПОРТ
from dotenv import load_dotenv
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Настройки
TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('BOT_PREFIX', '!')
ADMIN_ROLES = [role.strip() for role in os.getenv('ADMIN_ROLES', 'The Owner,Co-Owner').split(',')]

# JSON файл для хранения данных
DATA_FILE = 'points_data.json'

# Настройки по умолчанию для ролей
DEFAULT_ROLES = {
    50: {'name': 'raider newgen', 'color': '#2ecc71'},
    100: {'name': 'raider scout', 'color': '#3498db'},
    150: {'name': 'raider striker', 'color': '#e67e22'},
    350: {'name': 'raider legend', 'color': '#9b59b6'},
    500: {'name': 'raider commander', 'color': '#f1c40f'}
}

# Настройки интентов
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(PREFIX),
    intents=intents,
    help_command=None
)

# Хранение данных
points_data = {}
role_settings = {}

# ========== УТИЛИТЫ ДЛЯ РАБОТЫ С ДАННЫМИ ==========

def load_data():
    """Загрузка данных из JSON файла"""
    global points_data, role_settings
    
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                points_data = data.get('points', {})
                role_settings = data.get('roles', DEFAULT_ROLES)
                logger.info(f"✅ Данные загружены: {len(points_data)} пользователей")
        else:
            points_data = {}
            role_settings = DEFAULT_ROLES.copy()
            save_data()
            logger.info("✅ Создан новый файл данных")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки данных: {e}")
        points_data = {}
        role_settings = DEFAULT_ROLES.copy()

def save_data():
    """Сохранение данных в JSON файл"""
    try:
        data = {
            'points': points_data,
            'roles': role_settings,
            'last_updated': datetime.now().isoformat()
        }
        
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"💾 Данные сохранены: {len(points_data)} пользователей")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения данных: {e}")

def get_user_key(user_id: int, guild_id: int) -> str:
    """Генерация ключа для пользователя"""
    return f"{guild_id}_{user_id}"

def get_user_points(user_id: int, guild_id: int) -> int:
    """Получение поинтов пользователя"""
    key = get_user_key(user_id, guild_id)
    return points_data.get(key, 0)

def add_user_points(user_id: int, guild_id: int, amount: int) -> int:
    """Добавление поинтов пользователю"""
    key = get_user_key(user_id, guild_id)
    current = points_data.get(key, 0)
    points_data[key] = current + amount
    save_data()
    return points_data[key]

def remove_user_points(user_id: int, guild_id: int, amount: int) -> int:
    """Удаление поинтов у пользователя"""
    key = get_user_key(user_id, guild_id)
    current = points_data.get(key, 0)
    new_points = max(0, current - amount)
    points_data[key] = new_points
    save_data()
    return new_points

def set_user_points(user_id: int, guild_id: int, amount: int) -> int:
    """Установка точного количества поинтов"""
    key = get_user_key(user_id, guild_id)
    points_data[key] = max(0, amount)
    save_data()
    return points_data[key]

# ========== ПИНГЕР ДЛЯ 24/7 ==========

@tasks.loop(minutes=5)
async def keep_alive_ping():
    """Пингер для поддержания активности бота 24/7"""
    try:
        # Просто логируем активность
        logger.info(f"🤖 Бот активен | Серверов: {len(bot.guilds)} | Пользователей в базе: {len(points_data)}")
        
        # Обновляем статус
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{PREFIX}help | {len(bot.guilds)} серв."
            )
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в пингере: {e}")

@tasks.loop(minutes=30)
async def auto_save_data():
    """Автосохранение данных"""
    save_data()
    logger.info("💾 Автосохранение данных выполнено")

@tasks.loop(hours=24)
async def daily_backup():
    """Ежедневное резервное копирование"""
    try:
        if os.path.exists(DATA_FILE):
            backup_file = f"backup_{datetime.now().strftime('%Y%m%d')}.json"
            with open(DATA_FILE, 'r', encoding='utf-8') as src:
                data = src.read()
            with open(backup_file, 'w', encoding='utf-8') as dst:
                dst.write(data)
            logger.info(f"📦 Создан бэкап: {backup_file}")
    except Exception as e:
        logger.error(f"❌ Ошибка создания бэкапа: {e}")

# ========== ПРОВЕРКА ПРАВ ==========

def is_admin():
    async def predicate(ctx):
        # Проверка прав администратора Discord
        if ctx.author.guild_permissions.administrator:
            return True
        
        # Проверка кастомных админских ролей
        author_roles = [role.name for role in ctx.author.roles]
        return any(admin_role in author_roles for admin_role in ADMIN_ROLES)
    
    return commands.check(predicate)

# ========== ВЫДАЧА РОЛЕЙ ==========

async def check_and_assign_roles(member: discord.Member):
    """Проверка и выдача ролей на основе поинтов"""
    try:
        guild_id = member.guild.id
        user_id = member.id
        
        # Получаем поинты пользователя
        points = get_user_points(user_id, guild_id)
        
        # Сортируем роли по количеству поинтов
        sorted_roles = sorted(role_settings.items(), key=lambda x: x[0])
        
        # Находим самую высокую доступную роль
        highest_role = None
        for points_required, role_info in sorted_roles:
            if points >= points_required:
                highest_role = role_info
        
        if not highest_role:
            return
        
        role_name = highest_role['name']
        
        # Находим или создаем роль на сервере
        discord_role = discord.utils.get(member.guild.roles, name=role_name)
        
        if not discord_role:
            # Создаем новую роль
            try:
                color = discord.Color.from_str(highest_role.get('color', '#3498db'))
                discord_role = await member.guild.create_role(
                    name=role_name,
                    color=color,
                    reason="Автоматическое создание роли за поинты"
                )
                
                # Пытаемся переместить роль выше обычных ролей
                try:
                    positions = {}
                    for role in member.guild.roles:
                        if role.name in ADMIN_ROLES or role.name == '@everyone':
                            continue
                        positions[role] = role.position
                    
                    if positions:
                        max_position = max(positions.values())
                        await discord_role.edit(position=max_position + 1)
                except:
                    pass
                    
                logger.info(f"Создана новая роль: {role_name} на сервере {member.guild.name}")
                
            except discord.Forbidden:
                logger.error(f'Недостаточно прав для создания роли {role_name}')
                return
            except Exception as e:
                logger.error(f'Ошибка создания роли: {e}')
                return
        
        # Удаляем старые роли за поинты (только более низкие)
        for points_required, role_info in sorted_roles:
            if role_info['name'] != role_name:
                old_role = discord.utils.get(member.guild.roles, name=role_info['name'])
                if old_role and old_role in member.roles:
                    try:
                        await member.remove_roles(old_role)
                    except:
                        pass
        
        # Добавляем новую роль
        if discord_role and discord_role not in member.roles:
            try:
                await member.add_roles(discord_role)
                logger.info(f'Выдана роль {role_name} пользователю {member.display_name} ({points} поинтов)')
            except discord.Forbidden:
                logger.error(f'Недостаточно прав для выдачи роли {role_name}')
            except Exception as e:
                logger.error(f'Ошибка выдачи роли: {e}')
                
    except Exception as e:
        logger.error(f'Ошибка в check_and_assign_roles: {e}')

# ========== СОБЫТИЯ БОТА ==========

@bot.event
async def on_ready():
    """Событие при запуске бота"""
    logger.info(f'✅ Бот {bot.user} запущен!')
    logger.info(f'📊 Серверов: {len(bot.guilds)}')
    
    # Загружаем данные
    load_data()
    
    # Запускаем пингер для 24/7
    keep_alive_ping.start()
    auto_save_data.start()
    daily_backup.start()
    
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

# ========== КОМАНДЫ ДЛЯ АДМИНОВ ==========

@bot.hybrid_command(name='addpoints', description='Выдать поинты пользователю')
@is_admin()
async def add_points(ctx, member: discord.Member, amount: int, reason: str = "Выдано админом"):
    """Выдать поинты пользователю"""
    if amount <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    new_total = add_user_points(member.id, ctx.guild.id, amount)
    
    embed = discord.Embed(
        title="✅ Поинты выданы!",
        color=discord.Color.green()
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

@bot.hybrid_command(name='removepoints', description='Забрать поинты у пользователя')
@is_admin()
async def remove_points(ctx, member: discord.Member, amount: int, reason: str = "Изъято админом"):
    """Забрать поинты у пользователя"""
    if amount <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    new_total = remove_user_points(member.id, ctx.guild.id, amount)
    
    embed = discord.Embed(
        title="✅ Поинты изъяты!",
        color=discord.Color.green()
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

@bot.hybrid_command(name='setpoints', description='Установить точное количество поинтов')
@is_admin()
async def set_points(ctx, member: discord.Member, amount: int, reason: str = "Установлено админом"):
    """Установить точное количество поинтов"""
    if amount < 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов не может быть отрицательным!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    new_total = set_user_points(member.id, ctx.guild.id, amount)
    
    embed = discord.Embed(
        title="✅ Поинты установлены!",
        color=discord.Color.green()
    )
    embed.add_field(name="Пользователь", value=member.mention, inline=True)
    embed.add_field(name="Новое значение", value=f"{new_total} поинтов", inline=True)
    embed.add_field(name="Причина", value=reason, inline=False)
    embed.add_field(name="Установил", value=ctx.author.mention, inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    
    await ctx.send(embed=embed)
    
    # Проверяем и выдаем роли
    await check_and_assign_roles(member)

@bot.hybrid_command(name='setrole', description='Установить роль за определенное количество поинтов')
@is_admin()
async def set_role(ctx, points: int, role_name: str, color: str = "#3498db"):
    """Установить роль за определенное количество поинтов"""
    if points <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    try:
        # Проверяем валидность цвета
        discord.Color.from_str(color)
    except:
        color = "#3498db"
    
    # Сохраняем настройку роли
    role_settings[points] = {'name': role_name, 'color': color}
    save_data()
    
    embed = discord.Embed(
        title="✅ Роль установлена!",
        description=f"Роль **{role_name}** будет выдаваться за **{points}** поинтов",
        color=discord.Color.green()
    )
    embed.add_field(name="Цвет роли", value=color, inline=True)
    embed.set_footer(text=f"ID сервера: {ctx.guild.id}")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='resetpoints', description='Сбросить все поинты на сервере')
@is_admin()
async def reset_points(ctx):
    """Сбросить все поинты на сервере"""
    embed = discord.Embed(
        title="⚠️ ОПАСНОЕ ДЕЙСТВИЕ",
        description="Вы уверены, что хотите сбросить ВСЕ поинты на сервере?\nЭто действие необратимо!",
        color=discord.Color.red()
    )
    embed.add_field(name="Что будет сброшено:", 
                   value="• Все поинты пользователей\n• Вся история транзакций", 
                   inline=False)
    
    view = discord.ui.View(timeout=30)
    
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может подтвердить!", ephemeral=True)
            return
        
        # Удаляем все записи для этого сервера
        guild_id = str(ctx.guild.id)
        keys_to_remove = [key for key in points_data.keys() if key.startswith(guild_id + '_')]
        
        for key in keys_to_remove:
            del points_data[key]
        
        save_data()
        
        confirm_embed = discord.Embed(
            title="✅ Все поинты сброшены!",
            description=f"Удалено {len(keys_to_remove)} записей пользователей.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=confirm_embed, view=None)
    
    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("❌ Только автор команды может отменить!", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="❌ Сброс отменен",
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)
    
    confirm_button = discord.ui.Button(label="✅ Подтвердить", style=discord.ButtonStyle.danger)
    cancel_button = discord.ui.Button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    
    confirm_button.callback = confirm_callback
    cancel_button.callback = cancel_callback
    
    view.add_item(confirm_button)
    view.add_item(cancel_button)
    
    await ctx.send(embed=embed, view=view)

# ========== КОМАНДЫ ДЛЯ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ ==========

@bot.hybrid_command(name='points', description='Проверить свои поинты или поинты другого пользователя')
async def check_points(ctx, member: Optional[discord.Member] = None):
    """Проверить поинты"""
    if member is None:
        member = ctx.author
    
    user_id = member.id
    guild_id = ctx.guild.id
    
    # Получаем данные
    points = get_user_points(user_id, guild_id)
    
    # Создаем embed
    embed = discord.Embed(
        title=f"🏆 Поинты {member.display_name}",
        color=discord.Color.gold()
    )
    
    # Основная информация
    embed.add_field(name="Баланс", value=f"**{points}** поинтов", inline=True)
    
    # Система ролей
    if role_settings:
        roles_text = []
        sorted_roles = sorted(role_settings.items(), key=lambda x: x[0])
        
        current_role = "Нет роли"
        for points_required, role_info in sorted(sorted_roles, key=lambda x: x[0], reverse=True):
            if points >= points_required:
                current_role = role_info['name']
                break
        
        if current_role != "Нет роли":
            embed.add_field(name="Текущая роль", value=f"**{current_role}**", inline=True)
        
        # Показываем прогресс
        for points_required, role_info in sorted_roles:
            status = "✅" if points >= points_required else "⏳"
            roles_text.append(f"{status} **{role_info['name']}** - {points_required} поинтов")
        
        embed.add_field(
            name="🏅 Система ролей",
            value="\n".join(roles_text),
            inline=False
        )
        
        # Следующая роль
        next_role = None
        points_needed = 0
        for points_required, role_info in sorted_roles:
            if points < points_required:
                next_role = role_info['name']
                points_needed = points_required - points
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

@bot.hybrid_command(name='leaderboard', description='Таблица лидеров по поинтам')
async def leaderboard(ctx, page: int = 1):
    """Таблица лидеров"""
    guild_id = str(ctx.guild.id)
    
    # Фильтруем пользователей только этого сервера
    server_users = {}
    for key, points in points_data.items():
        if key.startswith(guild_id + '_'):
            user_id = key.split('_')[1]
            server_users[user_id] = points
    
    if not server_users:
        embed = discord.Embed(
            title="📊 Таблица лидеров",
            description="Пока никто не имеет поинтов!",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        return
    
    # Сортируем по убыванию
    sorted_users = sorted(server_users.items(), key=lambda x: x[1], reverse=True)
    
    # Пагинация
    limit = 10
    total_pages = (len(sorted_users) + limit - 1) // limit
    
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    page_data = sorted_users[start_idx:end_idx]
    
    # Создаем embed
    embed = discord.Embed(
        title="🏆 Таблица лидеров",
        color=discord.Color.gold()
    )
    
    # Добавляем записи
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, (user_id, user_points) in enumerate(page_data, start=1):
        try:
            member = await ctx.guild.fetch_member(int(user_id))
            username = member.display_name
        except:
            username = f"Пользователь ({user_id})"
        
        medal = medals[i-1] if i <= len(medals) else f"{i+start_idx}."
        
        # Определяем роль
        user_role = "Нет роли"
        if role_settings:
            sorted_roles = sorted(role_settings.items(), key=lambda x: x[0], reverse=True)
            for points_required, role_info in sorted_roles:
                if user_points >= points_required:
                    user_role = role_info['name']
                    break
        
        embed.add_field(
            name=f"{medal} {username}",
            value=f"**{user_points}** поинтов | 🏅 {user_role}",
            inline=False
        )
    
    # Статистика
    total_points = sum(server_users.values())
    avg_points = total_points / len(server_users) if server_users else 0
    max_points = max(server_users.values()) if server_users else 0
    
    embed.add_field(
        name="📊 Статистика сервера",
        value=f"• Всего пользователей: **{len(server_users)}**\n"
              f"• Всего поинтов: **{total_points}**\n"
              f"• Среднее: **{avg_points:.1f}**\n"
              f"• Максимум: **{max_points}**",
        inline=False
    )
    
    # Пагинация
    if total_pages > 1:
        embed.set_footer(text=f"Страница {page}/{total_pages} | Всего участников: {len(server_users)}")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='roles', description='Показать систему ролей')
async def show_roles(ctx):
    """Показать систему ролей"""
    if not role_settings:
        embed = discord.Embed(
            title="🏅 Система ролей",
            description="Система ролей не настроена.\nАдмины могут настроить с помощью `/setrole`",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="🏅 Система ролей",
        description="Роли выдаются автоматически при достижении определенного количества поинтов",
        color=discord.Color.gold()
    )
    
    sorted_roles = sorted(role_settings.items(), key=lambda x: x[0])
    
    for points_required, role_info in sorted_roles:
        color = role_info.get('color', '#3498db')
        color_block = f"`{color}`"
        
        embed.add_field(
            name=f"🎖️ {role_info['name']}",
            value=f"**{points_required}** поинтов\nЦвет: {color_block}",
            inline=True
        )
    
    embed.set_footer(text="Админские роли: " + ", ".join(ADMIN_ROLES))
    await ctx.send(embed=embed)

@bot.hybrid_command(name='ping', description='Проверить пинг бота')
async def ping_command(ctx):
    """Проверить пинг бота"""
    latency = round(bot.latency * 1000)
    
    embed = discord.Embed(
        title="🏓 Понг!",
        color=discord.Color.green()
    )
    embed.add_field(name="Задержка API", value=f"**{latency}мс**", inline=True)
    embed.add_field(name="Серверов", value=f"**{len(bot.guilds)}**", inline=True)
    embed.add_field(name="Пользователей в базе", value=f"**{len(points_data)}**", inline=True)
    embed.add_field(name="Статус", value="✅ **24/7 Активен**", inline=False)
    embed.set_footer(text="Бот автоматически сохраняет данные каждые 30 минут")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='help', description='Показать все команды')
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
        color=discord.Color.blue()
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
            name="👑 Команды для админов",
            value="• `/addpoints @пользователь количество [причина]` - Выдать поинты\n"
                  "• `/removepoints @пользователь количество [причина]` - Забрать поинты\n"
                  "• `/setpoints @пользователь количество [причина]` - Установить поинты\n"
                  "• `/setrole количество \"название роли\" [цвет]` - Установить роль\n"
                  "• `/resetpoints` - Сбросить все поинты",
            inline=False
        )
    
    # Информация о системе
    embed.add_field(
        name="ℹ️ Информация о боте",
        value=f"• Админские роли: {', '.join(ADMIN_ROLES)}\n"
              f"• Данные хранятся в JSON файле\n"
              f"• Автосохранение каждые 30 минут\n"
              f"• Бэкапы каждый день\n"
              f"• Пингер для 24/7 работы\n"
              f"• Пользователей в базе: {len(points_data)}",
        inline=False
    )
    
    await ctx.send(embed=embed)

# ========== ДОПОЛНИТЕЛЬНЫЕ КОМАНДЫ ==========

@bot.hybrid_command(name='admininfo', description='Информация для администраторов')
@is_admin()
async def admin_info(ctx):
    """Информация для администраторов"""
    guild_id = str(ctx.guild.id)
    
    # Подсчет пользователей на сервере
    server_users = {}
    for key, points in points_data.items():
        if key.startswith(guild_id + '_'):
            user_id = key.split('_')[1]
            server_users[user_id] = points
    
    total_points = sum(server_users.values())
    
    embed = discord.Embed(
        title="🛡️ Информация для администраторов",
        color=discord.Color.purple()
    )
    
    embed.add_field(
        name="📊 Статистика сервера",
        value=f"• Пользователей с поинтами: **{len(server_users)}**\n"
              f"• Всего поинтов на сервере: **{total_points}**\n"
              f"• Настроено ролей: **{len(role_settings)}**",
        inline=False
    )
    
    embed.add_field(
        name="🔧 Управление системой",
        value="• Используйте `/setrole` для настройки ролей\n"
              "• `/resetpoints` сбрасывает ВСЕ данные сервера\n"
              "• Данные сохраняются автоматически",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Техническая информация",
        value=f"• Файл данных: `{DATA_FILE}`\n"
              f"• Бот активен 24/7\n"
              f"• Последнее сохранение: {datetime.now().strftime('%H:%M:%S')}",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='fixroles', description='Принудительно проверить и выдать роли всем пользователям')
@is_admin()
async def fix_roles(ctx):
    """Принудительно проверить и выдать роли"""
    embed = discord.Embed(
        title="🔄 Проверка ролей",
        description="Начинаю проверку ролей для всех пользователей...",
        color=discord.Color.blue()
    )
    message = await ctx.send(embed=embed)
    
    guild_id = str(ctx.guild.id)
    processed = 0
    
    # Ищем пользователей этого сервера
    for key in points_data.keys():
        if key.startswith(guild_id + '_'):
            user_id = int(key.split('_')[1])
            
            try:
                member = await ctx.guild.fetch_member(user_id)
                await check_and_assign_roles(member)
                processed += 1
                
                # Обновляем сообщение каждые 10 пользователей
                if processed % 10 == 0:
                    embed.description = f"Обработано {processed} пользователей..."
                    await message.edit(embed=embed)
                    
            except:
                pass
    
    embed = discord.Embed(
        title="✅ Проверка завершена",
        description=f"Обработано {processed} пользователей",
        color=discord.Color.green()
    )
    await message.edit(embed=embed)

# ========== ОБРАБОТКА ОШИБОК ==========

@bot.event
async def on_command_error(ctx, error):
    """Обработка ошибок команд"""
    if isinstance(error, commands.CheckFailure):
        embed = discord.Embed(
            title="❌ Недостаточно прав",
            description=f"Только **{', '.join(ADMIN_ROLES)}** могут использовать эту команду!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="❌ Не хватает аргументов",
            description=f"Используйте `{PREFIX}help` для справки по командам",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.BadArgument):
        embed = discord.Embed(
            title="❌ Неправильные аргументы",
            description="Проверьте правильность введенных данных",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.CommandNotFound):
        pass  # Игнорируем
    else:
        logger.error(f"Необработанная ошибка команды: {error}")
        embed = discord.Embed(
            title="❌ Неизвестная ошибка",
            description="Произошла неизвестная ошибка. Администраторы уведомлены.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

# ========== ЗАПУСК БОТА ==========

if __name__ == "__main__":
    if not TOKEN:
        logger.error("❌ Токен бота не найден! Установите переменную окружения DISCORD_TOKEN")
        logger.info("📝 Создайте файл .env с содержимым:")
        logger.info("DISCORD_TOKEN=your_token_here")
        logger.info("BOT_PREFIX=!")
        logger.info("ADMIN_ROLES=The Owner,Co-Owner")
        exit(1)
    
    logger.info("🚀 Запуск Discord Points Bot (без БД)")
    logger.info(f"🤖 Префикс команд: {PREFIX}")
    logger.info(f"👑 Админские роли: {ADMIN_ROLES}")
    logger.info("🔄 Бот будет работать 24/7 с пингером")
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Ошибка авторизации! Проверьте токен бота.")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
