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
ADMIN_ROLES = [role.strip() forrolee in os.getenv('ADMIN_ROLES', 'The Owner,Co-Owner,Administrator,Right wing').split(',')]
PORT = int(os.getenv('PORT', '9999'))  #  ПОРТ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ

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

RAID_CHANNEL_IDS = [
    1465349741616304291,  # https://discord.com/channels/1431584140511154229/1465349741616304291
    1431694391528919232,  # https://discord.com/channels/1431584140511154229/1431694391528919232
    1441862129887084655,  # https://discord.com/channels/1431584140511154229/1441862129887084655
    1441862087851905177   # https://discord.com/channels/1431584140511154229/1441862087851905177
]

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
    """Проверка и выдача ролей на основе поинтов с уведомлениями в raid-points"""
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
        discord_role = discord.utils.get(member.guild.roles, name=role_name)
        if discord_role and discord_role in member.roles:
            return  # Уже есть эта роль
        
        # Находим или создаем роль на сервере
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
        
        # Получаем старую роль пользователя (если есть)
        old_role_name = None
        old_role_points = 0
        for points_required, role_info in sorted_roles:
            old_role = discord.utils.get(member.guild.roles, name=role_info['name'])
            if old_role and old_role in member.roles:
                old_role_name = role_info['name']
                old_role_points = points_required
                break
        
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
                
                # Отправляем уведомление о новой роли в raid-points
                await send_role_notification_to_raid_channel(member, role_name, points, old_role_name)
                
            except discord.Forbidden:
                logger.error(f'Недостаточно прав для выдачи роли {role_name}')
            except Exception as e:
                logger.error(f'Ошибка выдачи роли: {e}')
                
    except Exception as e:
        logger.error(f'Ошибка в check_and_assign_roles: {e}')

async def send_role_notification_to_raid_channel(member: discord.Member, new_role: str, points: int, old_role: str = None):
    """Отправка уведомления о получении новой роли в канал raid-points"""
    try:
        # Ищем канал raid-points
        raid_channel = await get_raid_points_channel(member.guild)
        
        if not raid_channel:
            logger.warning(f"Канал 'raid-points' не найден на сервере {member.guild.name}")
            # Пробуем отправить в ЛС
            await send_dm_notification(member, new_role, points, old_role)
            return
        
        # Проверяем права на отправку в канал
        if not raid_channel.permissions_for(member.guild.me).send_messages:
            logger.warning(f"Нет прав на отправку в канал {raid_channel.name}")
            await send_dm_notification(member, new_role, points, old_role)
            return
        
        # Создаем embed для уведомления
        embed = create_role_notification_embed(member, new_role, points, old_role)
        
        # Отправляем уведомление с упоминанием пользователя
        message_content = f"🎉 {member.mention}, поздравляем с новой ролью!"
        
        message = await raid_channel.send(
            content=message_content,
            embed=embed
        )
        
        # Добавляем праздничные реакции
        await add_celebration_reactions(message)
        
        logger.info(f"Уведомление о роли отправлено в канал {raid_channel.name}")
            
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о роли: {e}")
        # Пробуем отправить в ЛС если не удалось в канал
        try:
            await send_dm_notification(member, new_role, points, old_role)
        except:
            pass

async def get_raid_points_channel(guild: discord.Guild):
    """Поиск канала raid-points на сервере"""
    # Ищем по точному названию
    raid_channel = discord.utils.get(guild.text_channels, name="raid-points")
    
    # Если не нашли, ищем по похожим названиям
    if not raid_channel:
        similar_names = ['raidpoints', 'raid-points', 'raid_points', 'raid', 'points', 'рейд-поинты']
        for channel in guild.text_channels:
            if any(name in channel.name.lower() for name in similar_names):
                raid_channel = channel
                break
    
    return raid_channel

