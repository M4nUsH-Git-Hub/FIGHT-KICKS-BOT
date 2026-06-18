"""
Discord Ping Backup Bot — PINGER ONLY
======================================
Archivia automaticamente tutti i messaggi con @everyone, @here o menzioni di ruolo
inviati da owner, bot autorizzati e staff nel canale #ping-backup.

Comandi slash:
  /pingbackup setup      — imposta il canale di backup
  /pingbackup addchannel — aggiunge un canale aperto (archivia ping di chiunque)
  /pingbackup rmchannel  — rimuove un canale aperto
  /pingbackup config     — mostra la configurazione attuale
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import urllib.request
import re
import random
from datetime import datetime, timezone

# ── Configurazione persistente via GitHub Gist ────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
GITHUB_TOKEN = "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"
GITHUB_GIST_ID = "6cda801fb93b5515a36bfab543a5d0e1"
TRANSCRIPT_GIST_ID = "6ca31faba6736a24f456685d0408335a"

_config_cache = {}


def load_config() -> dict:
    """Carica config da GitHub Gist, fallback su file locale."""
    global _config_cache
    print(f"🔧 load_config chiamato — Gist ID: {GITHUB_GIST_ID}", flush=True)
    try:
        req = urllib.request.Request(
            f"https://api.github.com/gists/{GITHUB_GIST_ID}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            files = result.get("files", {})
            file_key = next((k for k in files if k.endswith(".json") or k == "config.json"), next(iter(files), None))
            if not file_key:
                raise KeyError("Nessun file trovato nel Gist")
            raw = files[file_key]["content"]
            data = json.loads(raw)
            _config_cache = data
            # Backup locale
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return data
    except Exception as e:
        print(f"⚠️ Gist load fallito ({e}), uso file locale")
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            _config_cache = data
            return data
    return _config_cache or {}


def save_config(data: dict):
    """Salva config su GitHub Gist e file locale come backup."""
    global _config_cache
    _config_cache = data

    # Salva file locale
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_FILE)

    # Aggiorna Gist
    try:
        payload = json.dumps({
            "files": {
                "config.json": {
                    "content": json.dumps(data, indent=2, ensure_ascii=False)
                }
            }
        }).encode()
        req = urllib.request.Request(
            f"https://api.github.com/gists/{GITHUB_GIST_ID}",
            data=payload,
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
            method="PATCH"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            print("✅ Config salvata su Gist")
    except Exception as e:
        print(f"⚠️ Gist save fallito ({e}), salvato solo in locale")




def save_transcript_to_gist(filename: str, html_content: str) -> str | None:
    """Salva un transcript HTML nel Gist dedicato e restituisce il link raw."""
    try:
        payload = json.dumps({
            "files": {
                filename: {
                    "content": html_content
                }
            }
        }).encode()
        req = urllib.request.Request(
            f"https://api.github.com/gists/{TRANSCRIPT_GIST_ID}",
            data=payload,
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
            method="PATCH"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            raw_url = result["files"][filename]["raw_url"]
            print(f"✅ Transcript salvato su Gist: {raw_url}")
            return raw_url
    except Exception as e:
        print(f"⚠️ Gist transcript save fallito: {e}")
        return None



async def _load_transcripts_from_gist():
    """Ricarica i transcript salvati su Gist in memoria al riavvio del bot."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.github.com/gists/{TRANSCRIPT_GIST_ID}",
                headers={
                    "Authorization": f"token {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github+json",
                }
            ) as resp:
                result = await resp.json()
                files = result.get("files", {})
                for fname, fdata in files.items():
                    if fname.endswith(".html"):
                        # Estrai token dal nome file: transcript-deal-0001-TOKEN.html
                        parts = fname.replace(".html", "").rsplit("-", 1)
                        if len(parts) == 2:
                            token = parts[1]
                            _transcript_store[token] = fdata.get("content", "")
                print(f"✅ {len(_transcript_store)} transcript ricaricati dal Gist")
    except Exception as e:
        print(f"⚠️ Errore ricarica transcript: {e}")

def get_guild_config(guild_id: int) -> dict:
    cfg = load_config()
    key = str(guild_id)
    if key not in cfg:
        cfg[key] = {
            "backup_channel_id": None,
            "staff_role_ids": [],
            "allowed_bot_ids": [],
            "open_channel_ids": [],
        }
        save_config(cfg)
    return cfg[key]


def update_guild_config(guild_id: int, partial: dict):
    cfg = load_config()
    key = str(guild_id)
    if key not in cfg:
        cfg[key] = {
            "backup_channel_id": None,
            "staff_role_ids": [],
            "allowed_bot_ids": [],
            "open_channel_ids": [],
        }
    cfg[key].update(partial)
    save_config(cfg)


# ── Setup intents ──────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ── Utility ───────────────────────────────────────────────────────────────────

def is_authorized(message: discord.Message, guild_cfg: dict) -> bool:
    author = message.author
    guild = message.guild
    if guild is None:
        return False
    if author.id == guild.owner_id:
        return True
    if author.bot and author.id in guild_cfg.get("allowed_bot_ids", []):
        return True
    if isinstance(author, discord.Member):
        member_role_ids = {r.id for r in author.roles}
        if member_role_ids & set(guild_cfg.get("staff_role_ids", [])):
            return True
    return False


def has_ping(message: discord.Message) -> bool:
    if message.mention_everyone:
        return True
    if message.role_mentions:
        return True
    if re.search(r'<@&\d+>', message.content):
        return True
    return False


def count_mentions_this_week(guild_id: int, role_label: str) -> int:
    cfg = load_config()
    key = str(guild_id)
    history = cfg.get(key, {}).get("mention_history", {})
    role_history = history.get(role_label, [])
    now = datetime.now(timezone.utc)
    week_ago = now.timestamp() - 7 * 24 * 3600
    return sum(1 for t in role_history if t >= week_ago)


def record_mention(guild_id: int, role_label: str):
    cfg = load_config()
    key = str(guild_id)
    if key not in cfg:
        cfg[key] = {}
    if "mention_history" not in cfg[key]:
        cfg[key]["mention_history"] = {}
    history = cfg[key]["mention_history"]
    if role_label not in history:
        history[role_label] = []
    now = datetime.now(timezone.utc)
    history[role_label].append(now.timestamp())
    week_ago = now.timestamp() - 30 * 24 * 3600
    history[role_label] = [t for t in history[role_label] if t >= week_ago]
    save_config(cfg)


def build_embed(message: discord.Message) -> discord.Embed:
    author = message.author
    channel = message.channel

    ping_types = []
    if message.mention_everyone:
        if "@here" in message.content:
            ping_types.append("@here")
        if "@everyone" in message.content:
            ping_types.append("@everyone")
        if not ping_types:
            ping_types.append("@everyone/@here")
    for role in message.role_mentions:
        ping_types.append(f"@{role.name}")
    mentioned_role_ids = {r.id for r in message.role_mentions}
    for match in re.finditer(r'<@&(\d+)>', message.content):
        rid = int(match.group(1))
        if rid not in mentioned_role_ids:
            role_obj = message.guild.get_role(rid)
            name = role_obj.name if role_obj else str(rid)
            ping_types.append(f"@{name}")

    ping_label = ", ".join(ping_types) if ping_types else "Role mention"

    record_mention(message.guild.id, ping_label)
    weekly_count = count_mentions_this_week(message.guild.id, ping_label)

    # Costruisci la stringa delle menzioni cliccabili
    mention_tags = []
    if message.mention_everyone:
        if "@here" in message.content:
            mention_tags.append("@here")
        if "@everyone" in message.content:
            mention_tags.append("@everyone")
        if not mention_tags:
            mention_tags.append("@everyone")
    for role in message.role_mentions:
        mention_tags.append(role.mention)
    mentioned_role_ids = {r.id for r in message.role_mentions}
    for match in re.finditer(r'<@&(\d+)>', message.content):
        rid = int(match.group(1))
        if rid not in mentioned_role_ids:
            mention_tags.append(f"<@&{rid}>")

    mention_str = ", ".join(mention_tags) if mention_tags else ping_label

    embed = discord.Embed(
        title="New Ping Detected",
        color=discord.Color(0x6B6B6B),
        timestamp=message.created_at,
    )

    embed.add_field(name="Pinged Role", value=mention_str, inline=True)
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Message", value=f"[Click here]({message.jump_url})", inline=True)

    images = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
    if images:
        embed.set_image(url=images[0].url)
    else:
        for msg_embed in message.embeds:
            if msg_embed.image and msg_embed.image.url:
                embed.set_image(url=msg_embed.image.url)
                break
            elif msg_embed.thumbnail and msg_embed.thumbnail.url:
                embed.set_image(url=msg_embed.thumbnail.url)
                break

    embed.set_footer(
        text="Ping Fetcher",
        icon_url="https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS/main/SCURO.png"
    )

    return embed


# ── Evento: on_ready ──────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online come {bot.user} (ID: {bot.user.id})", flush=True)
    print("Comandi slash sincronizzati globalmente.", flush=True)
    # Test connessione Gist
    cfg = load_config()
    print(f"📦 Config caricata — {len(cfg.get('wtb_webhooks', []))} webhook", flush=True)
    # Avvia task giveaway
    if not giveaway_check.is_running():
        giveaway_check.start()
    # Avvia member counter
    if not update_member_count.is_running():
        update_member_count.start()
    # Carica cache inviti
    for guild in bot.guilds:
        await _build_invite_cache(guild)
    # Ricarica transcript dal Gist in memoria
    await _load_transcripts_from_gist()
    # Registra views persistenti ticket (sopravvivono ai restart)
    bot.add_view(CreateTicketView("support"))
    bot.add_view(CreateDealTicketView())
    bot.add_view(TicketControlView("support"))
    bot.add_view(TicketControlView("deal"))
    # Avvia web server transcript
    await start_web_server()


