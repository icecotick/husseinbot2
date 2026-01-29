import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
from typing import Optional, List
import logging
from datetime import datetime
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Импорт базы данных
from database import db

# Настройки
TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('BOT_PREFIX', '!')
ADMIN_ROLES = [role.strip() for role in os.getenv('ADMIN_ROLES', 'The Owner,Co-Owner').split(',')]

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Настройки интентов
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

# Функция проверки админа
def is_admin():
    async def predicate(ctx):
        # Проверка прав администратора Discord
        if ctx.author.guild_permissions.administrator:
            return True
        
        # Проверка кастомных админских ролей
        author_roles = [role.name for role in ctx.author.roles]
        return any(admin_role in author_roles for admin_role in ADMIN_ROLES)
    
    return commands.check(predicate)

# Цвета для embed
COLORS = {
    'success': discord.Color.green(),
    'error': discord.Color.red(),
    'info': discord.Color.blue(),
    'warning': discord.Color.orange(),
    'points': discord.Color.gold(),
    'admin': discord.Color.purple()
}

# События бота
@bot.event
async def on_ready():
    """Событие при запуске бота"""
    logger.info(f'✅ Бот {bot.user} запущен!')
    logger.info(f'📊 Серверов: {len(bot.guilds)}')
    
    # Подключаем базу данных
    try:
        await db.connect()
        logger.info('✅ База данных подключена')
        
        # Инициализируем стандартные роли для всех серверов
        for guild in bot.guilds:
            await init_default_roles(guild.id)
            
    except Exception as e:
        logger.error(f'❌ Ошибка подключения к БД: {e}')
    
    # Устанавливаем статус
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{PREFIX}help | {len(bot.guilds)} серверов"
        )
    )
    
    # Синхронизация slash команд
    try:
        synced = await bot.tree.sync()
        logger.info(f'✅ Синхронизировано {len(synced)} команд')
    except Exception as e:
        logger.error(f'❌ Ошибка синхронизации команд: {e}')

async def init_default_roles(guild_id: int):
    """Инициализация стандартных ролей"""
    default_roles = {
        50: {'name': 'raider newgen', 'color': '#2ecc71'},
        100: {'name': 'raider scout', 'color': '#3498db'},
        150: {'name': 'raider striker', 'color': '#e67e22'},
        350: {'name': 'raider legend', 'color': '#9b59b6'},
        500: {'name': 'raider commander', 'color': '#f1c40f'}
    }
    
    current_roles = await db.get_role_settings(guild_id)
    
    # Добавляем только если их нет
    for points, role_info in default_roles.items():
        if points not in current_roles:
            await db.set_role_setting(
                guild_id, points, 
                role_info['name'], 
                role_info['color']
            )

async def check_and_assign_roles(member: discord.Member):
    """Проверка и выдача ролей на основе поинтов"""
    try:
        guild_id = member.guild.id
        user_id = member.id
        
        # Получаем поинты пользователя
        points = await db.get_user_points(user_id, guild_id)
        
        # Получаем настройки ролей
        role_settings = await db.get_role_settings(guild_id)
        
        if not role_settings:
            return
        
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
        
        # Проверяем, есть ли уже эта роль
        current_roles = await db.get_user_roles(user_id, guild_id)
        if role_name in current_roles:
            return
        
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
                
                # Перемещаем роль вверх (но ниже админских)
                positions = {}
                for role in member.guild.roles:
                    if role.name in ADMIN_ROLES:
                        continue
                    positions[role] = role.position
                
                if positions:
                    max_position = max(positions.values())
                    await discord_role.edit(position=max_position + 1)
                    
            except discord.Forbidden:
                logger.error(f'Недостаточно прав для создания роли {role_name}')
                return
            except Exception as e:
                logger.error(f'Ошибка создания роли: {e}')
                return
        
        # Удаляем старые роли за поинты
        for points_required, role_info in role_settings.items():
            if role_info['name'] != role_name:
                old_role = discord.utils.get(member.guild.roles, name=role_info['name'])
                if old_role and old_role in member.roles:
                    try:
                        await member.remove_roles(old_role)
                        await db.remove_user_role(user_id, guild_id, role_info['name'])
                    except:
                        pass
        
        # Добавляем новую роль
        if discord_role and discord_role not in member.roles:
            try:
                await member.add_roles(discord_role)
                await db.assign_user_role(user_id, guild_id, role_name)
                logger.info(f'Выдана роль {role_name} пользователю {member.id}')
            except discord.Forbidden:
                logger.error(f'Недостаточно прав для выдачи роли {role_name}')
            except Exception as e:
                logger.error(f'Ошибка выдачи роли: {e}')
                
    except Exception as e:
        logger.error(f'Ошибка в check_and_assign_roles: {e}')

