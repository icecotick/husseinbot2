import discord
from discord.ext import commands
import os
import logging
import asyncpg
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
DATABASE_URL = os.getenv('DATABASE_URL')
REDIRECT_URI = "https://husseinbot2.onrender.com/oauth2/callback"
PORT = int(os.getenv('PORT', '10000'))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.moderation = True # Для банов
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
                    lock_channel_id BIGINT DEFAULT 0, -- Изменено на ID одного канала из списка
                    log_channel_id BIGINT DEFAULT 0,
                    blacklist_ids TEXT DEFAULT ''
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT, guild_id BIGINT, points INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
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
            query = f'INSERT INTO guild_settings (guild_id, {", ".join(keys)}) VALUES ($1, {", ".join([f"${i+2}" for i in range(len(keys))])}) ON CONFLICT (guild_id) DO UPDATE SET {updates}'
            await conn.execute(query, guild_id, *values)

db = Database()

# --- ВЕБ-ИНТЕРФЕЙС (СТИЛИ) ---
HTML_STYLE = """
<style>
    body { background: #2f3136; color: #dcddde; font-family: 'Whitney', 'Helvetica Neue', Helvetica, Arial, sans-serif; margin: 0; padding: 20px; }
    .container { max-width: 900px; margin: auto; }
    .card { background: #36393f; padding: 30px; border-radius: 12px; box-shadow: 0 10px 20px rgba(0,0,0,0.4); margin-bottom: 20px; border: 1px solid #42454a; }
    h1, h2, h3 { color: #fff; text-align: center; text-transform: uppercase; letter-spacing: 1px; }
    .server-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 20px; margin-top: 30px; }
    .server-item { background: #2f3136; border-radius: 15px; padding: 20px; text-align: center; transition: 0.3s; border: 2px solid transparent; text-decoration: none; color: #fff; }
    .server-item:hover { transform: translateY(-10px); border-color: #5865f2; background: #3c3f44; box-shadow: 0 15px 30px rgba(0,0,0,0.5); }
    .server-item img { width: 80px; height: 80px; border-radius: 50%; margin-bottom: 15px; border: 3px solid #4f545c; }
    input, select, textarea { width: 100%; padding: 12px; margin: 8px 0; background: #202225; color: #ebebeb; border: 1px solid #18191c; border-radius: 5px; box-sizing: border-box; }
    .btn { background: #5865f2; color: white; border: none; padding: 15px; border-radius: 5px; cursor: pointer; width: 100%; font-weight: bold; font-size: 16px; transition: 0.2s; }
    .btn:hover { background: #4752c4; }
    label { font-size: 12px; color: #b9bbbe; font-weight: bold; text-transform: uppercase; }
</style>
"""

async def handle_home(request):
    login_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}&response_type=code&scope=identify%20guilds"
    return web.Response(text=f"{HTML_STYLE}<body><div style='text-align:center; padding-top:150px;'><h1>GNIDA BOT DASHBOARD</h1><p style='color:#b9bbbe'>Best management for your server</p><br><a href='{login_url}' class='btn' style='display:inline-block; width:250px; text-decoration:none;'>LOGIN VIA DISCORD</a></div></body>", content_type='text/html')

async def handle_servers(request):
    token = request.cookies.get('access_token')
    if not token: return web.HTTPFound('/')
    async with aiohttp.ClientSession() as session:
        async with session.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {token}'}) as resp:
            guilds = await resp.json()
    
    html = f"{HTML_STYLE}<body><div class='container'><h1>CHOOSE YOUR SERVER</h1><div class='server-grid'>"
    for g in guilds:
        if (int(g.get('permissions', 0)) & 0x8): # Только для админов
            icon = f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g['icon'] else "https://discord.com/assets/1f0ac534f30559f21f1d1e4e511394b8.svg"
            html += f"<a href='/manage/{g['id']}' class='server-item'><img src='{icon}'><br><b>{g['name']}</b></a>"
    html += "</div></div></body>"
    return web.Response(text=html, content_type='text/html')

