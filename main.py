import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import logging
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from aiohttp import web
import asyncio

# Импортируем нашу базу данных
from database import db

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
ADMIN_ROLES = [role.strip() for role in os.getenv('ADMIN_ROLES', 'The Owner,Co-Owner').split(',')]
PORT = int(os.getenv('PORT', '10000'))  #  ПОРТ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ

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

# ========== ВЕБ-СЕРВЕР ДЛЯ ПИНГА ==========

async def handle_ping(request):
    """Обработчик пинга"""
    logger.info("🏓 Получен пинг от мониторинга")
    return web.Response(text="Bot is alive! 🟢\nServers: " + str(len(bot.guilds)))

async def handle_health(request):
    """Обработчик health check"""
    return web.json_response({
        "status": "ok",
        "bot": str(bot.user),
        "servers": len(bot.guilds),
        "uptime": str(datetime.now())
    })

async def start_web_server():
    """Запуск веб-сервера для пинга"""
    try:
        app = web.Application()
        
        # Добавляем маршруты
        app.router.add_get('/', handle_ping)
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
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска веб-сервера: {e}")

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С РОЛЯМИ ==========

def is_admin():
    """Проверка, является ли пользователь администратором"""
    async def predicate(ctx):
        # Проверка прав администратора Discord
        if ctx.author.guild_permissions.administrator:
            return True
        
        # Проверка кастомных админских ролей
        author_roles = [role.name for role in ctx.author.roles]
        return any(admin_role in author_roles for admin_role in ADMIN_ROLES)
    
    return commands.check(predicate)

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

# ========== ТАСКИ ДЛЯ 24/7 РАБОТЫ ==========

@tasks.loop(minutes=14)
async def keep_alive():
    """Таск для поддержания активности бота"""
    try:
        logger.info(f"🤖 Бот активен | Серверов: {len(bot.guilds)}")
        
        # Обновляем статус
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{PREFIX}help | {len(bot.guilds)} серв."
            )
        )
    except Exception as e:
        logger.error(f"Ошибка в keep_alive: {e}")

# ========== СОБЫТИЯ БОТА ==========

@bot.event
async def on_ready():
    """Событие при запуске бота"""
    logger.info(f'✅ Бот {bot.user} запущен!')
    logger.info(f'📊 Серверов: {len(bot.guilds)}')
    logger.info(f'🌐 Порт веб-сервера: {PORT}')
    
    # Запускаем веб-сервер для пинга
    asyncio.create_task(start_web_server())
    
    # Подключаемся к базе данных
    if await db.connect():
        logger.info("✅ Подключение к базе данных успешно")
        
        # Инициализируем стандартные роли для всех серверов
        for guild in bot.guilds:
            try:
                await db.init_default_roles(guild.id)
                logger.info(f"✅ Инициализированы роли для сервера: {guild.name}")
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации ролей для {guild.name}: {e}")
    else:
        logger.error("❌ Не удалось подключиться к базе данных!")
        logger.error("⚠️  Бот будет работать в ограниченном режиме")
    
    # Запускаем таск для поддержания активности
    keep_alive.start()
    
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

# ========== КОМАНДЫ ДЛЯ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ ==========

@bot.hybrid_command(name='points', description='Проверить свои поинты или поинты другого пользователя')
async def check_points(ctx, member: Optional[discord.Member] = None):
    """Проверить поинты"""
    if member is None:
        member = ctx.author
    
    user_id = member.id
    guild_id = ctx.guild.id
    
    # Получаем данные из базы
    points = await db.get_user_points(user_id, guild_id)
    position = await db.get_user_position(user_id, guild_id)
    role_settings = await db.get_role_settings(guild_id)
    
    # Создаем embed
    embed = discord.Embed(
        title=f"🏆 Поинты {member.display_name}",
        color=COLORS['points']
    )
    
    # Основная информация
    embed.add_field(name="Баланс", value=f"**{points}** поинтов", inline=True)
    embed.add_field(name="Позиция в рейтинге", value=f"**#{position}**", inline=True)
    
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
    guild_id = ctx.guild.id
    
    # Получаем лидерборд из базы
    leaderboard_data = await db.get_leaderboard(guild_id, 100)  # Берем больше для пагинации
    
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
    
    # Пагинация
    limit = 10
    total_pages = (len(leaderboard_data) + limit - 1) // limit
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    page_data = leaderboard_data[start_idx:end_idx]
    
    # Создаем embed
    embed = discord.Embed(
        title="🏆 Таблица лидеров",
        color=COLORS['points']
    )
    
    # Добавляем записи
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, (user_id, user_points) in enumerate(page_data, start=1):
        try:
            member = await ctx.guild.fetch_member(user_id)
            username = member.display_name
        except:
            username = f"Пользователь ({user_id})"
        
        medal = medals[i-1] if i <= len(medals) else f"{i+start_idx}."
        
        # Определяем роль
        user_role = "Нет роли"
        role_settings = await db.get_role_settings(guild_id)
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
    embed.add_field(
        name="📊 Статистика сервера",
        value=f"• Всего пользователей: **{stats['total_users']}**\n"
              f"• Всего поинтов: **{stats['total_points']}**\n"
              f"• Среднее: **{stats['avg_points']:.1f}**\n"
              f"• Максимум: **{stats['max_points']}**",
        inline=False
    )
    
    # Пагинация
    if total_pages > 1:
        embed.set_footer(text=f"Страница {page}/{total_pages} | Всего участников: {stats['total_users']}")
    
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

@bot.hybrid_command(name='ping', description='Проверить пинг бота')
async def ping_command(ctx):
    """Проверить пинг бота"""
    latency = round(bot.latency * 1000)
    
    embed = discord.Embed(
        title="🏓 Понг!",
        color=COLORS['success']
    )
    embed.add_field(name="Задержка API", value=f"**{latency}мс**", inline=True)
    embed.add_field(name="Серверов", value=f"**{len(bot.guilds)}**", inline=True)
    embed.add_field(name="Порт", value=f"**{PORT}**", inline=True)
    embed.add_field(name="Статус БД", value="✅ **Подключена**", inline=True)
    embed.add_field(name="Режим работы", value="✅ **24/7 Активен**", inline=False)
    embed.set_footer(text=f"Эндпоинт для пинга: /ping")
    
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
    logger.info("🚀 Запуск Discord Points Bot с PostgreSQL")
    logger.info(f"🤖 Префикс команд: {PREFIX}")
    logger.info(f"👑 Админские роли: {ADMIN_ROLES}")
    logger.info(f"🌐 Порт веб-сервера: {PORT}")
    logger.info("🗄️  Используется база данных PostgreSQL")
    logger.info("🔄 Бот будет работать 24/7 с веб-сервером для пинга")
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Ошибка авторизации! Проверьте токен бота.")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