# Команды для админов
@bot.hybrid_command(name='addpoints', description='Выдать поинты пользователю')
@is_admin()
async def add_points(ctx, member: discord.Member, amount: int, reason: str = "Выдано админом"):
    """Выдать поинты пользователю"""
    if amount <= 0:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Количество поинтов должно быть положительным!",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    added, new_total = await db.add_points(
        member.id, ctx.guild.id, amount, 
        ctx.author.id, reason
    )
    
    embed = discord.Embed(
        title="✅ Поинты выданы!",
        color=COLORS['success']
    )
    embed.add_field(name="Получатель", value=member.mention, inline=True)
    embed.add_field(name="Добавлено", value=f"{added} поинтов", inline=True)
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
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    removed, new_total = await db.remove_points(
        member.id, ctx.guild.id, amount,
        ctx.author.id, reason
    )
    
    if removed == 0:
        embed = discord.Embed(
            title="⚠️ Внимание",
            description="У пользователя нет поинтов для изъятия",
            color=COLORS['warning']
        )
    else:
        embed = discord.Embed(
            title="✅ Поинты изъяты!",
            color=COLORS['success']
        )
        embed.add_field(name="Пользователь", value=member.mention, inline=True)
        embed.add_field(name="Изъято", value=f"{removed} поинтов", inline=True)
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
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    new_total = await db.set_points(
        member.id, ctx.guild.id, amount,
        ctx.author.id, reason
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
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    try:
        # Проверяем валидность цвета
        discord.Color.from_str(color)
    except:
        color = "#3498db"
    
    await db.set_role_setting(ctx.guild.id, points, role_name, color)
    
    embed = discord.Embed(
        title="✅ Роль установлена!",
        description=f"Роль **{role_name}** будет выдаваться за **{points}** поинтов",
        color=COLORS['success']
    )
    embed.add_field(name="Цвет роли", value=color, inline=True)
    embed.set_footer(text=f"ID сервера: {ctx.guild.id}")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='removerole', description='Удалить настройку роли')
@is_admin()
async def remove_role(ctx, points: int):
    """Удалить настройку роли"""
    role_settings = await db.get_role_settings(ctx.guild.id)
    
    if points not in role_settings:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Не найдена роль за {points} поинтов!",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)
        return
    
    role_name = role_settings[points]['name']
    await db.delete_role_setting(ctx.guild.id, points)
    
    embed = discord.Embed(
        title="✅ Роль удалена!",
        description=f"Удалена настройка роли **{role_name}** за **{points}** поинтов",
        color=COLORS['success']
    )
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='resetpoints', description='Сбросить все поинты на сервере')
@is_admin()
async def reset_points(ctx):
    """Сбросить все поинты на сервере"""
    embed = discord.Embed(
        title="⚠️ ОПАСНОЕ ДЕЙСТВИЕ",
        description="Вы уверены, что хотите сбросить ВСЕ поинты на сервере?\nЭто действие необратимо!",
        color=COLORS['error']
    )
    embed.add_field(name="Что будет сброшено:", 
                   value="• Все поинты пользователей\n• Вся история транзакций\n• Все выданные роли за поинты", 
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

# Команды для всех пользователей
@bot.hybrid_command(name='points', description='Проверить свои поинты или поинты другого пользователя')
async def check_points(ctx, member: Optional[discord.Member] = None):
    """Проверить поинты"""
    if member is None:
        member = ctx.author
    
    user_id = member.id
    guild_id = ctx.guild.id
    
    # Получаем данные
    points = await db.get_user_points(user_id, guild_id)
    position = await db.get_user_position(user_id, guild_id)
    role_settings = await db.get_role_settings(guild_id)
    user_roles = await db.get_user_roles(user_id, guild_id)
    
    # Создаем embed
    embed = discord.Embed(
        title=f"🏆 Поинты {member.display_name}",
        color=COLORS['points']
    )
    
    # Основная информация
    embed.add_field(name="Баланс", value=f"**{points}** поинтов", inline=True)
    embed.add_field(name="Позиция в рейтинге", value=f"**#{position}**", inline=True)
    
    if user_roles:
        embed.add_field(name="Текущая роль", value=f"**{user_roles[-1]}**", inline=True)
    
    # Система ролей
    if role_settings:
        roles_text = []
        sorted_roles = sorted(role_settings.items(), key=lambda x: x[0])
        
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
    if role_settings:
        sorted_roles = sorted(role_settings.items(), key=lambda x: x[0])
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
    guild_id = ctx.guild.id
    limit = 10
    offset = (page - 1) * limit
    
    # Получаем лидерборд
    leaderboard_data = await db.get_leaderboard(guild_id, limit + offset)
    
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
    
    for i, (user_id, user_points) in enumerate(leaderboard_data[offset:offset + limit], start=1):
        try:
            member = await ctx.guild.fetch_member(user_id)
            username = member.display_name
        except:
            username = f"Пользователь ({user_id})"
        
        medal = medals[i-1] if i <= len(medals) else f"{i}."
        
        # Определяем роль
        user_role = "Нет роли"
        if role_settings := await db.get_role_settings(guild_id):
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
    embed.add_field(
        name="📊 Статистика сервера",
        value=f"• Всего пользователей: **{stats['total_users']}**\n"
              f"• Всего поинтов: **{stats['total_points']}**\n"
              f"• Среднее: **{stats['avg_points']:.1f}**\n"
              f"• Максимум: **{stats['max_points']}**",
        inline=False
    )
    
    # Пагинация
    total_pages = (len(leaderboard_data) + limit - 1) // limit
    if total_pages > 1:
        embed.set_footer(text=f"Страница {page}/{total_pages} | Всего участников: {stats['total_users']}")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='history', description='История ваших транзакций')
