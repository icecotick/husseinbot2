import discord
from discord.ext import commands
import os
import logging
import asyncpg
import asyncio
from aiohttp import web
import aiohttp
from dotenv import load_dotenv

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
DATABASE_URL = os.getenv('DATABASE_URL')
# URL для редиректа (замени на свой, если изменится)
REDIRECT_URI = "https://husseinbot2.onrender.com/oauth2/callback"
PORT = int(os.getenv('PORT', '10000'))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.moderation = True  # КРИТИЧНО ДЛЯ БАНОВ

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- БАЗА ДАННЫХ ---
class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    lock_role_id BIGINT DEFAULT 0,
                    verify_role_id BIGINT DEFAULT 0,
                    mod_role_ids TEXT DEFAULT '',
                    admin_role_ids TEXT DEFAULT '',
                    lock_channel_id BIGINT DEFAULT 0,
                    log_channel_id BIGINT DEFAULT 0,
                    blacklist_ids TEXT DEFAULT ''
                )
            ''')

    async def get_settings(self, guild_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow('SELECT * FROM guild_settings WHERE guild_id = $1', guild_id)

    async def save_settings(self, guild_id, **kwargs):
        async with self.pool.acquire() as conn:
            keys = list(kwargs.keys())
            values = list(kwargs.values())
            updates = ", ".join([f"{k} = EXCLUDED.{k}" for k in keys])
            query = f'''
                INSERT INTO guild_settings (guild_id, {", ".join(keys)}) 
                VALUES ($1, {", ".join([f"${i+2}" for i in range(len(keys))])}) 
                ON CONFLICT (guild_id) DO UPDATE SET {updates}
            '''
            await conn.execute(query, guild_id, *values)

db = Database()

# --- СТИЛИ ДАШБОРДА ---
HTML_STYLE = """
<style>
    body { background: #2f3136; color: #dcddde; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; margin: 0; padding: 20px; }
    .container { max-width: 1000px; margin: auto; }
    .card { background: #36393f; padding: 30px; border-radius: 12px; box-shadow: 0 10px 20px rgba(0,0,0,0.3); margin-bottom: 20px; border: 1px solid #42454a; }
    h1 { color: #fff; text-align: center; font-size: 2.5em; text-shadow: 2px 2px 4px #000; }
    
    /* Сетка серверов */
    .server-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; margin-top: 30px; }
    .server-card { 
        background: #2f3136; border-radius: 15px; padding: 20px; text-align: center; 
        transition: all 0.3s ease; border: 2px solid #42454a; text-decoration: none; color: #fff; display: block;
    }
    .server-card:hover { 
        transform: translateY(-10px); border-color: #5865f2; background: #3c3f44; 
        box-shadow: 0 15px 30px rgba(0,0,0,0.5); 
    }
    .server-card img { width: 90px; height: 90px; border-radius: 50%; margin-bottom: 15px; border: 3px solid #4f545c; }
    .server-card b { font-size: 1.1em; display: block; margin-top: 5px; }

    /* Формы */
    input, select, textarea { 
        width: 100%; padding: 12px; margin: 10px 0; background: #202225; color: #ebebeb; 
        border: 1px solid #18191c; border-radius: 6px; font-size: 14px; 
    }
    label { font-size: 11px; color: #b9bbbe; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; }
    .btn { 
        background: #5865f2; color: white; border: none; padding: 15px; border-radius: 6px; 
        cursor: pointer; width: 100%; font-weight: bold; font-size: 16px; transition: 0.2s; 
    }
    .btn:hover { background: #4752c4; }
</style>
"""

# --- ОБРАБОТЧИКИ WEB ---

async def handle_home(request):
    login_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}&response_type=code&scope=identify%20guilds"
    return web.Response(text=f"""
    {HTML_STYLE}
    <body>
        <div style='text-align:center; padding-top:150px;'>
            <h1>GNIDA BOT DASHBOARD</h1>
            <p style='color:#b9bbbe'>Управление системой баллов и модерацией</p><br>
            <a href='{login_url}' class='btn' style='display:inline-block; width:280px; text-decoration:none;'>АВТОРИЗАЦИЯ ЧЕРЕЗ DISCORD</a>
        </div>
    </body>""", content_type='text/html')

async def handle_servers(request):
    token = request.cookies.get('access_token')
    if not token: return web.HTTPFound('/')
    
    async with aiohttp.ClientSession() as session:
        async with session.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {token}'}) as resp:
            guilds = await resp.json()
    
    html = f"{HTML_STYLE}<body><div class='container'><h1>ВАШИ СЕРВЕРЫ</h1><div class='server-grid'>"
    for g in guilds:
        # Проверка прав администратора (0x8)
        if (int(g.get('permissions', 0)) & 0x8):
            icon = f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g['icon'] else "https://discord.com/assets/1f0ac534f30559f21f1d1e4e511394b8.svg"
            html += f"""
            <a href='/manage/{g['id']}' class='server-card'>
                <img src='{icon}'>
                <b>{g['name']}</b>
            </a>"""
    html += "</div></div></body>"
    return web.Response(text=html, content_type='text/html')

async def handle_manage(request):
    guild_id = int(request.match_info['guild_id'])
    guild = bot.get_guild(guild_id)
    if not guild: return web.Response(text="Бот не найден на этом сервере. Пригласите его сначала.")
    
    s = await db.get_settings(guild_id) or {}
    
    # Генерация списков для Select
    roles_opt = "".join([f"<option value='{r.id}' {'selected' if s.get('lock_role_id')==r.id else ''}>{r.name}</option>" for r in sorted(guild.roles, reverse=True) if not r.is_default()])
    v_roles_opt = "".join([f"<option value='{r.id}' {'selected' if s.get('verify_role_id')==r.id else ''}>{r.name}</option>" for r in sorted(guild.roles, reverse=True) if not r.is_default()])
    
    # Выпадающий список КАНАЛОВ для Lock
    chan_opt = "".join([f"<option value='{c.id}' {'selected' if s.get('lock_channel_id')==c.id else ''}># {c.name}</option>" for c in guild.text_channels])
    
    # Выпадающий список КАНАЛОВ для Логов
    log_opt = "".join([f"<option value='{c.id}' {'selected' if s.get('log_channel_id')==c.id else ''}># {c.name}</option>" for c in guild.text_channels])

    html = f"""
    {HTML_STYLE}
    <body>
        <div class='container'>
            <div class='card'>
                <h2>Настройки: {guild.name}</h2>
                <form action="/save/{guild_id}" method="post">
                    <label>Роль для блокировки (!lock):</label>
                    <select name="lock_role_id"><option value="0">Не выбрано</option>{roles_opt}</select>
                    
                    <label>Канал для блокировки по умолчанию:</label>
                    <select name="lock_channel_id"><option value="0">Текущий канал</option>{chan_opt}</select>

                    <label>Роль верификации:</label>
                    <select name="verify_role_id"><option value="0">Не выбрано</option>{v_roles_opt}</select>
                    
                    <label>Канал для логов блеклиста:</label>
                    <select name="log_channel_id"><option value="0">Без логов</option>{log_opt}</select>

                    <label>Blacklist (ID пользователей через запятую - БУДУТ ЗАБАНЕНЫ):</label>
                    <textarea name="blacklist_ids" rows="5" placeholder="123456, 789012...">{s.get('blacklist_ids', '')}</textarea>

                    <button type="submit" class="btn">СОХРАНИТЬ И ПРИМЕНИТЬ</button>
                </form>
            </div>
        </div>
    </body>"""
    return web.Response(text=html, content_type='text/html')

async def handle_save(request):
    guild_id = int(request.match_info['guild_id'])
    data = await request.post()
    guild = bot.get_guild(guild_id)
    
    # Получаем старые настройки для сравнения блеклиста
    old_s = await db.get_settings(guild_id) or {}
    new_bl_str = data.get('blacklist_ids', '').replace(' ', '')
    
    await db.save_settings(guild_id,
        lock_role_id=int(data.get('lock_role_id', 0)),
        verify_role_id=int(data.get('verify_role_id', 0)),
        lock_channel_id=int(data.get('lock_channel_id', 0)),
        log_channel_id=int(data.get('log_channel_id', 0)),
        blacklist_ids=new_bl_str)

    # ЛОГИКА АВТО-БАНА (Blacklist)
    if guild:
        old_bl = set(old_s.get('blacklist_ids', '').split(',')) if old_s.get('blacklist_ids') else set()
        new_bl = set(new_bl_str.split(',')) if new_bl_str else set()
        
        to_ban = new_bl - old_bl
        to_unban = old_bl - new_bl
        
        log_chan = guild.get_channel(int(data.get('log_channel_id', 0)))

        # Бан новых в списке
        for uid in to_ban:
            if uid.isdigit():
                try:
                    user_obj = await bot.fetch_user(int(uid))
                    await guild.ban(user_obj, reason="GNIDA Blacklist: Добавлен через дашборд")
                    if log_chan: await log_chan.send(f"🚫 **БАН:** {user_obj.name} (`{uid}`) добавлен в блеклист.")
                except Exception as e:
                    logger.error(f"Ошибка бана {uid}: {e}")
        
        # Разбан удаленных из списка
        for uid in to_unban:
            if uid.isdigit():
                try:
                    user_obj = await bot.fetch_user(int(uid))
                    await guild.unban(user_obj, reason="GNIDA Blacklist: Удален из дашборда")
                    if log_chan: await log_chan.send(f"✅ **РАЗБАН:** {user_obj.name} (`{uid}`) удален из блеклиста.")
                except: pass

    return web.HTTPFound(f'/manage/{guild_id}')

# (Добавь здесь обработчик callback для OAuth2, чтобы сохранять access_token в куки)

# --- КОМАНДЫ БОТА ---

@bot.command()
async def lock(ctx):
    s = await db.get_settings(ctx.guild.id)
    if not s: return
    
    role = ctx.guild.get_role(s['lock_role_id'])
    # Если в настройках выбран канал, лочим его, иначе текущий
    channel = ctx.guild.get_channel(s['lock_channel_id']) or ctx.channel
    
    if role and channel:
        await channel.set_permissions(role, send_messages=False)
        await ctx.send(f"🔒 Канал {channel.mention} закрыт для роли **{role.name}**")

@bot.command()
async def unlock(ctx):
    s = await db.get_settings(ctx.guild.id)
    if not s: return
    
    role = ctx.guild.get_role(s['lock_role_id'])
    channel = ctx.guild.get_channel(s['lock_channel_id']) or ctx.channel
    
    if role and channel:
        await channel.set_permissions(role, send_messages=None)
        await ctx.send(f"🔓 Канал {channel.mention} снова открыт.")

# --- ЗАПУСК ---

async def start_web():
    app = web.Application()
    app.router.add_get('/', handle_home)
    app.router.add_get('/servers', handle_servers)
    app.router.add_get('/manage/{guild_id}', handle_manage)
    app.router.add_post('/save/{guild_id}', handle_save)
    # app.router.add_get('/oauth2/callback', handle_callback) # Добавь свою логику callback
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

@bot.event
async def on_ready():
    await db.connect()
    await start_web()
    print(f"[{bot.user}] GNIDA BOT СИСТЕМА ЗАПУЩЕНА")

bot.run(TOKEN)