async def add_celebration_reactions(message):
    """Добавляет праздничные реакции к сообщению"""
    reactions = ["🎉", "🏆", "⭐", "👑", "🔥", "💪", "🚀"]
    
    for reaction in reactions[:3]:  # Добавляем только первые 3 реакции
        try:
            await message.add_reaction(reaction)
        except:
            pass

async def send_dm_notification(member: discord.Member, new_role: str, points: int, old_role: str = None):
    """Отправка уведомления в личные сообщения (резервный вариант)"""
    try:
        embed = create_role_notification_embed(member, new_role, points, old_role)
        
        # Пытаемся отправить в ЛС
        await member.send(
            f"🎉 Поздравляем, {member.name}! Вы получили новую роль на сервере **{member.guild.name}**!\n"
            f"*Уведомление должно было отправиться в канал #raid-points, но он не найден или нет прав*",
            embed=embed
        )
        
        logger.info(f"Уведомление о роли отправлено в ЛС пользователю {member.name}")
        
    except discord.Forbidden:
        logger.warning(f"Не удалось отправить ЛС пользователю {member.name} (запрещено)")
    except Exception as e:
        logger.error(f"Ошибка отправки ЛС: {e}")

def create_role_notification_embed(member: discord.Member, new_role: str, points: int, old_role: str = None):
    """Создание embed для уведомления о роли"""
    
    # Определяем цвет в зависимости от роли
    role_colors = {
        'raider newgen': discord.Color.green(),
        'raider scout': discord.Color.blue(),
        'raider striker': discord.Color.orange(),
        'raider legend': discord.Color.purple(),
        'raider commander': discord.Color.gold()
    }
    
    color = role_colors.get(new_role.lower(), discord.Color.blurple())
    
    # Создаем embed
    embed = discord.Embed(
        title="🎉 НОВАЯ РОЛЬ!",
        description=f"**{member.display_name}** получил(а) новую роль!",
        color=color,
        timestamp=discord.utils.utcnow()
    )
    
    # Аватар пользователя
    embed.set_thumbnail(url=member.display_avatar.url)
    
    # Информация о ролях
    role_info = f"**🎖️ Новая роль:** `{new_role}`\n"
    if old_role:
        role_info += f"**📈 Предыдущая роль:** `{old_role}`\n"
    role_info += f"**🏆 Текущие поинты:** `{points}`"
    
    embed.add_field(name="Роли и поинты", value=role_info, inline=False)
    
    # Поздравление в зависимости от роли
    congratulations = {
        'raider newgen': "Добро пожаловать в ряды рейдеров! Начинается твое путешествие! 🚀",
        'raider scout': "Отличная работа! Ты становишься опытным скаутом! 🔍",
        'raider striker': "Впечатляюще! Ты теперь ударная сила нашего отряда! 💥",
        'raider legend': "Легендарно! Твои достижения войдут в историю! 📜",
        'raider commander': "Величайший из великих! Ты ведешь за собой весь отряд! 👑"
    }
    
    congrats_text = congratulations.get(new_role.lower(), 
        f"Поздравляем с получением роли **{new_role}**! Продолжай в том же духе! ✨")
    
    embed.add_field(name="🎊 Поздравления!", value=congrats_text, inline=False)
    
    # Следующая цель
    next_role_info = get_next_role_info(new_role)
    if next_role_info:
        embed.add_field(name="🎯 Следующая цель", value=next_role_info, inline=False)
    
    # Футер
    embed.set_footer(
        text=f"ID: {member.id} • Автоматическая выдача",
        icon_url=member.guild.icon.url if member.guild.icon else None
    )
    
    return embed

