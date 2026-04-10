import discord
from discord.ext import commands
import os
import logging
import asyncpg
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv('DISCORD_TOKEN')
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
DATABASE_URL = os.getenv('DATABASE_URL')
REDIRECT_URI = "https://husseinbot2.onrender.com/oauth2/callback"
PORT = int(os.getenv('PORT', '10000'))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
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
                    lock_channel_ids TEXT DEFAULT '',
                    log_channel_id BIGINT DEFAULT 0,
                    blacklist_ids TEXT DEFAULT ''
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS role_rewards (
                    id SERIAL PRIMARY KEY, guild_id BIGINT, role_id BIGINT, required_points INTEGER
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT, guild_id BIGINT, points INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            ''')
        logger.info("✅ База данных подключена")

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

# --- ПРОВЕРКА ПРАВ ---
def check_permissions(member, s, p_type="mod"):
    if member.guild_permissions.administrator: return True
    if not s: return False
    
    # Ищем ID ролей в строке из БД
    target_ids = s['admin_role_ids'] if p_type == "admin" else s.get('mod_role_ids', '')
    if not target_ids: return False
    
    user_role_ids = [str(r.id) for r in member.roles]
    return any(rid.strip() in target_ids.split(',') for rid in user_role_ids)

# --- ВЕБ-ИНТЕРФЕЙС ---
HTML_STYLE = """
<style>
    body { background: #36393f; color: #dcddde; font-family: 'Segoe UI', sans-serif; padding: 20px; }
    .card { background: #2f3136; padding: 25px; border-radius: 10px; max-width: 800px; margin: auto; box-shadow: 0 8px 16px rgba(0,0,0,0.3); }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    h2, h3 { color: #fff; border-bottom: 1px solid #4f545c; padding-bottom: 8px; }
    label { font-size: 0.85em; font-weight: bold; display: block; margin-top: 10px; color: #b9bbbe; }
    input, select, textarea { width: 100%; padding: 10px; margin-top: 5px; background: #40444b; color: #fff; border: 1px solid #202225; border-radius: 4px; }
    .btn { background: #5865f2; color: #fff; border: none; padding: 12px; border-radius: 4px; cursor: pointer; width: 100%; font-weight: bold; margin-top: 15px; }
    .btn:hover { background: #4752c4; }
    .footer { text-align: center; margin-top: 20px; font-size: 0.8em; color: #72767d; }
</style>
"""

async def handle_manage(request):
    guild_id = int(request.match_info['guild_id'])
    guild = bot.get_guild(guild_id)
    if not guild: return web.Response(text="Бот не на сервере!")
    
    s = await db.get_settings(guild_id) or {}
    
    # Генерация списков
    roles_opt = "".join([f"<option value='{r.id}' {'selected' if s.get('lock_role_id')==r.id else ''}>{r.name}</option>" for r in sorted(guild.roles, reverse=True) if not r.is_default()])
    roles_opt_v = "".join([f"<option value='{r.id}' {'selected' if s.get('verify_role_id')==r.id else ''}>{r.name}</option>" for r in sorted(guild.roles, reverse=True) if not r.is_default()])
    log_chan_opt = "".join([f"<option value='{c.id}' {'selected' if s.get('log_channel_id')==c.id else ''}>#{c.name}</option>" for c in guild.text_channels])

    html = f"""{HTML_STYLE}<body><div class='container'><div class='card'>
    <h2>🛡️ Настройки {guild.name}</h2>
    <form action="/save/{guild_id}" method="post">
        <div class="grid">
            <div>
                <h3>🎭 Основные роли</h3>
                <label>Роль блокировки (!lock):</label>
                <select name="lock_role_id"><option value="0">Не выбрано</option>{roles_opt}</select>
                <label>Роль верификации:</label>
                <select name="verify_role_id"><option value="0">Не выбрано</option>{roles_opt_v}</select>
            </div>
            <div>
                <h3>🔑 Права (ID ролей через запятую)</h3>
                <label>Модераторы:</label>
                <input type="text" name="mod_role_ids" value="{s.get('mod_role_ids', '')}">
                <label>Администраторы:</label>
                <input type="text" name="admin_role_ids" value="{s.get('admin_role_ids', '')}">
            </div>
        </div>

        <h3>📝 Каналы и Логирование</h3>
        <label>Каналы для блокировки (ID через запятую):</label>
        <input type="text" name="lock_channel_ids" value="{s.get('lock_channel_ids', '')}" placeholder="ID1, ID2, ID3">
        
        <label>Канал для логов Черного списка:</label>
        <select name="log_channel_id"><option value="0">Не выбрано</option>{log_chan_opt}</select>

        <h3>🚫 Черный список</h3>
        <label>ID пользователей (через запятую):</label>
        <textarea name="blacklist_ids" rows="3">{s.get('blacklist_ids', '')}</textarea>

        <button type="submit" class="btn">💾 Сохранить изменения</button>
    </form>
    </div><div class='footer'><a href="/servers" style="color:inherit;">← Назад к списку серверов</a></div></div></body>"""
    return web.Response(text=html, content_type='text/html')

async def handle_save(request):
    guild_id = int(request.match_info['guild_id'])
    data = await request.post()
    
    def to_int(val):
        return int(val) if val and val.isdigit() else 0

    old_s = await db.get_settings(guild_id) or {}
    new_bl = data.get('blacklist_ids', '')

    await db.save_settings(guild_id,
        lock_role_id=to_int(data.get('lock_role_id')),
        verify_role_id=to_int(data.get('verify_role_id')),
        mod_role_ids=data.get('mod_role_ids', ''),
        admin_role_ids=data.get('admin_role_ids', ''),
        lock_channel_ids=data.get('lock_channel_ids', ''),
        log_channel_id=to_int(data.get('log_channel_id')),
        blacklist_ids=new_bl
    )

    # Логирование изменения блеклиста
    if new_bl != old_s.get('blacklist_ids', ''):
        log_id = to_int(data.get('log_channel_id'))
        if log_id:
            chan = bot.get_channel(log_id)
            if chan:
                await chan.send(f"🛰️ **Обновление черного списка в панели:**\nНовые ID: `{new_bl if new_bl else 'Список пуст'}`")

    return web.HTTPFound(f'/manage/{guild_id}')

# --- КОМАНДЫ БОТА ---

@bot.command()
async def lock(ctx):
    s = await db.get_settings(ctx.guild.id)
    if not check_permissions(ctx.author, s, "mod"): return await ctx.send("❌ У вас нет прав модератора.")
    
    role = ctx.guild.get_role(s['lock_role_id'])
    if not role: return await ctx.send("❌ Роль для блокировки не настроена.")

    c_ids = s.get('lock_channel_ids', '').split(',')
    targets = [ctx.guild.get_channel(int(i.strip())) for i in c_ids if i.strip().isdigit()]
    if not targets: targets = [ctx.channel]

    for ch in targets:
        if ch: await ch.set_permissions(role, send_messages=False)
    await ctx.send(f"🔒 Закрыт доступ к {len(targets)} кан. для {role.name}")

@bot.command()
async def verify(ctx, member: discord.Member):
    s = await db.get_settings(ctx.guild.id)
    if not check_permissions(ctx.author, s, "mod"): return
    
    role = ctx.guild.get_role(s['verify_role_id'])
    if role:
        await member.add_roles(role)
        await ctx.send(f"✅ {member.mention} верифицирован.")

@bot.command()
async def addpoints(ctx, member: discord.Member, amount: int):
    s = await db.get_settings(ctx.guild.id)
    if not check_permissions(ctx.author, s, "admin"): return await ctx.send("❌ Только для администраторов.")
    
    new_pts = await db.add_points(member.id, ctx.guild.id, amount)
    await ctx.send(f"💰 {member.mention}: +{amount} баллов (Всего: {new_pts})")

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="Команды бота", color=0x5865f2)
    embed.add_field(name="🛡️ Модерация", value="`!lock`, `!unlock`, `!verify`", inline=False)
    embed.add_field(name="👑 Админ", value="`!addpoints`, `!removepoints`", inline=False)
    embed.description = f"🔗 [Панель управления]({REDIRECT_URI.replace('/oauth2/callback', '')})"
    await ctx.send(embed=embed)

# --- ВСЕ ОСТАЛЬНЫЕ РОУТЫ (Home, Callback, Servers) ---
async def handle_home(request):
    login_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}&response_type=code&scope=identify%20guilds"
    return web.Response(text=f"{HTML_STYLE}<body style='text-align:center; padding-top:100px;'><h1>Robot Dashboard</h1><a href='{login_url}' class='btn' style='width:200px;'>Войти</a></body>", content_type='text/html')

async def handle_callback(request):
    code = request.query.get('code')
    async with aiohttp.ClientSession() as session:
        data = {'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT_URI}
        async with session.post('https://discord.com/api/oauth2/token', data=data) as resp:
            token_data = await resp.json()
            access_token = token_data.get('access_token')
    res = web.HTTPFound('/servers')
    res.set_cookie('access_token', access_token, max_age=3600)
    return res

async def handle_servers(request):
    token = request.cookies.get('access_token')
    if not token: return web.HTTPFound('/')
    async with aiohttp.ClientSession() as session:
        async with session.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {token}'}) as resp:
            guilds = await resp.json()
    html = f"{HTML_STYLE}<body><div class='card'><h2>Ваши серверы</h2>"
    for g in guilds:
        if (int(g.get('permissions', 0)) & 0x8) or (int(g.get('permissions', 0)) & 0x20):
            html += f"<p>• {g['name']} <a href='/manage/{g['id']}' style='color:#5865f2; float:right;'>Настроить</a></p>"
    html += "</div></body>"
    return web.Response(text=html, content_type='text/html')

@bot.event
async def on_ready():
    await db.connect()
    app = web.Application()
    app.router.add_get('/', handle_home)
    app.router.add_get('/oauth2/callback', handle_callback)
    app.router.add_get('/servers', handle_servers)
    app.router.add_get('/manage/{guild_id}', handle_manage)
    app.router.add_post('/save/{guild_id}', handle_save)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logger.info(f"🚀 Бот запущен: {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: return
    s = await db.get_settings(message.guild.id)
    if s and s['blacklist_ids'] and str(message.author.id) in s['blacklist_ids'].split(','):
        return
    await bot.process_commands(message)

bot.run(TOKEN)