# ── Gruppo comandi slash /pingbackup ──────────────────────────────────────────

class PingBackupGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="pingbackup", description="Gestione del bot ping backup")


ping_group = PingBackupGroup()


@ping_group.command(name="setup", description="Imposta o crea il canale di backup per i ping")
@app_commands.describe(canale="Canale esistente da usare (opzionale)")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction, canale: discord.TextChannel = None):
    guild = interaction.guild
    if canale is None:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(send_messages=False, read_messages=False),
            guild.me: discord.PermissionOverwrite(send_messages=True, read_messages=True),
        }
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True)
        try:
            canale = await guild.create_text_channel("ping-backup", overwrites=overwrites,
                                                      topic="📋 Archivio automatico di tutti i ping del server")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Non ho i permessi per creare canali.", ephemeral=True)
            return

    update_guild_config(guild.id, {"backup_channel_id": canale.id})
    await interaction.response.send_message(
        f"✅ Canale di backup impostato su {canale.mention}.", ephemeral=True)



@ping_group.command(name="addchannel", description="Aggiunge un canale aperto — archivia ping di chiunque")
@app_commands.describe(canale="Canale da aggiungere")
@app_commands.checks.has_permissions(administrator=True)
async def addchannel(interaction: discord.Interaction, canale: discord.TextChannel):
    cfg = get_guild_config(interaction.guild.id)
    ids: list = cfg.get("open_channel_ids", [])
    if canale.id in ids:
        await interaction.response.send_message(f"ℹ️ {canale.mention} è già nella lista.", ephemeral=True)
        return
    ids.append(canale.id)
    update_guild_config(interaction.guild.id, {"open_channel_ids": ids})
    await interaction.response.send_message(f"✅ {canale.mention} aggiunto — i ping di chiunque verranno archiviati.", ephemeral=True)


@ping_group.command(name="rmchannel", description="Rimuove un canale aperto")
@app_commands.describe(canale="Canale da rimuovere")
@app_commands.checks.has_permissions(administrator=True)
async def rmchannel(interaction: discord.Interaction, canale: discord.TextChannel):
    cfg = get_guild_config(interaction.guild.id)
    ids: list = cfg.get("open_channel_ids", [])
    if canale.id not in ids:
        await interaction.response.send_message(f"ℹ️ {canale.mention} non era nella lista.", ephemeral=True)
        return
    ids.remove(canale.id)
    update_guild_config(interaction.guild.id, {"open_channel_ids": ids})
    await interaction.response.send_message(f"✅ {canale.mention} rimosso dalla lista.", ephemeral=True)


@ping_group.command(name="config", description="Mostra la configurazione attuale del bot")
@app_commands.checks.has_permissions(administrator=True)
async def config_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id)

    backup_ch = guild.get_channel(cfg.get("backup_channel_id") or 0)
    ch_str = backup_ch.mention if backup_ch else "*(non impostato)*"


    open_ids = cfg.get("open_channel_ids", [])
    open_str = ", ".join(f"<#{c}>" for c in open_ids) if open_ids else "*(nessuno)*"

    embed = discord.Embed(title="⚙️ Configurazione Ping Backup", color=discord.Color.blurple())
    embed.add_field(name="Canale backup", value=ch_str, inline=False)
    embed.add_field(name="Canali aperti", value=open_str, inline=False)
    embed.set_footer(text="Owner del server: sempre autorizzato automaticamente")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# Gestione errori permessi
@setup.error
@addchannel.error
@rmchannel.error
@config_cmd.error
async def admin_only_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Solo gli amministratori possono usare questo comando.", ephemeral=True)


tree.add_command(ping_group)


# ── Anti-Link System ───────────────────────────────────────────────────────────

# Canale dove solo i domini whitelist sono permessi
ANTILINK_WHITELIST_CHANNEL = 1467863886370701322
# Canale dove nessun link è permesso
ANTILINK_STRICT_CHANNEL = 1416322516481212516

ANTILINK_CHANNEL_IDS = {ANTILINK_WHITELIST_CHANNEL, ANTILINK_STRICT_CHANNEL}

URL_REGEX = re.compile(
    r'https?://[^\s]+|www\.[^\s]+',
    re.IGNORECASE
)

def extract_domain(url: str) -> str:
    """Estrae il dominio da un URL."""
    url = re.sub(r'^https?://', '', url)
    url = re.sub(r'^www\.', '', url)
    return url.split('/')[0].split('?')[0].lower()


def get_antilink_config(guild_id: int) -> dict:
    cfg = load_config()
    key = str(guild_id)
    guild_cfg = cfg.get(key, {})
    return guild_cfg.get("antilink", {
        "allowed_domains": [],
    })


def update_antilink_config(guild_id: int, partial: dict):
    cfg = load_config()
    key = str(guild_id)
    if key not in cfg:
        cfg[key] = {}
    if "antilink" not in cfg[key]:
        cfg[key]["antilink"] = {"allowed_domains": []}
    cfg[key]["antilink"].update(partial)
    save_config(cfg)


def is_link_allowed(url: str, antilink_cfg: dict) -> bool:
    """Restituisce True se il link è nella whitelist."""
    domain = extract_domain(url)
    allowed = antilink_cfg.get("allowed_domains", [])
    return any(domain == a or domain.endswith("." + a) for a in allowed)


DM_WARNING_WHITELIST = """⚠️ **Unauthorized Link Detected**

This link is not permitted in the [WTB Verified](https://discord.com/channels/1383358337432813618/1467863886370701322) channel. Only verified links from approved domains are allowed. If you believe your link should be permitted, please request access via our [Support Ticket](https://discord.com/channels/1383358337432813618/1416824721932161025)

Repeated violations will result in disciplinary action!

*Fight Kicks Staff*"""

DM_WARNING_STRICT = """⚠️ **Unauthorized Link Detected**

Links are strictly not permitted in the [Legit Check](https://discord.com/channels/1383358337432813618/1416322516481212516) channel. Please use text only.

Repeated violations will result in disciplinary action!

*Fight Kicks Staff*"""


@bot.event
async def on_message(message: discord.Message):
    if message.author.id == bot.user.id:
        return
    if message.guild is None:
        return

    guild_cfg = get_guild_config(message.guild.id)

    # ── Anti-link check ──
    if message.channel.id in ANTILINK_CHANNEL_IDS:
        antilink_cfg = get_antilink_config(message.guild.id)
        is_exempt = (
            message.author.id == message.guild.owner_id
            or message.author.guild_permissions.administrator
            or (message.author.bot and message.author.id in guild_cfg.get("allowed_bot_ids", []))
            or (isinstance(message.author, discord.Member) and
                {r.id for r in message.author.roles} & set(guild_cfg.get("staff_role_ids", [])))
        )
        if not is_exempt:
            urls = URL_REGEX.findall(message.content)
            for url in urls:
                # Canale strict: nessun link ammesso
                if message.channel.id == ANTILINK_STRICT_CHANNEL:
                    try:
                        await message.delete()
                    except discord.Forbidden:
                        print("⚠️ Impossibile eliminare il messaggio — controlla i permessi del bot")
                    try:
                        await message.author.send(DM_WARNING_STRICT)
                    except discord.Forbidden:
                        pass
                    return
                # Canale whitelist: solo domini autorizzati
                elif not is_link_allowed(url, antilink_cfg):
                    try:
                        await message.delete()
                    except discord.Forbidden:
                        print("⚠️ Impossibile eliminare il messaggio — controlla i permessi del bot")
                    try:
                        await message.author.send(DM_WARNING_WHITELIST)
                    except discord.Forbidden:
                        pass
                    return

    # ── Ping backup check ──
    backup_channel_id = guild_cfg.get("backup_channel_id")
    if backup_channel_id and has_ping(message):
        open_channel_ids = guild_cfg.get("open_channel_ids", [])
        if message.channel.id in open_channel_ids or is_authorized(message, guild_cfg):
            backup_channel = message.guild.get_channel(backup_channel_id)
            if backup_channel:
                embed = build_embed(message)
                try:
                    await backup_channel.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException) as e:
                    print(f"⚠️ Errore invio embed: {e}")

    await bot.process_commands(message)


# ── Gruppo comandi /antilink ───────────────────────────────────────────────────

class AntiLinkGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="antilink", description="Gestione sistema anti-link")


antilink_group = AntiLinkGroup()


@antilink_group.command(name="allow", description="Aggiunge un dominio alla whitelist")
@app_commands.describe(dominio="Dominio da autorizzare (es. wtbmarketlist.eu)")
@app_commands.checks.has_permissions(administrator=True)
async def antilink_allow(interaction: discord.Interaction, dominio: str):
    dominio = dominio.lower().strip()
    cfg = get_antilink_config(interaction.guild.id)
    allowed = cfg.get("allowed_domains", [])
    if dominio in allowed:
        await interaction.response.send_message(f"ℹ️ `{dominio}` è già nella whitelist.", ephemeral=True)
        return
    allowed.append(dominio)
    update_antilink_config(interaction.guild.id, {"allowed_domains": allowed})
    await interaction.response.send_message(f"✅ `{dominio}` aggiunto alla whitelist.", ephemeral=True)