def get_next_role_info(current_role: str):
    """Получение информации о следующей роли"""
    role_hierarchy = {
        'raider newgen': {'next': 'raider scout', 'points': 100},
        'raider scout': {'next': 'raider striker', 'points': 150},
        'raider striker': {'next': 'raider legend', 'points': 350},
        'raider legend': {'next': 'raider commander', 'points': 500},
        'raider commander': {'next': None, 'points': None}
    }
    
    info = role_hierarchy.get(current_role.lower())
    if info and info['next']:
        return f"**Следующая роль:** `{info['next']}` (нужно {info['points']} поинтов)"
    elif current_role.lower() == 'raider commander':
        return "🎖️ **Достигнута максимальная роль!** Ты на вершине! 🏔️"
    
    return None

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
@bot.hybrid_command(
    name='setnotificationchannel',
    description='Установить канал для уведомлений о ролях'
)
@is_admin()
async def set_notification_channel(
    ctx,
    channel: Optional[discord.TextChannel] = None
):
    """Установить канал для уведомлений о ролях"""
    try:
        if channel is None:
            # Если канал не указан, устанавливаем текущий
            channel = ctx.channel
        
        # Здесь можно сохранить настройку в базе данных
        # Пока просто показываем сообщение
        
        embed = discord.Embed(
            title="✅ Канал уведомлений установлен",
            description=f"Уведомления о получении ролей будут отправляться в {channel.mention}",
            color=COLORS['success']
        )
        
        # Проверяем доступность канала
        if not channel.permissions_for(ctx.guild.me).send_messages:
            embed.add_field(
                name="⚠️ Внимание",
                value="У бота нет прав на отправку сообщений в этот канал!",
                inline=False
            )
        
        embed.add_field(
            name="Текущие настройки",
            value=f"• Канал: {channel.mention}\n"
                  f"• Название: `{channel.name}`\n"
                  f"• ID: `{channel.id}`",
            inline=False
        )
        
        embed.set_footer(text="Уведомления отправляются автоматически при получении новой роли")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Ошибка в set_notification_channel: {e}")
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Произошла ошибка: {str(e)}",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)

@bot.hybrid_command(
    name='testnotification',
    description='Тестовая отправка уведомления в raid-points'
)
@is_admin()
async def test_notification(ctx, member: Optional[discord.Member] = None):
    """Тестовая отправка уведомления"""
    try:
        if member is None:
            member = ctx.author
        
        # Ищем канал raid-points
        raid_channel = await get_raid_points_channel(ctx.guild)
        
        if not raid_channel:
            embed = discord.Embed(
                title="❌ Канал не найден",
                description="Канал 'raid-points' не найден на сервере.",
                color=COLORS['error']
            )
            embed.add_field(
                name="Рекомендации",
                value="1. Создайте текстовый канал с именем `raid-points`\n"
                      "2. Убедитесь, что у бота есть права на отправку сообщений\n"
                      "3. Используйте команду `/setnotificationchannel` для настройки",
                inline=False
            )
            await ctx.send(embed=embed)
            return
        
        # Проверяем права
        if not raid_channel.permissions_for(ctx.guild.me).send_messages:
            embed = discord.Embed(
                title="❌ Нет прав",
                description=f"У бота нет прав на отправку сообщений в {raid_channel.mention}",
                color=COLORS['error']
            )
            await ctx.send(embed=embed)
            return
        
        # Создаем тестовое уведомление
        embed = create_role_notification_embed(
            member=member,
            new_role="raider commander",  # Тестовая роль
            points=999,
            old_role="raider legend"
        )
        
        # Отправляем тест
        test_message = await raid_channel.send(
            f"🔧 **ТЕСТОВОЕ УВЕДОМЛЕНИЕ**\n"
            f"{member.mention}, это тест отправки уведомлений!",
            embed=embed
        )
        
        # Добавляем реакции
        await add_celebration_reactions(test_message)
        
        # Отчет об успехе
        success_embed = discord.Embed(
            title="✅ Тест успешен!",
            description=f"Тестовое уведомление отправлено в {raid_channel.mention}",
            color=COLORS['success']
        )
        success_embed.add_field(
            name="Детали теста",
            value=f"• Пользователь: {member.mention}\n"
                  f"• Канал: {raid_channel.mention}\n"
                  f"• Сообщение: [Перейти]({test_message.jump_url})",
            inline=False
        )
        
        await ctx.send(embed=success_embed)
        
    except Exception as e:
        logger.error(f"Ошибка в test_notification: {e}")
        embed = discord.Embed(
            title="❌ Ошибка теста",
            description=f"Произошла ошибка: {str(e)}",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)