async def handle_manage(request):
    guild_id = int(request.match_info['guild_id'])
    guild = bot.get_guild(guild_id)
    if not guild: return web.Response(text="Bot is not in this guild.")
    
    s = await db.get_settings(guild_id) or {}
    roles_opt = "".join([f"<option value='{r.id}' {'selected' if s.get('lock_role_id')==r.id else ''}>{r.name}</option>" for r in sorted(guild.roles, reverse=True) if not r.is_default()])
    v_roles_opt = "".join([f"<option value='{r.id}' {'selected' if s.get('verify_role_id')==r.id else ''}>{r.name}</option>" for r in sorted(guild.roles, reverse=True) if not r.is_default()])
    chan_opt = "".join([f"<option value='{c.id}' {'selected' if s.get('lock_channel_id')==c.id else ''}>#{c.name}</option>" for c in guild.text_channels])
    log_opt = "".join([f"<option value='{c.id}' {'selected' if s.get('log_channel_id')==c.id else ''}>#{c.name}</option>" for c in guild.text_channels])

    html = f"""{HTML_STYLE}<body><div class='container'><div class='card'>
    <h2>SETTINGS: {guild.name}</h2>
    <form action="/save/{guild_id}" method="post">
        <label>Role to Lock:</label><select name="lock_role_id"><option value="0">None</option>{roles_opt}</select>
        <label>Verification Role:</label><select name="verify_role_id"><option value="0">None</option>{v_roles_opt}</select>
        <label>Channel to Lock (!lock):</label><select name="lock_channel_id"><option value="0">Current Channel</option>{chan_opt}</select>
        <label>Blacklist Log Channel:</label><select name="log_channel_id"><option value="0">None</option>{log_opt}</select>
        <label>Admin Role IDs (comma separated):</label><input type="text" name="admin_role_ids" value="{s.get('admin_role_ids', '')}">
        <label>Mod Role IDs (comma separated):</label><input type="text" name="mod_role_ids" value="{s.get('mod_role_ids', '')}">
        <label>Blacklist (IDs for Auto-Ban):</label><textarea name="blacklist_ids" rows="4">{s.get('blacklist_ids', '')}</textarea>
        <button type="submit" class="btn">SAVE CONFIGURATION</button>
    </form>
    </div></div></body>"""
    return web.Response(text=html, content_type='text/html')

async def handle_save(request):
    guild_id = int(request.match_info['guild_id'])
    data = await request.post()
    guild = bot.get_guild(guild_id)
    
    old_s = await db.get_settings(guild_id) or {}
    new_bl_str = data.get('blacklist_ids', '')
    
    await db.save_settings(guild_id,
        lock_role_id=int(data.get('lock_role_id', 0)),
        verify_role_id=int(data.get('verify_role_id', 0)),
        lock_channel_id=int(data.get('lock_channel_id', 0)),
        log_channel_id=int(data.get('log_channel_id', 0)),
        admin_role_ids=data.get('admin_role_ids', ''),
        mod_role_ids=data.get('mod_role_ids', ''),
        blacklist_ids=new_bl_str)

    # ЛОГИКА БАНА (Blacklist)
    if guild:
        old_bl = set(old_s.get('blacklist_ids', '').split(',')) if old_s.get('blacklist_ids') else set()
        new_bl = set(new_bl_str.split(',')) if new_bl_str else set()
        
        to_ban = new_bl - old_bl
        to_unban = old_bl - new_bl
        
        log_chan = guild.get_channel(int(data.get('log_channel_id', 0)))

        for uid in to_ban:
            if uid.strip().isdigit():
                try:
                    user_obj = await bot.fetch_user(int(uid))
                    await guild.ban(user_obj, reason="Added to Gnida Blacklist")
                    if log_chan: await log_chan.send(f"🚫 **BANNED:** {user_obj.name} ({uid})")
                except: pass
        
        for uid in to_unban:
            if uid.strip().isdigit():
                try:
                    user_obj = await bot.fetch_user(int(uid))
                    await guild.unban(user_obj, reason="Removed from Gnida Blacklist")
                    if log_chan: await log_chan.send(f"✅ **UNBANNED:** {user_obj.name} ({uid})")
                except: pass

    return web.HTTPFound(f'/manage/{guild_id}')

# --- КОМАНДЫ БОТА ---

@bot.command()
async def lock(ctx):
    s = await db.get_settings(ctx.guild.id)
    role = ctx.guild.get_role(s['lock_role_id'])
    channel = ctx.guild.get_channel(s['lock_channel_id']) or ctx.channel
    if role:
        await channel.set_permissions(role, send_messages=False)
        await ctx.send(f"🔒 Channel {channel.mention} locked for {role.name}")

@bot.command()
async def unlock(ctx):
    s = await db.get_settings(ctx.guild.id)
    role = ctx.guild.get_role(s['lock_role_id'])
    channel = ctx.guild.get_channel(s['lock_channel_id']) or ctx.channel
    if role:
        await channel.set_permissions(role, send_messages=None)
        await ctx.send(f"🔓 Channel {channel.mention} unlocked.")

# (Остальные команды: addpoints, verify, help — подключаются по аналогии)

async def start_web():
    app = web.Application()
    app.router.add_get('/', handle_home)
    app.router.add_get('/oauth2/callback', handle_callback)
    app.router.add_get('/servers', handle_servers)
    app.router.add_get('/manage/{guild_id}', handle_manage)
    app.router.add_post('/save/{guild_id}', handle_save)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

@bot.event
async def on_ready():
    await db.connect()
    await start_web()
    print(f"Logged in as {bot.user} | GNIDA BOT ACTIVE")

bot.run(TOKEN)