@antilink_group.command(name="unallow", description="Rimuove un dominio dalla whitelist")
@app_commands.describe(dominio="Dominio da rimuovere dalla whitelist")
@app_commands.checks.has_permissions(administrator=True)
async def antilink_unallow(interaction: discord.Interaction, dominio: str):
    dominio = dominio.lower().strip()
    cfg = get_antilink_config(interaction.guild.id)
    allowed = cfg.get("allowed_domains", [])
    if dominio not in allowed:
        await interaction.response.send_message(f"ℹ️ `{dominio}` non era nella whitelist.", ephemeral=True)
        return
    allowed.remove(dominio)
    update_antilink_config(interaction.guild.id, {"allowed_domains": allowed})
    await interaction.response.send_message(f"✅ `{dominio}` rimosso dalla whitelist.", ephemeral=True)


@antilink_group.command(name="config", description="Mostra i domini autorizzati")
@app_commands.checks.has_permissions(administrator=True)
async def antilink_config(interaction: discord.Interaction):
    cfg = get_antilink_config(interaction.guild.id)
    allowed = ", ".join(f"`{d}`" for d in cfg.get("allowed_domains", [])) or "*(nessuno)*"
    channels = ", ".join(f"<#{c}>" for c in ANTILINK_CHANNEL_IDS)

    embed = discord.Embed(title="⚙️ Configurazione Anti-Link", color=discord.Color.blurple())
    embed.add_field(name="Canali monitorati", value=channels, inline=False)
    embed.add_field(name="Domini whitelist", value=allowed, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@antilink_allow.error
@antilink_unallow.error
@antilink_config.error
async def antilink_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Solo gli amministratori possono usare questo comando.", ephemeral=True)


tree.add_command(antilink_group)


# ── WTB Command ───────────────────────────────────────────────────────────────

WTB_CHANNEL_ID = 1416219889303027722  # ⚠️ Sostituisci con l'ID reale di #wtb-monitor

FOOTER_ICON_WTB = "https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS/main/SCURO.png"

WTB_SERVER_LINK = "https://discord.gg/2aetYnaNSy"  # ⚠️ Sostituisci con il link reale



KICKSDB_API_KEY = "KICKS-A300-700C-981A-AE30A0839709"
RAPIDAPI_KEY = "7291b27ce9mshb8403cf0bbcfa49p1302afjsn4ea8c08075fe"

async def fetch_sneaker_image(nome: str, codice: str) -> str | None:
    """
    Cerca immagine tramite Sneaker Database StockX su RapidAPI.
    Cerca prima per SKU (styleId), poi per nome.
    """
    import aiohttp
    import urllib.parse

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": "sneaker-database-stockx.p.rapidapi.com",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:

        # ── Tentativo 1: cerca per SKU ──
        try:
            url = f"https://sneaker-database-stockx.p.rapidapi.com/productprice?styleId={urllib.parse.quote(codice)}"
            print(f"  🔎 RapidAPI SKU: {codice}")
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"  📡 Status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    img = data.get("image") or data.get("thumbnail")
                    if img:
                        print(f"✅ Immagine SKU trovata: {img[:80]}")
                        return img
                else:
                    body = await resp.text()
                    print(f"  ❌ Error: {body[:200]}")
        except Exception as e:
            print(f"⚠️ RapidAPI SKU fallito: {e}")

        # ── Tentativo 2: Simple Search per nome ──
        try:
            url = f"https://sneaker-database-stockx.p.rapidapi.com/searchproduct?query={urllib.parse.quote(nome)}"
            print(f"  🔎 RapidAPI nome: {nome}")
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"  📡 Status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    # Può essere lista o dict
                    if isinstance(data, list) and data:
                        img = data[0].get("image") or data[0].get("thumbnail")
                    elif isinstance(data, dict):
                        results = data.get("results", data.get("products", [data]))
                        img = results[0].get("image") or results[0].get("thumbnail") if results else None
                    else:
                        img = None
                    if img:
                        print(f"✅ Immagine nome trovata: {img[:80]}")
                        return img
                else:
                    body = await resp.text()
                    print(f"  ❌ Error: {body[:200]}")
        except Exception as e:
            print(f"⚠️ RapidAPI nome fallito: {e}")

    print("❌ Nessuna immagine trovata")
    return None

@tree.command(name="wtb", description="Posta un annuncio WTB nel canale wtb-monitor")
@app_commands.describe(
    nome="Nome del prodotto (es. Air Jordan 4 Retro OG SP Nigel Sylvester)",
    taglia="Taglia EU (es. 43 1/3)",
    codice="Codice SKU (es. HF4340-800)",
    link="Link StockX del prodotto",
    immagine="URL immagine del prodotto",
    condizione="Condizione (default: DSWT)",
    price="Prezzo offerto (default: YOUR OFFER)",
)
async def wtb(
    interaction: discord.Interaction,
    nome: str,
    taglia: str,
    codice: str,
    link: str,
    immagine: str,
    condizione: str = "DSWT",
    price: str = "YOUR OFFER",
):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Solo il proprietario del server può usare questo comando.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    img_url = immagine if immagine else None

    channel = interaction.guild.get_channel(WTB_CHANNEL_ID)
    if not channel:
        await interaction.followup.send("❌ Canale wtb-monitor non trovato.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"{nome} {taglia}",
        url=link,
        color=discord.Color(0x575553),
    )
    embed.description = (
        f"- {codice} – {condizione} – {price}\n"
        f"- Contact {interaction.user.mention} privately via DM\n"
        f"- [FIGHT KICKS OFFICIAL WTB SERVER]({WTB_SERVER_LINK})"
    )

    if img_url:
        embed.set_image(url=img_url)

    embed.set_footer(text="WTB Monitor", icon_url=FOOTER_ICON_WTB)
    embed.timestamp = datetime.now(timezone.utc)

    await channel.send(embed=embed)
    await send_to_webhooks(embed)
    await interaction.followup.send(f"✅ WTB inviato — {nome} {taglia} | Webhook: {len(get_webhooks())}", ephemeral=True)
    print(f"✅ WTB inviato — {nome} | img:{'✅' if img_url else '❌'} | webhook:{len(get_webhooks())}")


@wtb.error
async def wtb_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await interaction.response.send_message("❌ Errore nel comando WTB.", ephemeral=True)



# ── WTB Update Command ────────────────────────────────────────────────────────

WTB_LIST_URL = "https://www.wtbmarketlist.eu/list/734909407825100813"
WTB_UPDATE_CHANNEL_ID = 1420780972340805754


@tree.command(name="wtbupdate", description="Invia la WTB List aggiornata nel canale")
@app_commands.describe(immagine="URL immagine opzionale da allegare all'embed")
async def wtbupdate(interaction: discord.Interaction, immagine: str = None):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Solo il proprietario può usare questo comando.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    channel = interaction.guild.get_channel(WTB_UPDATE_CHANNEL_ID)
    if not channel:
        await interaction.followup.send("❌ Canale WTB Update non trovato.", ephemeral=True)
        return

    embed = discord.Embed(
        title="WTB LIST UPDATE",
        url=WTB_LIST_URL,
        color=discord.Color(0x575553),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="WTB Update", icon_url="https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS/main/SCURO.png")

    if immagine:
        embed.set_image(url=immagine)

    sent_msg = await channel.send(content="<@&1427396900801347594>", embed=embed)
    await interaction.followup.send("✅ WTB Update inviato!", ephemeral=True)
    print(f"✅ WTB Update inviato | img: {'✅' if immagine else '❌'}")

    # Invia al backup channel se configurato
    guild_cfg = get_guild_config(interaction.guild.id)
    backup_channel_id = guild_cfg.get("backup_channel_id")
    if backup_channel_id:
        backup_channel = interaction.guild.get_channel(backup_channel_id)
        if backup_channel:
            backup_embed = build_embed(sent_msg)
            try:
                await backup_channel.send(embed=backup_embed)
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"⚠️ Errore invio backup WTB Update: {e}")



# ── Webhook Manager ───────────────────────────────────────────────────────────

def get_webhooks() -> list:
    cfg = load_config()
    return cfg.get("wtb_webhooks", [])

def save_webhooks(webhooks: list):
    cfg = load_config()
    cfg["wtb_webhooks"] = webhooks
    save_config(cfg)

async def send_to_webhooks(embed: discord.Embed):
    """Invia l'embed a tutti i webhook salvati in parallelo."""
    import aiohttp
    import asyncio
    webhooks = get_webhooks()
    if not webhooks:
        return

    payload = {
        "embeds": [embed.to_dict()]
    }

    async def send_one(url: str):
        try:
            # Normalizza URL — rimuove /v10 se presente
            clean_url = url.replace("/api/v10/webhooks/", "/api/webhooks/")
            async with aiohttp.ClientSession() as session:
                async with session.post(clean_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status in (200, 204):
                        print(f"✅ Webhook OK: {clean_url[:60]}")
                    else:
                        body = await resp.text()
                        print(f"⚠️ Webhook {resp.status}: {clean_url[:60]} — {body[:100]}")
        except Exception as e:
            print(f"❌ Webhook error ({url[:60]}): {e}")

    await asyncio.gather(*[send_one(url) for url in webhooks])
    print(f"📡 Inviato a {len(webhooks)} webhook")


class WebhookGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="webhook", description="Gestione webhook WTB")


webhook_group = WebhookGroup()