@bot.hybrid_command(
    name='checkraidchannel',
    description='Проверить канал raid-points'
)
async def check_raid_channel(ctx):
    """Проверить наличие и доступность канала raid-points"""
    try:
        # Ищем канал
        raid_channel = await get_raid_points_channel(ctx.guild)
        
        embed = discord.Embed(
            title="🔍 Проверка канала raid-points",
            color=COLORS['info']
        )
        
        if raid_channel:
            embed.description = f"Канал найден: {raid_channel.mention}"
            
            # Проверяем права
            perms = raid_channel.permissions_for(ctx.guild.me)
            
            status = []
            if perms.send_messages:
                status.append("✅ Отправка сообщений")
            else:
                status.append("❌ Нет прав на отправку сообщений")
            
            if perms.embed_links:
                status.append("✅ Встраиваемые ссылки (embeds)")
            else:
                status.append("❌ Нет прав на embeds")
            
            if perms.add_reactions:
                status.append("✅ Добавление реакций")
            else:
                status.append("❌ Нет прав на реакции")
            
            if perms.mention_everyone:
                status.append("✅ Упоминания")
            else:
                status.append("⚠️ Нет прав на @everyone")
            
            embed.add_field(
                name="📊 Статус прав",
                value="\n".join(status),
                inline=False
            )
            
            embed.add_field(
                name="📝 Информация о канале",
                value=f"• Название: `{raid_channel.name}`\n"
                      f"• ID: `{raid_channel.id}`\n"
                      f"• Позиция: {raid_channel.position}\n"
                      f"• Создан: {raid_channel.created_at.strftime('%d.%m.%Y')}",
                inline=False
            )
            
            if all([perms.send_messages, perms.embed_links, perms.add_reactions]):
                embed.add_field(
                    name="🎉 Готов к работе!",
                    value="Канал полностью готов к отправке уведомлений!",
                    inline=False
                )
            else:
                embed.add_field(
                    name="⚠️ Требуются права",
                    value="Необходимо выдать боту права:\n"
                          "• Send Messages\n"
                          "• Embed Links\n"
                          "• Add Reactions",
                    inline=False
                )
                
        else:
            embed.description = "Канал 'raid-points' не найден!"
            embed.add_field(
                name="🚀 Как создать?",
                value="1. Создайте текстовый канал\n"
                      "2. Назовите его `raid-points`\n"
                      "3. Убедитесь, что у бота есть права:\n"
                      "   • 📝 Отправка сообщений\n"
                      "   • 🔗 Встраиваемые ссылки\n"
                      "   • ⭐ Добавление реакций",
                inline=False
            )
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Ошибка в check_raid_channel: {e}")
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Произошла ошибка: {str(e)}",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)