async def history(ctx, limit: int = 10):
    """История транзакций"""
    if limit > 25:
        limit = 25
    
    transactions = await db.get_user_transactions(ctx.author.id, ctx.guild.id, limit)
    
    if not transactions:
        embed = discord.Embed(
            title="📜 История транзакций",
            description="У вас нет истории транзакций.",
            color=COLORS['info']
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title=f"📜 История транзакций {ctx.author.display_name}",
        color=COLORS['info']
    )
    
    total_positive = 0
    total_negative = 0
    
    for i, transaction in enumerate(transactions, 1):
        amount = transaction['amount']
        reason = transaction['reason']
        
        if amount > 0:
            total_positive += amount
            emoji = "📈"
            amount_str = f"+{amount}"
        else:
            total_negative += abs(amount)
            emoji = "📉"
            amount_str = f"{amount}"
        
        # Форматируем дату
        date = transaction['created_at'].strftime("%d.%m.%Y %H:%M")
        
        embed.add_field(
            name=f"{emoji} {date}",
            value=f"**{amount_str}** поинтов\n*{reason}*",
            inline=False
        )
    
    # Итоги
    embed.add_field(
        name="📊 Итоги",
        value=f"Получено: **+{total_positive}**\n"
              f"Потрачено: **-{total_negative}**\n"
              f"Баланс: **{total_positive - total_negative}**",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='roles', description='Показать систему ролей')
async def show_roles(ctx):
    """Показать систему ролей"""
    role_settings = await db.get_role_settings(ctx.guild.id)
    
    if not role_settings:
        embed = discord.Embed(
            title="🏅 Система ролей",
            description="Система ролей не настроена.\nАдмины могут настроить с помощью `/setrole`",
            color=COLORS['info']
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="🏅 Система ролей",
        description="Роли выдаются автоматически при достижении определенного количества поинтов",
        color=COLORS['points']
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

@bot.hybrid_command(name='stats', description='Статистика сервера')
@is_admin()
async def stats(ctx):
    """Статистика сервера"""
    stats_data = await db.get_guild_stats(ctx.guild.id)
    
    embed = discord.Embed(
        title="📊 Статистика сервера",
        color=COLORS['admin']
    )
    
    embed.add_field(
        name="👥 Пользователи",
        value=f"Всего: **{stats_data['total_users']}**",
        inline=True
    )
    
    embed.add_field(
        name="🏆 Поинты",
        value=f"Всего: **{stats_data['total_points']}**",
        inline=True
    )
    
    embed.add_field(
        name="📈 Среднее",
        value=f"**{stats_data['avg_points']:.1f}** поинтов",
        inline=True
    )
    
    # Топ 3 пользователя
    if stats_data['top_users']:
        top_text = ""
        for i, (user_id, points) in enumerate(stats_data['top_users'], 1):
            try:
                member = await ctx.guild.fetch_member(user_id)
                username = member.display_name
            except:
                username = f"User {user_id}"
            
            top_text += f"**{i}.** {username}: **{points}** поинтов\n"
        
        embed.add_field(
            name="🏅 Топ-3",
            value=top_text,
            inline=False
        )
    
    # Активность
    role_settings = await db.get_role_settings(ctx.guild.id)
    if role_settings:
        embed.add_field(
            name="🎯 Уровни",
            value=f"Настроено ролей: **{len(role_settings)}**",
            inline=True
        )
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name='help', description='Показать все команды')
async def help_command(ctx):
    """Показать все команды"""
    # Проверяем, является ли пользователь админом
    is_user_admin = await is_admin().predicate(ctx)
    
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
              "• `/history [лимит]` - История транзакций\n"
              "• `/roles` - Система ролей\n"
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
                  "• `/removerole количество` - Удалить роль\n"
                  "• `/resetpoints` - Сбросить все поинты\n"
                  "• `/stats` - Статистика сервера",
            inline=False
        )
    
    # Информация о системе
    embed.add_field(
        name="ℹ️ Информация",
        value=f"• Админские роли: {', '.join(ADMIN_ROLES)}\n"
              f"• Бот работает на Render.com\n"
              f"• Данные хранятся в PostgreSQL\n"
              f"• Роли выдаются автоматически",
        inline=False
    )
    
    await ctx.send(embed=embed)

# Запуск бота
if __name__ == "__main__":
    if not TOKEN:
        logger.error("❌ Токен бота не найден! Установите переменную окружения DISCORD_TOKEN")
        exit(1)
    
    logger.info("🚀 Запуск Discord Points Bot...")
    bot.run(TOKEN)