@webhook_group.command(name="add", description="Aggiunge un webhook alla lista WTB")
@app_commands.describe(url="URL del webhook Discord")
async def webhook_add(interaction: discord.Interaction, url: str):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Solo il proprietario può usare questo comando.", ephemeral=True)
        return
    if not ("discord.com/api" in url and "webhooks" in url):
        await interaction.response.send_message("❌ URL webhook non valido.", ephemeral=True)
        return
    # Normalizza — rimuove /v10 se presente
    url = url.replace("/api/v10/webhooks/", "/api/webhooks/")
    webhooks = get_webhooks()
    if url in webhooks:
        await interaction.response.send_message("ℹ️ Webhook già presente.", ephemeral=True)
        return
    webhooks.append(url)
    save_webhooks(webhooks)
    await interaction.response.send_message(f"✅ Webhook aggiunto. Totale: {len(webhooks)}", ephemeral=True)


@webhook_group.command(name="remove", description="Rimuove un webhook dalla lista WTB")
@app_commands.describe(url="URL del webhook da rimuovere")
async def webhook_remove(interaction: discord.Interaction, url: str):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Solo il proprietario può usare questo comando.", ephemeral=True)
        return
    webhooks = get_webhooks()
    # Normalizza entrambi — rimuove /v10 per confronto
    url_norm = url.replace("/api/v10/webhooks/", "/api/webhooks/")
    match = next((w for w in webhooks if w.replace("/api/v10/webhooks/", "/api/webhooks/") == url_norm), None)
    if not match:
        await interaction.response.send_message("ℹ️ Webhook non trovato.", ephemeral=True)
        return
    webhooks.remove(match)
    save_webhooks(webhooks)
    await interaction.response.send_message(f"✅ Webhook rimosso. Totale: {len(webhooks)}", ephemeral=True)