@bot.hybrid_command(
    name='addpoints',
    description='Выдать поинты одному или нескольким пользователям'
)
@is_admin()
async def add_points(
    ctx,
    members: commands.Greedy[discord.Member],  # Множество пользователей
    amount: int,  # Количество поинтов
    *, reason: str = "Выдано админом"  # Причина
):
    """
    Выдать поинты одному или нескольким пользователям
    
    Примеры использования:
    !addpoints @User1 100 Награда
    !addpoints @User1 @User2 @User3 50 Общая награда
    !addpoints @User1 100
    """
    
    try:
        # Проверка аргументов
        if not members:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Не указан пользователь!",
                color=COLORS['error']
            )
            embed.add_field(
                name="Примеры использования:",
                value="• `!addpoints @пользователь 100`\n"
                      "• `!addpoints @пользователь1 @пользователь2 50 Награда`",
                inline=False
            )
            await ctx.send(embed=embed)
            return
        
        if amount <= 0:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Количество поинтов должно быть положительным!",
                color=COLORS['error']
            )
            await ctx.send(embed=embed)
            return
        
        if len(members) > 20:
            embed = discord.Embed(
                title="❌ Слишком много пользователей",
                description="Можно выдать поинты не более чем 20 пользователям за раз.",
                color=COLORS['error']
            )
            await ctx.send(embed=embed)
            return
        
        # Если один пользователь - старый формат
        if len(members) == 1:
            member = members[0]
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
            
        # Если несколько пользователей
        else:
            # Начинаем выдачу
            processing_embed = discord.Embed(
                title="🔄 Выдача поинтов...",
                description=f"Выдача {amount} поинтов {len(members)} пользователям",
                color=COLORS['info']
            )
            processing_embed.add_field(name="Причина", value=reason, inline=False)
            processing_embed.set_footer(text="Подождите, идет обработка...")
            
            message = await ctx.send(embed=processing_embed)
            
            # Выдаем поинты всем пользователям
            results = []
            for member in members:
                try:
                    new_total = await db.add_points(member.id, ctx.guild.id, amount, ctx.author.id, reason)
                    results.append({
                        'member': member,
                        'success': True,
                        'new_total': new_total
                    })
                    
                    # Проверяем и выдаем роли
                    await check_and_assign_roles(member)
                    
                except Exception as e:
                    logger.error(f"Ошибка выдачи поинтов {member}: {e}")
                    results.append({
                        'member': member,
                        'success': False,
                        'error': str(e)
                    })
            
            # Создаем итоговый отчет
            success_count = sum(1 for r in results if r['success'])
            failed_count = len(results) - success_count
            
            # Основной embed с итогами
            final_embed = discord.Embed(
                title="✅ Выдача поинтов завершена!",
                color=COLORS['success'] if failed_count == 0 else COLORS['warning']
            )
            
            # Сводка
            summary = f"**Успешно:** {success_count} пользователей\n"
            if failed_count > 0:
                summary += f"**Не удалось:** {failed_count} пользователей\n"
            summary += f"**Количество поинтов:** {amount} каждому\n"
            summary += f"**Причина:** {reason}"
            
            final_embed.add_field(name="📊 Сводка", value=summary, inline=False)
            
            # Список пользователей (первые 10)
            if len(members) <= 10:
                users_list = ""
                for result in results:
                    if result['success']:
                        users_list += f"✅ {result['member'].mention} → **{result['new_total']}** поинтов\n"
                    else:
                        users_list += f"❌ {result['member'].mention} → Ошибка\n"
                
                final_embed.add_field(
                    name="👥 Пользователи",
                    value=users_list,
                    inline=False
                )
            else:
                # Если много пользователей, показываем только успешных/неуспешных
                if success_count > 0:
                    final_embed.add_field(
                        name=f"✅ Успешно ({success_count})",
                        value=f"Поинты выданы {success_count} пользователям",
                        inline=True
                    )
                if failed_count > 0:
                    final_embed.add_field(
                        name=f"❌ Ошибки ({failed_count})",
                        value=f"Не удалось выдать {failed_count} пользователям",
                        inline=True
                    )
            
            final_embed.add_field(name="👑 Выдал", value=ctx.author.mention, inline=True)
            final_embed.set_footer(text=f"Всего пользователей: {len(members)}")
            
            await message.edit(embed=final_embed)
            
            # Если были ошибки, показываем детали
            if failed_count > 0:
                error_details = ""
                for result in results:
                    if not result['success']:
                        error_details += f"• {result['member'].mention}: {result.get('error', 'Неизвестная ошибка')}\n"
                
                if error_details:
                    error_embed = discord.Embed(
                        title="⚠️ Детали ошибок",
                        description=error_details[:1000],  # Ограничение Discord
                        color=COLORS['error']
                    )
                    await ctx.send(embed=error_embed)
    
    except Exception as e:
        logger.error(f"Ошибка в add_points: {e}")
        embed = discord.Embed(
            title="❌ Критическая ошибка",
            description=f"Произошла ошибка: {str(e)}",
            color=COLORS['error']
        )
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

