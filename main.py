import discord
from discord.ext import commands
import os
import logging
import asyncpg
import asyncio
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
            # Основные настройки
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    lock_role_id BIGINT,
                    verify_role_id BIGINT,
                    unverify_role_id BIGINT,
                    admin_role_ids TEXT,
                    blacklist_ids TEXT
                )
            ''')
            # Авто-роли за баллы
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS role_rewards (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    role_id BIGINT,
                    required_points INTEGER
                )
            ''')
            # Пользователи и баллы
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT, guild_id BIGINT, points INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            ''')
        logger.info("✅ База данных PostgreSQL подключена")

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

    async def add_points(self, user_id, guild_id, amount):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                INSERT INTO users (user_id, guild_id, points) VALUES ($1, $2, $3)
                ON CONFLICT (user_id, guild_id) DO UPDATE SET points = users.points + $3
                RETURNING points
            ''', user_id, guild_id, amount)
            return row['points']

db = Database()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def check_auto_roles(member, current_points):
    """Проверяет и выдает роли, если набрано нужное кол-во баллов"""
    async with db.pool.acquire() as conn:
        rewards = await conn.fetch('SELECT role_id, required_points FROM role_rewards WHERE guild_id = $1', member.guild.id)
    
    for reward in rewards:
        if current_points >= reward['required_points']:
            role = member.guild.get_role(reward['role_id'])
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Авто-роль за поинты")
                    logger.info(f"Авто-выдача: {role.name} выдана {member.name}")
                except:
                    pass

# --- ВЕБ-ИНТЕРФЕЙС (DASHBOARD) ---
HTML_STYLE = """
<style>
    body { background: #36393f; color: #dcddde; font-family: 'Segoe UI', sans-serif; margin: 0; padding: 20px; }
    .container { max-width: 800px; margin: auto; }
    .card { background: #2f3136; padding: 25px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
    h2, h3 { color: #fff; margin-top: 0; }
    input, select, textarea { width: 100%; padding: 10px; margin: 10px 0; background: #40444b; color: #fff; border: 1px solid #202225; border-radius: 4px; }
    .btn { background: #5865f2; color: #fff; border: none; padding: 12px 20px; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; font-weight: bold; }
    .btn:hover { background: #4752c4; }
    .reward-item { background: #40444b; padding: 10px; border-radius: 4px; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center; }
    .server-card { background: #2f3136; border-radius: 8px; padding: 15px; margin: 10px; display: inline-block; width: 180px; text-align: center; }
    .server-card img { border-radius: 50%; width: 64px; }
</style>
"""

async def handle_home(request):
    login_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}&response_type=code&scope=identify%20guilds"
    return web.Response(text=f"{HTML_STYLE}<body><div style='text-align:center; padding-top:100px;'><h1>🤖 Бот Панель</h1><a href='{login_url}' class='btn'>Войти через Discord</a></div></body>", content_type='text/html')