@webhook_group.command(name="list", description="Mostra tutti i webhook salvati")
async def webhook_list(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Solo il proprietario può usare questo comando.", ephemeral=True)
        return
    webhooks = get_webhooks()
    if not webhooks:
        await interaction.response.send_message("ℹ️ Nessun webhook salvato.", ephemeral=True)
        return
    lines = [f"`{i+1}.` {url[:80]}..." for i, url in enumerate(webhooks)]
    embed = discord.Embed(
        title=f"📡 Webhook WTB ({len(webhooks)})",
        description="\n".join(lines),
        color=discord.Color(0x575553)
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@webhook_group.command(name="clear", description="Rimuove tutti i webhook")
async def webhook_clear(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Solo il proprietario può usare questo comando.", ephemeral=True)
        return
    save_webhooks([])
    await interaction.response.send_message("✅ Tutti i webhook rimossi.", ephemeral=True)


tree.add_command(webhook_group)


# ── Sistema Giveaway ──────────────────────────────────────────────────────────

GIVEAWAY_EMOJI  = "🎉"
GIVEAWAY_COLOR  = discord.Color(0x575553)
GIVEAWAY_FOOTER = "Giveaway"
GIVEAWAY_ICON   = "https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS/main/SCURO.png"


def parse_duration(raw: str) -> int | None:
    """Converte '1d', '12h', '30m', '60s' in secondi. Ritorna None se non valido."""
    raw = raw.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if raw and raw[-1] in units:
        try:
            return int(raw[:-1]) * units[raw[-1]]
        except ValueError:
            return None
    return None


def get_giveaways() -> dict:
    cfg = load_config()
    return cfg.get("giveaways", {})


def save_giveaways(giveaways: dict):
    cfg = load_config()
    cfg["giveaways"] = giveaways
    save_config(cfg)


def build_giveaway_embed(
    prize: str,
    end_ts: float,
    winners_count: int,
    host: str | None = None,
    entries: int = 0,
    ended: bool = False,
    winner_ids: list[int] | None = None,
    rules: str | None = None,
) -> discord.Embed:
    """
    host: stringa libera opzionale — mention, testo o link. Se None, il campo non appare.
    rules: testo opzionale mostrato nel campo Rules dell'embed attivo.
    """
    discord_ts = f"<t:{int(end_ts)}:f>"

    winner_label = "Winner" if winners_count == 1 else "Winners"

    if ended:
        title = prize
        if winner_ids:
            winners_str = " - ".join(f"<@{wid}>" for wid in winner_ids)
            description = f"**{winner_label} :** {winners_str}"
        else:
            description = "No valid participants."
    else:
        title = prize
        description = None

    embed = discord.Embed(title=title, description=description, color=GIVEAWAY_COLOR)

    if not ended:
        embed.add_field(name="Expires", value=f"<t:{int(end_ts)}:R> | <t:{int(end_ts)}:f>", inline=False)
        embed.add_field(name="Entries", value=str(entries), inline=True)
        embed.add_field(name=winner_label, value=str(winners_count), inline=True)
        if host:
            embed.add_field(name="Host", value=host, inline=False)
        if rules:
            embed.add_field(name="Rules", value=rules, inline=False)
    else:
        embed.add_field(name="Expired", value=discord_ts, inline=False)
        embed.add_field(name="Entries", value=str(entries), inline=True)
        embed.add_field(name=winner_label, value=str(winners_count), inline=True)
        if host:
            embed.add_field(name="Host", value=host, inline=False)

    embed.set_footer(text=GIVEAWAY_FOOTER, icon_url=GIVEAWAY_ICON)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


async def count_entries(message: discord.Message) -> int:
    """Conta le reaction 🎉 escludendo i bot."""
    for reaction in message.reactions:
        if str(reaction.emoji) == GIVEAWAY_EMOJI:
            count = 0
            async for user in reaction.users():
                if not user.bot:
                    count += 1
            return count
    return 0


async def conclude_giveaway(giveaway_id: str, giveaway: dict):
    """Estrae i vincitori, aggiorna l'embed e notifica il canale."""
    channel = bot.get_channel(giveaway["channel_id"])
    if channel is None:
        return
    try:
        message = await channel.fetch_message(giveaway["message_id"])
    except discord.NotFound:
        return

    participants = []
    for reaction in message.reactions:
        if str(reaction.emoji) == GIVEAWAY_EMOJI:
            async for user in reaction.users():
                if not user.bot:
                    participants.append(user.id)

    winners_count = giveaway["winners_count"]
    winner_ids = random.sample(participants, min(winners_count, len(participants))) if participants else []
    entries = len(participants)

    ended_embed = build_giveaway_embed(
        prize=giveaway["prize"],
        end_ts=giveaway["end_ts"],
        winners_count=winners_count,
        host=giveaway["host"],
        entries=entries,
        ended=True,
        winner_ids=winner_ids,
    )
    await message.edit(embed=ended_embed)

    if winner_ids:
        mentions = " - ".join(f"<@{wid}>" for wid in winner_ids)
        await channel.send(
            f"Congratulations {mentions}\nYou won **{giveaway['prize']}**!"
        )
    else:
        await channel.send(
            f"The giveaway for **{giveaway['prize']}** ended with no valid participants."
        )

    giveaways = get_giveaways()
    giveaways.pop(giveaway_id, None)
    save_giveaways(giveaways)


def migrate_giveaway(g: dict) -> dict:
    """Migrazione: converte host_id (vecchio formato) in host (nuovo formato)."""
    if "host" not in g and "host_id" in g:
        g["host"] = f"<@{g['host_id']}>"
    return g


@tasks.loop(seconds=30)
async def giveaway_check():
    """Ogni 30s: chiude i giveaway scaduti e aggiorna le entries di quelli attivi."""
    now = datetime.now(timezone.utc).timestamp()
    giveaways = get_giveaways()

    # Migra eventuali record in vecchio formato
    changed = False
    for gid, g in giveaways.items():
        if "host" not in g:
            migrate_giveaway(g)
            changed = True
    if changed:
        save_giveaways(giveaways)

    expired = [gid for gid, g in giveaways.items() if g["end_ts"] <= now]
    for gid in expired:
        await conclude_giveaway(gid, giveaways[gid])

    # Aggiorna entries embed per i giveaway ancora attivi
    active = {gid: g for gid, g in giveaways.items() if g["end_ts"] > now}
    for gid, g in active.items():
        channel = bot.get_channel(g["channel_id"])
        if channel is None:
            continue
        try:
            message = await channel.fetch_message(g["message_id"])
        except discord.NotFound:
            continue
        entries = await count_entries(message)
        updated_embed = build_giveaway_embed(
            prize=g["prize"],
            end_ts=g["end_ts"],
            winners_count=g["winners_count"],
            host=g.get("host"),
            entries=entries,
            rules=g.get("rules"),
        )
        await message.edit(embed=updated_embed)


class GiveawayGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="giveaway", description="Giveaway management")


giveaway_group = GiveawayGroup()


@giveaway_group.command(name="start", description="Start a new giveaway")
@app_commands.describe(
    prize="What's being given away (e.g. '2GB Flaming Proxies')",
    duration="Giveaway duration (e.g. 1d, 12h, 30m)",
    winners="Number of winners (default: 1)",
    hosted_by="Optional: override host with a partner tag or link",
    rules="Optional: giveaway rules shown in the embed",
)
async def giveaway_start(
    interaction: discord.Interaction,
    prize: str,
    duration: str,
    winners: app_commands.Range[int, 1, 20] = 1,
    hosted_by: str = None,
    rules: str = None,
):
    if not (
        interaction.user.id == interaction.guild.owner_id
        or interaction.user.guild_permissions.administrator
    ):
        await interaction.response.send_message(
            "❌ Only the owner or an administrator can create giveaways.", ephemeral=True
        )
        return

    seconds = parse_duration(duration)
    if seconds is None or seconds < 30:
        await interaction.response.send_message(
            "❌ Invalid duration. Use: `30s`, `10m`, `2h`, `1d` (minimum 30 seconds).",
            ephemeral=True,
        )
        return

    target_channel = interaction.channel
    end_ts = datetime.now(timezone.utc).timestamp() + seconds
    host = hosted_by if hosted_by else None

    embed = build_giveaway_embed(
        prize=prize,
        end_ts=end_ts,
        winners_count=winners,
        host=host,
        entries=0,
        rules=rules,
    )

    await interaction.response.send_message("✅ Giveaway started!", ephemeral=True)
    giveaway_msg = await target_channel.send(embed=embed)
    await giveaway_msg.add_reaction(GIVEAWAY_EMOJI)

    giveaways = get_giveaways()
    giveaways[str(giveaway_msg.id)] = {
        "message_id": giveaway_msg.id,
        "channel_id": target_channel.id,
        "guild_id": interaction.guild.id,
        "prize": prize,
        "end_ts": end_ts,
        "winners_count": winners,
        "host": host,
        "rules": rules,
    }
    save_giveaways(giveaways)



tree.add_command(giveaway_group)




@bot.command(name="redem")
async def redem(ctx, *, args: str = ""):
    """
    !redem                           → default (support ticket)
    !redem "Server Name" link        → ticket in external server
    !redem @tag/<@ID>/ID             → contact a person
    """
    if not (
        ctx.author.id == ctx.guild.owner_id
        or ctx.author.guild_permissions.administrator
    ):
        await ctx.message.delete()
        return

    await ctx.message.delete()

    line1 = "Congratulations to the winners!"
    line3 = "Thank you for participating ♥️"

    args = args.strip()
    import re as _re

    mention_match = _re.match(r"<@!?(\d+)>", args)
    id_only_match = _re.fullmatch(r"\d+", args)
    username_match = _re.match(r"@\S+", args)

    if mention_match:
        line2 = f"To collect your prize contact <@{mention_match.group(1)}>"
    elif id_only_match:
        line2 = f"To collect your prize contact <@{args}>"
    elif username_match:
        line2 = f"To collect your prize contact {args}"
    elif args:
        # Formato: "Server Name" link  oppure  Server Name link (ultima parola = link)
        quoted = _re.match(r'"(.+?)"\s+(https?://\S+)', args)
        if quoted:
            server_name, server_link = quoted.group(1), quoted.group(2)
        else:
            parts = args.rsplit(None, 1)
            server_name = parts[0].strip('"') if len(parts) > 1 else args
            server_link = parts[1] if len(parts) > 1 else ""
        line2 = f"To collect your prize open a ticket in [**{server_name}**]({server_link})"
    else:
        line2 = "To collect your prize open a [support ticket](https://discord.com/channels/1383358337432813618/1416824721932161025)"

    message = f"- {line1}\n- {line2}\n- {line3}"
    await ctx.send(message)


# ── Announcement Command ──────────────────────────────────────────────────────

ANNOUNCEMENT_CHANNEL_ID = 1383358337432813621
_announcement_pending = set()  # set di user ID in attesa

@bot.command(name="news")
async def news(ctx):
    if not (
        ctx.author.id == ctx.guild.owner_id
        or ctx.author.guild_permissions.administrator
    ):
        await ctx.message.delete()
        return

    await ctx.message.delete()

    # Notifica solo a chi ha scritto il comando
    prompt = await ctx.send(f"<@{ctx.author.id}> ✅ Scrivi il messaggio da mandare nel canale announcement. Puoi allegare foto o file.")
    _announcement_pending.add(ctx.author.id)

    def check(m):
        return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and ctx.author.id in _announcement_pending

    try:
        msg = await bot.wait_for("message", check=check, timeout=300)
    except:
        _announcement_pending.discard(ctx.author.id)
        await prompt.delete()
        return

    _announcement_pending.discard(ctx.author.id)

    ann_channel = ctx.guild.get_channel(ANNOUNCEMENT_CHANNEL_ID)
    if not ann_channel:
        await prompt.delete()
        await msg.delete()
        return

    # Scarica gli allegati PRIMA di cancellare il messaggio (dopo la delete il CDN restituisce 404)
    files = []
    for att in msg.attachments:
        try:
            f = await att.to_file()
            files.append(f)
        except Exception as e:
            print(f"⚠️ Allegato non scaricabile: {e}")

    await prompt.delete()
    await msg.delete()

    content = msg.content if msg.content else None

    try:
        await ann_channel.send(content=content, files=files if files else discord.utils.MISSING)
    except Exception as e:
        print(f"⚠️ Errore invio announcement: {e}")



# ── Ticket System ─────────────────────────────────────────────────────────────

import asyncio
from aiohttp import web as aiohttp_web
import aiohttp

TICKET_OWNER_ID       = 734909407825100813
TICKET_LOG_CHANNEL_ID = 1416439928497111171
LOGO_URL              = "https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS/main/SCURO.png"
CLOSE_DELAY           = 5  # secondi prima dell'eliminazione

PANELS = {
    "support": {
        "label":        "OPEN SUPPORT TICKET",
        "button_label": "📩 Create Ticket",
        "footer":       "Support Ticket",
        "category_id":  1416824941042471072,
        "color":        0x6B6B6B,
        "prefix":       "support",
    },
    "deal": {
        "label":        "OPEN DEAL TICKET",
        "button_label": "📩 Create Ticket",
        "footer":       "Deal Ticket",
        "category_id":  1416823933445083316,
        "color":        0x6B6B6B,
        "prefix":       "deal",
    },
}

# In-memory transcript store: channel_id -> list of HTML strings
_transcripts: dict[int, list[str]] = {}
# In-memory web server storage: token -> html content
_transcript_store: dict[str, str] = {}

# ── Web server per transcript ──────────────────────────────────────────────────

async def transcript_handler(request):
    token = request.match_info.get("token", "")
    html = _transcript_store.get(token)
    if not html:
        return aiohttp_web.Response(status=404, text="Transcript not found.")
    return aiohttp_web.Response(content_type="text/html", text=html)

async def start_web_server():
    app = aiohttp_web.Application()
    app.router.add_get("/transcript/{token}", transcript_handler)
    app.router.add_get("/health", lambda r: aiohttp_web.Response(text="OK"))
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = aiohttp_web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Web server avviato su porta {port}")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _ticket_number(channel_name: str) -> str:
    parts = channel_name.rsplit("-", 1)
    return parts[-1] if len(parts) == 2 else "0000"

def _is_owner(user_id: int) -> bool:
    return user_id == TICKET_OWNER_ID

def _render_discord_text(text: str, guild) -> str:
    """Converte markdown Discord e mention in HTML leggibile."""
    import re
    # **bold**
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # *italic*
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # `code`
    text = re.sub(r'`([^`]+)`', r'<code style="background:#202225;padding:1px 4px;border-radius:3px">\1</code>', text)
    # User mentions <@ID>
    def replace_user(m):
        uid = int(m.group(1))
        member = guild.get_member(uid) if guild else None
        name = str(member) if member else str(uid)
        return f'<span style="color:#7289da;background:#3c4270;padding:0 3px;border-radius:3px">@{name}</span>'
    text = re.sub(r'<@!?(\d+)>', replace_user, text)
    # Channel mentions <#ID>
    def replace_channel(m):
        cid = int(m.group(1))
        ch = guild.get_channel(cid) if guild else None
        name = ch.name if ch else str(cid)
        return f'<span style="color:#7289da;background:#3c4270;padding:0 3px;border-radius:3px">#{name}</span>'
    text = re.sub(r'<#(\d+)>', replace_channel, text)
    # Role mentions <@&ID>
    def replace_role(m):
        rid = int(m.group(1))
        role = guild.get_role(rid) if guild else None
        name = role.name if role else str(rid)
        return f'<span style="color:#7289da">@{name}</span>'
    text = re.sub(r'<@&(\d+)>', replace_role, text)
    return text

def _format_message_html(msg: discord.Message) -> str:
    avatar = msg.author.display_avatar.url if msg.author.display_avatar else ""
    name   = str(msg.author)
    ts     = msg.created_at.strftime("%d/%m/%Y %H:%M")
    text   = _render_discord_text(msg.content or "", msg.guild)
    text   = text.replace("\n", "<br>")

    attachments_html = ""
    for att in msg.attachments:
        if att.content_type and att.content_type.startswith("image"):
            attachments_html += f'<br><img src="{att.url}" style="max-width:400px;border-radius:4px;margin-top:6px">'
        else:
            attachments_html += f'<br><a href="{att.url}" style="color:#7289da">{att.filename}</a>'

    return f"""
    <div class="msg">
      <img class="avatar" src="{avatar}" alt="">
      <div class="content">
        <span class="author">{name}</span>
        <span class="ts">{ts}</span>
        <div class="text">{text}{attachments_html}</div>
      </div>
    </div>"""

def _build_transcript_html(channel: discord.TextChannel, messages: list[discord.Message], panel_label: str) -> str:
    msgs_html = "\n".join(_format_message_html(m) for m in messages)
    user_counts: dict[str, int] = {}
    for m in messages:
        key = str(m.author)
        user_counts[key] = user_counts.get(key, 0) + 1
    users_rows = "\n".join(
        f'<tr><td>{u}</td><td>{c}</td></tr>'
        for u, c in sorted(user_counts.items(), key=lambda x: -x[1])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transcript — {channel.name}</title>
<style>
  body{{background:#36393f;color:#dcddde;font-family:'Whitney','Helvetica Neue',Helvetica,Arial,sans-serif;margin:0;padding:0}}
  .header{{background:#2f3136;padding:20px 30px;border-bottom:1px solid #202225}}
  .header h1{{margin:0;font-size:1.4rem;color:#fff}}
  .header p{{margin:4px 0 0;font-size:.85rem;color:#b9bbbe}}
  .stats{{background:#2f3136;margin:16px 24px;border-radius:8px;padding:16px;display:flex;gap:32px;flex-wrap:wrap}}
  .stat{{display:flex;flex-direction:column}}
  .stat-label{{font-size:.75rem;color:#b9bbbe;text-transform:uppercase;letter-spacing:.05em}}
  .stat-value{{font-size:1rem;color:#fff;margin-top:2px}}
  .users-table{{background:#2f3136;margin:0 24px 16px;border-radius:8px;padding:16px}}
  .users-table h3{{margin:0 0 10px;font-size:.9rem;color:#b9bbbe;text-transform:uppercase;letter-spacing:.05em}}
  table{{border-collapse:collapse;width:100%}}
  td,th{{padding:6px 10px;text-align:left;font-size:.85rem;border-bottom:1px solid #40444b}}
  th{{color:#b9bbbe}}
  .messages{{padding:0 24px 40px}}
  .msg{{display:flex;gap:12px;padding:8px 0;border-bottom:1px solid #40444b33}}
  .avatar{{width:40px;height:40px;border-radius:50%;flex-shrink:0}}
  .content{{flex:1;min-width:0}}
  .author{{font-weight:600;color:#fff;font-size:.9rem}}
  .ts{{font-size:.75rem;color:#72767d;margin-left:8px}}
  .text{{font-size:.9rem;color:#dcddde;margin-top:4px;word-break:break-word;white-space:pre-wrap}}
</style>
</head>
<body>
<div class="header">
  <h1>📄 {channel.name}</h1>
  <p>{panel_label} — FIGHT KICKS WTB</p>
</div>
<div class="stats">
  <div class="stat"><span class="stat-label">Channel</span><span class="stat-value">{channel.name}</span></div>
  <div class="stat"><span class="stat-label">Messages</span><span class="stat-value">{len(messages)}</span></div>
</div>
<div class="users-table">
  <h3>Users in transcript</h3>
  <table><tr><th>User</th><th>Messages</th></tr>{users_rows}</table>
</div>
<div class="messages">{msgs_html}</div>
</body>
</html>"""

async def _generate_and_post_transcript(channel: discord.TextChannel, guild: discord.Guild, panel_label: str):
    """Genera il transcript, lo hosta e invia l'embed nel canale log."""
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        messages.append(msg)

    html = _build_transcript_html(channel, messages, panel_label)

    import secrets
    token = secrets.token_urlsafe(16)
    _transcript_store[token] = html

    # Salva su Gist come backup persistente
    filename = f"transcript-{channel.name}-{token[:8]}.html"
    import asyncio as _asyncio
    _asyncio.get_event_loop().run_in_executor(None, save_transcript_to_gist, filename, html)

    # Link Railway per aprire correttamente la pagina HTML
    base_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if base_url:
        link = f"https://{base_url}/transcript/{token}"
    else:
        link = f"http://localhost:{os.environ.get('PORT', 8080)}/transcript/{token}"

    log_ch = guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if not log_ch:
        return link

    # Conta utenti con formato emoji | mention | ID
    user_counts: dict[str, tuple] = {}
    for m in messages:
        uid = m.author.id
        if uid not in user_counts:
            user_counts[uid] = (m.author.mention, str(uid), 0)
        mention, mid, cnt = user_counts[uid]
        user_counts[uid] = (mention, mid, cnt + 1)
    sorted_users = sorted(user_counts.values(), key=lambda x: -x[2])
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    users_lines = []
    for i, (mention, mid, cnt) in enumerate(sorted_users):
        emoji = emojis[i] if i < len(emojis) else f"{i+1}."
        users_lines.append(f"{emoji} | {mention} | `{mid}`")
    users_str = "\n".join(users_lines)[:1024]

    # Trova chi ha aperto il ticket dal topic del canale
    opener_avatar = None
    if channel.topic:
        import re
        match = re.search(r'\((\d+)\)', channel.topic)
        if match:
            opener_id = int(match.group(1))
            opener_member = channel.guild.get_member(opener_id)
            if opener_member:
                opener_avatar = opener_member.display_avatar.url

    embed = discord.Embed(color=0x6B6B6B)
    embed.add_field(name="Ticket Name",  value=channel.name,   inline=True)
    panel_short = panel_label.replace("OPEN SUPPORT TICKET", "Support Ticket").replace("OPEN DEAL TICKET", "Deal Ticket")
    embed.add_field(name="Panel Name",   value=panel_short,    inline=True)
    embed.add_field(name="Messages",     value=str(len(messages)), inline=True)
    embed.add_field(name="Users in transcript", value=users_str or "—", inline=False)
    if opener_avatar:
        embed.set_thumbnail(url=opener_avatar)
    embed.set_footer(text="Ticket Support", icon_url=LOGO_URL)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Direct Link", url=link, style=discord.ButtonStyle.link))

    await log_ch.send(embed=embed, view=view)
    return link

# ── Views / Buttons ────────────────────────────────────────────────────────────

class TicketControlView(discord.ui.View):
    """Bottoni Close Ticket e Transcript — persistenti nel canale ticket."""
    def __init__(self, panel_key: str):
        super().__init__(timeout=None)
        self.panel_key = panel_key

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger,  custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_owner(interaction.user.id):
            await interaction.response.send_message("❌ Only the server owner can close tickets.", ephemeral=True)
            return
        log_ch = interaction.guild.get_channel(TICKET_LOG_CHANNEL_ID)
        log_mention = log_ch.mention if log_ch else ""
        await interaction.response.send_message(
            f"Ticket will be closed in **{CLOSE_DELAY} seconds**\nTranscript ready : {log_mention}",
            ephemeral=False
        )
        panel_label = PANELS[self.panel_key]["label"]
        await _generate_and_post_transcript(interaction.channel, interaction.guild, panel_label)
        await asyncio.sleep(CLOSE_DELAY)
        try:
            await interaction.channel.delete(reason="Ticket closed")
        except Exception as e:
            print(f"⚠️ Error deleting ticket channel: {e}")

    @discord.ui.button(label="📄 Transcript", style=discord.ButtonStyle.primary, custom_id="ticket_transcript")
    async def transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_owner(interaction.user.id):
            await interaction.response.send_message("❌ Only the server owner can generate transcripts.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        panel_label = PANELS[self.panel_key]["label"]
        link = await _generate_and_post_transcript(interaction.channel, interaction.guild, panel_label)
        log_ch = interaction.guild.get_channel(TICKET_LOG_CHANNEL_ID)
        await interaction.followup.send(f"Transcript ready : {log_ch.mention if log_ch else str(TICKET_LOG_CHANNEL_ID)}", ephemeral=True)


class CreateTicketView(discord.ui.View):
    """Bottone Create Ticket nell'embed del panel."""
    def __init__(self, panel_key: str):
        super().__init__(timeout=None)
        self.panel_key = panel_key

    @discord.ui.button(label="📩 Create Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_create_support")
    async def create_support(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_create_ticket(interaction, "support")

    
class CreateDealTicketView(discord.ui.View):
    """Bottone Create Ticket per il panel deal."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Create Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_create_deal")
    async def create_deal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_create_ticket(interaction, "deal")


async def _handle_create_ticket(interaction: discord.Interaction, panel_key: str):
    panel   = PANELS[panel_key]
    guild   = interaction.guild
    user    = interaction.user
    category = guild.get_channel(panel["category_id"])

    # Controlla se l'utente ha già un ticket aperto in questa categoria
    if category:
        for ch in category.text_channels:
            if ch.topic and str(user.id) in ch.topic:
                await interaction.response.send_message(
                    f"❌ You already have an open ticket: {ch.mention}", ephemeral=True
                )
                return

    await interaction.response.defer(ephemeral=True)

    # Numero progressivo
    existing = [c.name for c in (category.text_channels if category else [])]
    nums = []
    for name in existing:
        parts = name.rsplit("-", 1)
        if len(parts) == 2 and parts[-1].isdigit():
            nums.append(int(parts[-1]))
    next_num = (max(nums) + 1) if nums else 1
    channel_name = f"{panel['prefix']}-{next_num:04d}"

    # Permessi: solo owner + utente
    SUPPORT_STAFF_ID = 1417768260044325005

    overwrites = {
        guild.default_role:                    discord.PermissionOverwrite(view_channel=False),
        user:                                  discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    owner_member = guild.get_member(TICKET_OWNER_ID)
    if owner_member:
        overwrites[owner_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    # Bot stesso — appare nella lista membri del ticket
    bot_member = guild.get_member(bot.user.id)
    if bot_member:
        overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    # Staff aggiuntivo solo per ticket support
    if panel_key == "support":
        staff_member = guild.get_member(SUPPORT_STAFF_ID)
        if staff_member:
            overwrites[staff_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    # Admin override
    for role in guild.roles:
        if role.permissions.administrator:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    ticket_ch = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"Ticket by {user} ({user.id})",
    )

    descriptions = {
        "support": "Describe your need, the staff will be with you as soon as possible",
        "deal":    "Provide the product data and the agreed price, the staff will be with you as soon as possible",
    }
    titles = {
        "support": "WELCOME TO SUPPORT TICKET",
        "deal":    "WELCOME TO DEAL TICKET",
    }
    embed = discord.Embed(
        title=titles[panel_key],
        description=descriptions[panel_key],
        color=panel["color"],
    )
    embed.set_footer(text=panel["footer"], icon_url=LOGO_URL)

    control_view = TicketControlView(panel_key)
    await ticket_ch.send(content=user.mention, embed=embed, view=control_view)
    await interaction.followup.send(f"Your ticket has been created : {ticket_ch.mention}", ephemeral=True)
    print(f"✅ Ticket created: {ticket_ch.name} for {user}")


# ── Setup commands ─────────────────────────────────────────────────────────────

@bot.command(name="ticket")
async def ticket_setup(ctx, panel_key: str = "support"):
    """!ticket support  oppure  !ticket deal — invia l'embed nel canale corrente."""
    if ctx.author.id != TICKET_OWNER_ID:
        await ctx.message.delete()
        return
    await ctx.message.delete()

    panel_key = panel_key.lower()
    if panel_key not in PANELS:
        await ctx.send(f"❌ Unknown panel. Use: `!ticket support` or `!ticket deal`", delete_after=10)
        return

    panel = PANELS[panel_key]
    embed = discord.Embed(
        title=panel["label"],
        description="To create a ticket use the **Create Ticket** button below",
        color=panel["color"],
    )
    embed.set_footer(text=panel["footer"], icon_url=LOGO_URL)

    if panel_key == "support":
        view = CreateTicketView("support")
    else:
        view = CreateDealTicketView()

    await ctx.send(embed=embed, view=view)
    print(f"✅ Ticket panel '{panel_key}' sent in #{ctx.channel.name}")





# ── Member Counter ────────────────────────────────────────────────────────────

MEMBER_COUNT_CHANNEL_ID = 1416747222250426511
MEMBER_COUNT_ROLE_ID    = 1416724423607713883

@tasks.loop(minutes=15)
async def update_member_count():
    for guild in bot.guilds:
        channel = guild.get_channel(MEMBER_COUNT_CHANNEL_ID)
        if not channel:
            continue
        role = guild.get_role(MEMBER_COUNT_ROLE_ID)
        if not role:
            continue
        count = len(role.members)
        new_name = f"💻│Members : {count}"
        if channel.name != new_name:
            try:
                await channel.edit(name=new_name)
                print(f"✅ Member count aggiornato: {new_name}")
            except Exception as e:
                print(f"⚠️ Errore aggiornamento member count: {e}")

@update_member_count.before_loop
async def before_member_count():
    await bot.wait_until_ready()


# ── Invite Logger ─────────────────────────────────────────────────────────────

INVITE_LOG_CHANNEL_ID = 1416338500764307538

# Cache inviti: guild_id -> {code: uses}
_invite_cache: dict[int, dict[str, int]] = {}

async def _build_invite_cache(guild: discord.Guild):
    try:
        invites = await guild.invites()
        _invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
    except Exception as e:
        print(f"⚠️ Invite cache errore: {e}")

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    channel = guild.get_channel(INVITE_LOG_CHANNEL_ID)
    if not channel:
        return

    # Leggi inviti aggiornati
    try:
        new_invites = await guild.invites()
    except Exception as e:
        print(f"⚠️ fetch_invites errore: {e}")
        return

    old_cache = _invite_cache.get(guild.id, {})
    inviter = None
    invite_uses = 0

    # Mappa codici invito personalizzati
    INVITE_LABELS = {
        "7qXpwSuPuN": ("`Social`", None),
        "2aetYnaNSy": ("`Discord`", None),
        "Vd7C7Wjx3c": ("`Instagram`", None),
    }

    for inv in new_invites:
        old_uses = old_cache.get(inv.code, 0)
        if inv.uses > old_uses:
            inviter = inv.inviter
            invite_uses = inv.uses
            invite_code = inv.code
            break
    else:
        invite_code = None

    # Aggiorna cache
    _invite_cache[guild.id] = {inv.code: inv.uses for inv in new_invites}

    created_at = member.created_at.strftime("%d/%m/%Y")

    lines = [f"**New Member :** {member.mention}"]
    lines.append(f"**Account created :** `{created_at}`")
    if invite_code and invite_code in INVITE_LABELS:
        label, owner_id = INVITE_LABELS[invite_code]
        if label:
            lines.append(f"**Invited by :** {label}")
        else:
            lines.append(f"**Invited by :** <@{owner_id}>")
        lines.append(f"**Invites :** `{invite_uses}`")
    elif inviter:
        lines.append(f"**Invited by :** {inviter.mention}")
        lines.append(f"**Invites :** `{invite_uses}`")

    embed = discord.Embed(description="\n".join(lines), color=0x6B6B6B)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="New Members", icon_url=LOGO_URL)
    await channel.send(embed=embed)
    print(f"✅ Join log: {member} invited by {inviter}")

    # Assegna ruolo automatico
    role = member.guild.get_role(AUTO_ROLE_ID)
    if role:
        try:
            await member.add_roles(role)
            print(f"✅ Auto role assegnato: {role.name} → {member}")
        except Exception as e:
            print(f"⚠️ Errore auto role: {e}")

@bot.event
async def on_invite_create(invite: discord.Invite):
    if invite.guild:
        cache = _invite_cache.setdefault(invite.guild.id, {})
        cache[invite.code] = invite.uses or 0

@bot.event
async def on_invite_delete(invite: discord.Invite):
    if invite.guild:
        _invite_cache.get(invite.guild.id, {}).pop(invite.code, None)


# ── Auto Role ─────────────────────────────────────────────────────────────────

AUTO_ROLE_ID = 1416724423607713883

@bot.event
async def on_member_join_autorole(member: discord.Member):
    role = member.guild.get_role(AUTO_ROLE_ID)
    if role:
        try:
            await member.add_roles(role)
            print(f"✅ Ruolo assegnato: {role.name} → {member}")
        except Exception as e:
            print(f"⚠️ Errore assegnazione ruolo: {e}")


# ── Purge ─────────────────────────────────────────────────────────────────────

@bot.command(name="purge")
async def purge(ctx, amount: int = 10):
    if ctx.author.id != TICKET_OWNER_ID:
        await ctx.message.delete()
        return
    if amount < 1 or amount > 100:
        await ctx.send("❌ Inserisci un numero tra 1 e 100.", delete_after=5)
        return
    await ctx.message.delete()
    deleted = await ctx.channel.purge(limit=amount)



# ── Ban / Unban ───────────────────────────────────────────────────────────────

@bot.command(name="ban")
async def ban(ctx, user_input: str, *, reason: str = "No reason provided"):
    if ctx.author.id != TICKET_OWNER_ID:
        await ctx.message.delete()
        return
    await ctx.message.delete()

    # Supporta sia mention che ID numerico
    user_id = None
    if user_input.startswith("<@") and user_input.endswith(">"):
        user_id = int(user_input.strip("<@!>"))
    elif user_input.isdigit():
        user_id = int(user_input)

    if not user_id:
        await ctx.send("❌ Utente non valido. Usa `!ban @utente` o `!ban ID`", delete_after=5)
        return

    try:
        await ctx.guild.ban(discord.Object(id=user_id), reason=reason, delete_message_days=0)
        msg = f"User `{user_id}` has been banned\nReason : `{reason}`"
        await ctx.send(msg, delete_after=10)
        print(f"✅ Bannato: {user_id} — {reason}")
    except discord.NotFound:
        await ctx.send("❌ Utente non trovato.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ Non ho i permessi per bannare questo utente.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Errore: {e}", delete_after=5)


@bot.command(name="unban")
async def unban(ctx, user_id: int):
    if ctx.author.id != TICKET_OWNER_ID:
        await ctx.message.delete()
        return
    await ctx.message.delete()

    try:
        await ctx.guild.unban(discord.Object(id=user_id))
        await ctx.send(f"User `{user_id}` has been unbanned", delete_after=10)
        print(f"✅ Unbannato: {user_id}")
    except discord.NotFound:
        await ctx.send("❌ Utente non trovato nei ban.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ Non ho i permessi per sbannare.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Errore: {e}", delete_after=5)


# ── Timestamp Generator ───────────────────────────────────────────────────────

@tree.command(name="timestamp", description="Genera un timestamp Discord da data e ora")
@app_commands.describe(
    giorno="Giorno (1-31)",
    mese="Mese (1-12)",
    ora="Ora (0-23)",
    minuti="Minuti (0-59)"
)
async def timestamp_cmd(
    interaction: discord.Interaction,
    giorno: int,
    mese: int,
    ora: int,
    minuti: int
):
    from datetime import datetime
    import zoneinfo

    tz = zoneinfo.ZoneInfo("Europe/Rome")
    anno = datetime.now(tz).year
    try:
        dt = datetime(anno, mese, giorno, ora, minuti, tzinfo=tz)
    except ValueError as e:
        await interaction.response.send_message(f"❌ Data non valida : `{e}`", ephemeral=True)
        return

    ts = int(dt.timestamp())

    date_str = dt.strftime("%d/%m/%Y at %H:%M")

    embed = discord.Embed(
        title="Timestamp Generator",
        description=f"`{date_str}`",
        color=0x6B6B6B
    )
    embed.add_field(
        name="Raffle",
        value=f"`<t:{ts}:f>`",
        inline=False
    )
    embed.add_field(
        name="Remainder",
        value=f"`{ts}`",
        inline=False
    )
    embed.set_footer(text="Discord Tools", icon_url="https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS-LOGO-FOOTER/main/SCURO.png")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Release Countdown ─────────────────────────────────────────────────────────

@tree.command(name="release", description="Calcola la data di release da giorni/ore/minuti")
@app_commands.describe(
    giorni="Giorni che mancano",
    ore="Ore che mancano",
    minuti="Minuti che mancano"
)
async def release_cmd(
    interaction: discord.Interaction,
    giorni: int,
    ore: int,
    minuti: int
):
    from datetime import datetime, timedelta
    import zoneinfo

    tz = zoneinfo.ZoneInfo("Europe/Rome")
    now = datetime.now(tz)
    release = now + timedelta(days=giorni, hours=ore, minutes=minuti)
    # Arrotonda i minuti al multiplo di 5 più vicino
    rounded_minutes = round(release.minute / 5) * 5
    if rounded_minutes == 60:
        release = release.replace(minute=0) + timedelta(hours=1)
    else:
        release = release.replace(minute=rounded_minutes, second=0, microsecond=0)
    ts = int(release.timestamp())
    date_str = release.strftime("%d/%m/%Y at %H:%M")

    embed = discord.Embed(color=0x6B6B6B)
    embed.add_field(name="Release date", value=f"`{date_str}`", inline=False)
    embed.add_field(name="Raffle", value=f"`<t:{ts}:f>`", inline=False)
    embed.add_field(name="Remainder", value=f"`{ts}`", inline=False)
    embed.set_footer(text="Discord Tools", icon_url="https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS-LOGO-FOOTER/main/SCURO.png")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Percentuale ───────────────────────────────────────────────────────────────

@tree.command(name="percentuale", description="Calcola la percentuale di un numero")
@app_commands.describe(
    percentuale="La percentuale (es. 20)",
    numero="Il numero di partenza (es. 150)"
)
async def percentuale_cmd(
    interaction: discord.Interaction,
    percentuale: float,
    numero: float
):
    risultato = (percentuale / 100) * numero
    risultato_str = f"{risultato:.2f}".rstrip("0").rstrip(".")

    embed = discord.Embed(color=0x6B6B6B)
    embed.add_field(
        name="Percentuale",
        value=f"`{percentuale}% of {numero} = {risultato_str}`",
        inline=False
    )
    embed.set_footer(text="Discord Tools", icon_url="https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS-LOGO-FOOTER/main/SCURO.png")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Notion Integration ────────────────────────────────────────────────────────

NOTION_TOKEN    = os.environ.get("NOTION_TOKEN", "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")
NOTION_DB_ID    = "22f2595a87448058b766cec9d2bf6919"
NOTION_TABLE_URL = "https://app.notion.com/p/22f2595a87448058b766cec9d2bf6919?v=22f2595a8744818bb6af000c1b13c281"
NOTION_API_URL  = "https://api.notion.com/v1"
NOTION_HEADERS  = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

async def notion_get_next_id() -> int:
    """Restituisce il prossimo ID progressivo."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        payload = {"page_size": 100}
        async with session.post(
            f"{NOTION_API_URL}/databases/{NOTION_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=payload
        ) as resp:
            data = await resp.json()
            rows = data.get("results", [])
            ids = []
            for row in rows:
                props = row.get("properties", {})
                id_prop = props.get("ID", {})
                num = id_prop.get("number")
                if num is not None:
                    ids.append(int(num))
            return max(ids) + 1 if ids else 1


async def notion_add_order(seller, buyer, model, size, sell, retail, profit, ordered) -> str | None:
    """Aggiunge una nuova riga al database Notion e restituisce il page_id."""
    import aiohttp
    next_id = await notion_get_next_id()
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "ID":       {"number": next_id},
            "ORDERED":  {"date": {"start": ordered}},
            "SELLER":   {"rich_text": [{"text": {"content": seller}}]},
            "BUYER":    {"rich_text": [{"text": {"content": buyer}}]},
            "SKU":      {"title": [{"text": {"content": model}}]},
            "SIZE":     {"rich_text": [{"text": {"content": size}}]},
            "SELL":     {"number": sell},
            "RETAIL":   {"number": retail},
            "PROFIT":   {"number": profit},
            "STATUS":   {"select": {"name": "BOUGHT"}},
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{NOTION_API_URL}/pages",
            headers=NOTION_HEADERS,
            json=payload
        ) as resp:
            data = await resp.json()
            if resp.status == 200:
                return str(next_id)
            else:
                print(f"⚠️ Notion error: {data}")
                return None


async def notion_update_tracking(row_id: int, courier: str, tracking: str) -> bool:
    """Aggiorna COURRIER e TRACKING sulla riga con ID specificato."""
    import aiohttp
    # Prima trova il page_id dalla riga con quell'ID
    async with aiohttp.ClientSession() as session:
        payload = {
            "filter": {
                "property": "ID",
                "number": {"equals": row_id}
            }
        }
        async with session.post(
            f"{NOTION_API_URL}/databases/{NOTION_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=payload
        ) as resp:
            data = await resp.json()
            results = data.get("results", [])
            if not results:
                return False
            page_id = results[0]["id"]

        # Aggiorna la pagina
        update_payload = {
            "properties": {
                "COURIER":  {"rich_text": [{"text": {"content": courier}}]},
                "TRACKING": {"rich_text": [{"text": {"content": tracking}}]},
            }
        }
        async with session.patch(
            f"{NOTION_API_URL}/pages/{page_id}",
            headers=NOTION_HEADERS,
            json=update_payload
        ) as resp:
            return resp.status == 200


@tree.command(name="notion", description="Inserisce un nuovo ordine nella tabella Notion")
@app_commands.describe(
    seller="Nome del venditore",
    buyer="Nome dell'acquirente",
    model="Model o SKU",
    size="Taglia",
    sell="Prezzo di vendita (€)",
    retail="Prezzo di acquisto (€)"
)
async def notion_cmd(
    interaction: discord.Interaction,
    seller: str,
    buyer: str,
    model: str,
    size: str,
    sell: float,
    retail: float
):
    if interaction.user.id != TICKET_OWNER_ID:
        await interaction.response.send_message("❌ Non hai i permessi.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    from datetime import datetime
    import zoneinfo
    tz = zoneinfo.ZoneInfo("Europe/Rome")
    ordered = datetime.now(tz).strftime("%Y-%m-%d")
    profit = round(sell - retail, 2)

    row_id = await notion_add_order(seller, buyer, model, size, sell, retail, profit, ordered)

    if row_id:
        embed = discord.Embed(title="Order added", color=0x6B6B6B)
        embed.add_field(name="ID",       value=f"`{row_id}`",   inline=True)
        embed.add_field(name="Model",    value=f"`{model}`",    inline=True)
        embed.add_field(name="Size",     value=f"`{size}`",     inline=True)
        embed.add_field(name="Seller",   value=f"`{seller}`",   inline=True)
        embed.add_field(name="Buyer",    value=f"`{buyer}`",    inline=True)
        embed.add_field(name="Sell",     value=f"`{sell}€`",    inline=True)
        embed.add_field(name="Retail",   value=f"`{retail}€`",  inline=True)
        embed.add_field(name="Profit",   value=f"`{profit}€`",  inline=True)
        embed.add_field(name="Ordered",  value=f"`{ordered}`",  inline=True)
        embed.set_footer(text="Notion • Fight Kicks", icon_url="https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS-LOGO-FOOTER/main/SCURO.png")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Table", url=NOTION_TABLE_URL, style=discord.ButtonStyle.link))

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        print(f"✅ Notion ordine aggiunto: {model} (ID {row_id})")
    else:
        await interaction.followup.send("❌ Errore durante l'inserimento su Notion.", ephemeral=True)


@tree.command(name="tracking", description="Aggiorna corriere e tracking di un ordine")
@app_commands.describe(
    id="ID della riga da aggiornare",
    courier="Nome del corriere (es. DHL, GLS, BRT)",
    tracking="Codice tracking"
)
async def tracking_cmd(
    interaction: discord.Interaction,
    id: int,
    courier: str,
    tracking: str
):
    if interaction.user.id != TICKET_OWNER_ID:
        await interaction.response.send_message("❌ Non hai i permessi.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    ok = await notion_update_tracking(id, courier, tracking)

    if ok:
        embed = discord.Embed(title="Tracking updated", color=0x6B6B6B)
        embed.add_field(name="ID",       value=f"`{id}`",       inline=True)
        embed.add_field(name="Courier",  value=f"`{courier}`",  inline=True)
        embed.add_field(name="Tracking", value=f"`{tracking}`", inline=True)
        embed.set_footer(text="Notion • Fight Kicks", icon_url="https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS-LOGO-FOOTER/main/SCURO.png")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Table", url=NOTION_TABLE_URL, style=discord.ButtonStyle.link))

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        print(f"✅ Notion tracking aggiornato: riga {id}")
    else:
        await interaction.followup.send(f"❌ Riga con ID `{id}` non trovata.", ephemeral=True)

# ── Disconnessione e avvio ─────────────────────────────────────────────────────

@bot.event
async def on_disconnect():
    print("⚠️  Bot disconnesso — tentativo di riconnessione automatica...")



if __name__ == "__main__":
    import time
    import subprocess
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Variabile d'ambiente DISCORD_BOT_TOKEN non impostata.")

    # Playwright serve per il comando /wtb
    print("🔧 Installazione Chromium...")
    subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=False)
    subprocess.run(["python", "-m", "playwright", "install-deps", "chromium"], check=False)
    print("✅ Chromium pronto")

    async def main():
        while True:
            try:
                await bot.start(token)
            except Exception as e:
                print(f"❌ Errore: {e} — nuovo tentativo tra 10 secondi...")
                await asyncio.sleep(10)

    import asyncio
    asyncio.run(main())