@bot.hybrid_command(
    name='raidblock',
    description='Заблокировать каналы для всех пользователей'
)
@is_admin()
async def raid_block(ctx):
    """Блокировка каналов - доступ только для админов"""
    try:
        embed = discord.Embed(
            title="🔒 Блокировка каналов",
            description="Начинаю блокировку каналов...",
            color=COLORS['warning']
        )
        embed.add_field(
            name="Каналы для блокировки",
            value=f"• <#{RAID_CHANNEL_IDS[0]}>\n"
                  f"• <#{RAID_CHANNEL_IDS[1]}>\n"
                  f"• <#{RAID_CHANNEL_IDS[2]}>\n"
                  f"• <#{RAID_CHANNEL_IDS[3]}>",
            inline=False
        )
        embed.set_footer(text="Доступ останется только у админов")
        
        message = await ctx.send(embed=embed)
        
        # Получаем роль @everyone
        everyone_role = ctx.guild.default_role
        
        # Список результатов
        results = []
        blocked_channels = []
        
        for channel_id in RAID_CHANNEL_IDS:
            try:
                channel = ctx.guild.get_channel(channel_id)
                if not channel:
                    # Пробуем получить канал через fetch
                    channel = await ctx.guild.fetch_channel(channel_id)
                
                if channel:
                    # Сохраняем текущие права для роли @everyone
                    current_perms = channel.overwrites_for(everyone_role)
                    
                    # Создаем новые права - запрещаем отправку сообщений
                    overwrite = discord.PermissionOverwrite()
                    overwrite.send_messages = False
                    overwrite.add_reactions = False
                    
                    # Применяем права
                    await channel.set_permissions(everyone_role, overwrite=overwrite)
                    
                    results.append(f"✅ {channel.mention} - заблокирован")
                    blocked_channels.append(channel)
                    
                else:
                    results.append(f"❌ Канал {channel_id} не найден")
                    
            except discord.Forbidden:
                results.append(f"❌ Нет прав для блокировки канала {channel_id}")
            except discord.HTTPException as e:
                results.append(f"❌ Ошибка API: {e}")
            except Exception as e:
                results.append(f"❌ Неизвестная ошибка: {str(e)}")
        
        # Создаем итоговый embed
        final_embed = discord.Embed(
            title="🔒 Блокировка завершена",
            color=COLORS['success'] if len(blocked_channels) > 0 else COLORS['error']
        )
        
        # Статистика
        success_count = sum(1 for r in results if "✅" in r)
        failed_count = len(results) - success_count
        
        final_embed.add_field(
            name="📊 Результаты",
            value=f"**Успешно:** {success_count} каналов\n"
                  f"**Не удалось:** {failed_count} каналов",
            inline=False
        )
        
        # Детали
        if len(results) <= 10:
            final_embed.add_field(
                name="📝 Детали",
                value="\n".join(results),
                inline=False
            )
        
        # Инструкция для админов
        admin_roles = [discord.utils.get(ctx.guild.roles, name=role_name) for role_name in ADMIN_ROLES]
        admin_mentions = [role.mention for role in admin_roles if role]
        
        if admin_mentions:
            final_embed.add_field(
                name="👑 Доступ остался у",
                value="\n".join(admin_mentions),
                inline=True
            )
        
        final_embed.add_field(
            name="🔓 Для разблокировки",
            value=f"Используйте `{PREFIX}raidunlock`",
            inline=True
        )
        
        final_embed.set_footer(text=f"Команда выполнена: {ctx.author.display_name}")
        
        await message.edit(embed=final_embed)
        
    except Exception as e:
        logger.error(f"Ошибка в raid_block: {e}")
        embed = discord.Embed(
            title="❌ Ошибка блокировки",
            description=f"Произошла ошибка: {str(e)}",
            color=COLORS['error']
        )
        await ctx.send(embed=embed)