async def handle_callback(request):
    code = request.query.get('code')
    async with aiohttp.ClientSession() as session:
        data = {'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT_URI}
        async with session.post('https://discord.com/api/oauth2/token', data=data) as resp:
            token_data = await resp.json()
            access_token = token_data.get('access_token')
    response = web.HTTPFound('/servers')
    response.set_cookie('access_token', access_token, max_age=3600)
    return response

async def handle_servers(request):
    token = request.cookies.get('access_token')
    if not token: return web.HTTPFound('/')
    async with aiohttp.ClientSession() as session:
        async with session.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {token}'}) as resp:
            guilds = await resp.json()
    
    html = f"{HTML_STYLE}<body><div class='container'><h1>Выберите сервер</h1>"
    for g in guilds:
        perms = int(g.get('permissions', 0))
        if (perms & 0x8) or (perms & 0x20):
            icon = f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g['icon'] else "https://discord.com/assets/1f0ac534f30559f21f1d1e4e511394b8.svg"
            html += f"<div class='server-card'><img src='{icon}'><br><br><b>{g['name']}</b><br><br><a href='/manage/{g['id']}' class='btn'>Настроить</a></div>"
    html += "</div></body>"
    return web.Response(text=html, content_type='text/html')

async def handle_manage(request):
    guild_id = int(request.match_info['guild_id'])
    guild = bot.get_guild(guild_id)
    if not guild: return web.Response(text="Бот не на сервере. Сначала добавьте его!")
    
    s = await db.get_settings(guild_id) or {}
    async with db.pool.acquire() as conn:
        rewards = await conn.fetch('SELECT * FROM role_rewards WHERE guild_id = $1 ORDER BY required_points ASC', guild_id)
    
    roles_opt = "".join([f"<option value='{r.id}'>{r.name}</option>" for r in sorted(guild.roles, reverse=True) if not r.is_default()])
    rewards_html = "".join([f"<div class='reward-item'><span>{guild.get_role(r['role_id']).name if guild.get_role(r['role_id']) else 'Удалена'} — <b>{r['required_points']}</b> поинтов</span> <a href='/del_reward/{guild_id}/{r['id']}' style='color:#ed4245; text-decoration:none;'>Удалить</a></div>" for r in rewards])

    html = f"""{HTML_STYLE}<body><div class='container'>
    <div class='card'>
        <h2>⚙️ Настройки {guild.name}</h2>
        <form action="/save/{guild_id}" method="post">
            <label>Роль блокировки (!lock):</label>
            <select name="lock_role_id"><option value="0">Не выбрано</option>{roles_opt.replace(f"value='{s.get('lock_role_id')}'", f"selected value='{s.get('lock_role_id')}'")}</select>
            
            <label>Роль верификации (!verify):</label>
            <select name="verify_role_id"><option value="0">Не выбрано</option>{roles_opt.replace(f"value='{s.get('verify_role_id')}'", f"selected value='{s.get('verify_role_id')}'")}</select>
            
            <label>Черный список (ID пользователей через запятую):</label>
            <textarea name="blacklist_ids" rows="2">{s.get('blacklist_ids', '')}</textarea>
            
            <button type="submit" name="action" value="save_general" class="btn">Сохранить основные настройки</button>
            <hr>
            <h3>🏆 Авто-роли за баллы</h3>
            <label>Выберите роль:</label><select name="new_role_id">{roles_opt}</select>
            <label>Нужно баллов:</label><input type="number" name="new_points" placeholder="1000">
            <button type="submit" name="action" value="add_reward" class="btn" style="background:#3ba55c;">Добавить роль</button>
        </form>
        <div style="margin-top:20px;">{rewards_html}</div>
    </div>
    <a href="/servers" style="color:#aaa;">← Назад к списку</a>
    </div></body>"""
    return web.Response(text=html, content_type='text/html')

async def handle_save(request):
    guild_id = int(request.match_info['guild_id'])
    data = await request.post()
    if data.get('action') == 'save_general':
        await db.save_settings(guild_id, 
            lock_role_id=int(data['lock_role_id']),
            verify_role_id=int(data['verify_role_id']),
            blacklist_ids=data['blacklist_ids'])
    elif data.get('action') == 'add_reward':
        async with db.pool.acquire() as conn:
            await conn.execute('INSERT INTO role_rewards (guild_id, role_id, required_points) VALUES ($1, $2, $3)',
                             guild_id, int(data['new_role_id']), int(data['new_points']))
    return web.HTTPFound(f'/manage/{guild_id}')

async def handle_del_reward(request):
    guild_id = int(request.match_info['guild_id'])
    rid = int(request.match_info['id'])
    async with db.pool.acquire() as conn:
        await conn.execute('DELETE FROM role_rewards WHERE id = $1 AND guild_id = $2', rid, guild_id)
    return web.HTTPFound(f'/manage/{guild_id}')

# --- КОМАНДЫ БОТА ---

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: return
    # Проверка черного списка
    s = await db.get_settings(message.guild.id)
    if s and s['blacklist_ids'] and str(message.author.id) in s['blacklist_ids'].split(','):
        return
    await bot.process_commands(message)

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="📚 Команды бота", color=0x5865F2)
    embed.description = f"Настройка ролей и ЧС: [Панель управления]({REDIRECT_URI.replace('/oauth2/callback', '')})"
    embed.add_field(name="🔒 Модерация", value="`!lock` — закрыть чат\n`!unlock` — открыть чат", inline=False)
    embed.add_field(name="💰 Баллы", value="`!addpoints @user <кол-во>`\n`!removepoints @user <кол-во>`", inline=False)
    embed.add_field(name="✅ Верификация", value="`!verify @user` — выдать роль верификации", inline=False)
    embed.set_footer(text="Роли за поинты выдаются автоматически.")
    await ctx.send(embed=embed)

@bot.command()
async def addpoints(ctx, member: discord.Member, amount: int):
    if not ctx.author.guild_permissions.administrator: return
    new_pts = await db.add_points(member.id, ctx.guild.id, amount)
    await ctx.send(f"✅ {member.mention} +**{amount}** баллов. (Всего: **{new_pts}**)")
    await check_auto_roles(member, new_pts)

@bot.command()
async def removepoints(ctx, member: discord.Member, amount: int):
    if not ctx.author.guild_permissions.administrator: return
    new_pts = await db.add_points(member.id, ctx.guild.id, -amount)
    await ctx.send(f"❌ {member.mention} -**{amount}** баллов.")

@bot.command()
async def lock(ctx):
    s = await db.get_settings(ctx.guild.id)
    if not s or not s['lock_role_id']: return await ctx.send("❌ Роль не настроена в панели!")
    role = ctx.guild.get_role(s['lock_role_id'])
    await ctx.channel.set_permissions(role, send_messages=False)
    await ctx.send(f"🔒 Канал заблокирован для {role.name}")

@bot.command()
async def unlock(ctx):
    s = await db.get_settings(ctx.guild.id)
    if not s or not s['lock_role_id']: return await ctx.send("❌ Роль не настроена!")
    role = ctx.guild.get_role(s['lock_role_id'])
    await ctx.channel.set_permissions(role, send_messages=None)
    await ctx.send(f"🔓 Канал разблокирован для {role.name}")

@bot.command()
async def verify(ctx, member: discord.Member):
    s = await db.get_settings(ctx.guild.id)
    if not s or not s['verify_role_id']: return await ctx.send("❌ Роль верификации не настроена!")
    role = ctx.guild.get_role(s['verify_role_id'])
    await member.add_roles(role)
    await ctx.send(f"✅ {member.mention} верифицирован!")

# --- ЗАПУСК ---
async def start_web():
    app = web.Application()
    app.router.add_get('/', handle_home)
    app.router.add_get('/oauth2/callback', handle_callback)
    app.router.add_get('/servers', handle_servers)
    app.router.add_get('/manage/{guild_id}', handle_manage)
    app.router.add_post('/save/{guild_id}', handle_save)
    app.router.add_get('/del_reward/{guild_id}/{id}', handle_del_reward)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

@bot.event
async def on_ready():
    await db.connect()
    await start_web()
    logger.info(f"🚀 Бот {bot.user} запущен на порту {PORT}")

bot.run(TOKEN)