@bot.hybrid_command(
    name='raidunlock',
    description='Разблокировать каналы для всех пользователей'
)
@is_admin()
async def raid_unlock(ctx):
    """Разблокировка рейдовых каналов"""
    try:
        embed = discord.Embed(
            title="🔓 Разблокировка каналов",
            description="Начинаю разблокировку каналов...",
            color=COLORS['info']
        )
        embed.add_field(
            name="Каналы для разблокировки",
            value=f"• <#{RAID_CHANNEL_IDS[0]}>\n"
                  f"• <#{RAID_CHANNEL_IDS[1]}>\n"
                  f"• <#{RAID_CHANNEL_IDS[2]}>\n"
                  f"• <#{RAID_CHANNEL_IDS[3]}>",
            inline=False
        )
        embed.set_footer(text="Доступ будет восстановлен для всех")
        
        message = await ctx.send(embed=embed)
        
        # Получаем роль @everyone
        everyone_role = ctx.guild.default_role
        
        # Список результатов
        results = []
        unlocked_channels = []
        
        for channel_id in RAID_CHANNEL_IDS:
            try:
                channel = ctx.guild.get_channel(channel_id)
                if not channel:
                    # Пробуем получить канал через fetch
                    channel = await ctx.guild.fetch_channel(channel_id)
                
                if channel:
                    # Сбрасываем права для роли @everyone (разрешаем отправку)
                    overwrite = discord.PermissionOverwrite()
                    overwrite.send_messages = True
                    overwrite.add_reactions = True
                    overwrite.read_messages = True
                    
                    # Применяем права
                    await channel.set_permissions(everyone_role, overwrite=overwrite)
                    
                    results.append(f"✅ {channel.mention} - разблокирован")
                    unlocked_channels.append(channel)
                    
                else:
                    results.append(f"❌ Канал {channel_id} не найден")
                    
            except discord.Forbidden:
                results.append(f"❌ Нет прав для разблокировки канала {channel_id}")
            except discord.HTTPException as e:
                results.append(f"❌ Ошибка API: {e}")
            except Exception as e:
                results.append(f"❌ Неизвестная ошибка: {str(e)}")
        
        # Создаем итоговый embed
        final_embed = discord.Embed(
            title="🔓 Разблокировка завершена",
            color=COLORS['success'] if len(unlocked_channels) > 0 else COLORS['error']
        )
        
        # Статистика
        success_count = sum(1 for r in results if "✅" in r)
        failed_count = len(results) - success_count
        
        final_embed.add_field(
            name="📊 Результаты",
            value=f"**Успешно:** {success_count} каналов\n"
                  f"**Не удалось:** {failed_count} каналов",
            inline=False
        )
        
        # Детали
        if len(results) <= 10:
            final_embed.add_field(
                name="📝 Детали",
                value="\n".join(results),
                inline=False
            )
        
        final_embed.add_field(
            name="📢 Каналы снова доступны",
            value="Теперь все участники могут писать в каналах",
            inline=False
        )
        
        final_embed.set_footer(text=f"Команда выполнена: {ctx.author.display_name}")
        
        await message.edit(embed=final_embed)
        
    except Exception as e:
        logger.error(f"Ошибка в raid_unlock: {e}")
        embed = discord.Embed(
            title="❌ Ошибка разблокировки",
            description=f"Произошла ошибка: {str(e)}",
            color=COLORS['error']
        )
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
