"""
Discord Ping Backup Bot — PINGER ONLY
======================================
Archivia automaticamente tutti i messaggi con @everyone, @here o menzioni di ruolo
inviati da owner, bot autorizzati e staff nel canale #ping-backup.

Comandi slash:
  /pingbackup setup      — imposta il canale di backup
  /pingbackup addstaff   — aggiunge un ruolo allo staff autorizzato
  /pingbackup rmstaff    — rimuove un ruolo dallo staff
  /pingbackup addbot     — aggiunge un bot alla whitelist
  /pingbackup rmbot      — rimuove un bot dalla whitelist
  /pingbackup addchannel — aggiunge un canale aperto (archivia ping di chiunque)
  /pingbackup rmchannel  — rimuove un canale aperto
  /pingbackup config     — mostra la configurazione attuale
"""

import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import re
from datetime import datetime, timezone

# ── Percorso file di configurazione ───────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(data: dict):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_FILE)


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
    print(f"✅ Bot online come {bot.user} (ID: {bot.user.id})")
    print("Comandi slash sincronizzati globalmente.")


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


@ping_group.command(name="addstaff", description="Aggiunge un ruolo alla lista staff autorizzati")
@app_commands.describe(ruolo="Ruolo da autorizzare")
@app_commands.checks.has_permissions(administrator=True)
async def addstaff(interaction: discord.Interaction, ruolo: discord.Role):
    cfg = get_guild_config(interaction.guild.id)
    ids: list = cfg.get("staff_role_ids", [])
    if ruolo.id in ids:
        await interaction.response.send_message(f"ℹ️ {ruolo.mention} è già autorizzato.", ephemeral=True)
        return
    ids.append(ruolo.id)
    update_guild_config(interaction.guild.id, {"staff_role_ids": ids})
    await interaction.response.send_message(f"✅ {ruolo.mention} aggiunto agli staff autorizzati.", ephemeral=True)


@ping_group.command(name="rmstaff", description="Rimuove un ruolo dalla lista staff autorizzati")
@app_commands.describe(ruolo="Ruolo da rimuovere")
@app_commands.checks.has_permissions(administrator=True)
async def rmstaff(interaction: discord.Interaction, ruolo: discord.Role):
    cfg = get_guild_config(interaction.guild.id)
    ids: list = cfg.get("staff_role_ids", [])
    if ruolo.id not in ids:
        await interaction.response.send_message(f"ℹ️ {ruolo.mention} non era nella lista.", ephemeral=True)
        return
    ids.remove(ruolo.id)
    update_guild_config(interaction.guild.id, {"staff_role_ids": ids})
    await interaction.response.send_message(f"✅ {ruolo.mention} rimosso dagli staff autorizzati.", ephemeral=True)


@ping_group.command(name="addbot", description="Aggiunge un bot alla whitelist")
@app_commands.describe(bot_id="ID numerico del bot da autorizzare")
@app_commands.checks.has_permissions(administrator=True)
async def addbot(interaction: discord.Interaction, bot_id: str):
    try:
        bid = int(bot_id)
    except ValueError:
        await interaction.response.send_message("❌ Inserisci un ID numerico valido.", ephemeral=True)
        return
    cfg = get_guild_config(interaction.guild.id)
    ids: list = cfg.get("allowed_bot_ids", [])
    if bid in ids:
        await interaction.response.send_message("ℹ️ Bot già nella whitelist.", ephemeral=True)
        return
    ids.append(bid)
    update_guild_config(interaction.guild.id, {"allowed_bot_ids": ids})
    await interaction.response.send_message(f"✅ Bot `{bid}` aggiunto alla whitelist.", ephemeral=True)


@ping_group.command(name="rmbot", description="Rimuove un bot dalla whitelist")
@app_commands.describe(bot_id="ID numerico del bot da rimuovere")
@app_commands.checks.has_permissions(administrator=True)
async def rmbot(interaction: discord.Interaction, bot_id: str):
    try:
        bid = int(bot_id)
    except ValueError:
        await interaction.response.send_message("❌ Inserisci un ID numerico valido.", ephemeral=True)
        return
    cfg = get_guild_config(interaction.guild.id)
    ids: list = cfg.get("allowed_bot_ids", [])
    if bid not in ids:
        await interaction.response.send_message("ℹ️ Bot non trovato nella whitelist.", ephemeral=True)
        return
    ids.remove(bid)
    update_guild_config(interaction.guild.id, {"allowed_bot_ids": ids})
    await interaction.response.send_message(f"✅ Bot `{bid}` rimosso dalla whitelist.", ephemeral=True)


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

    staff_ids = cfg.get("staff_role_ids", [])
    staff_str = ", ".join(f"<@&{r}>" for r in staff_ids) if staff_ids else "*(nessuno)*"

    bot_ids = cfg.get("allowed_bot_ids", [])
    bot_str = ", ".join(f"`{b}`" for b in bot_ids) if bot_ids else "*(nessuno)*"

    open_ids = cfg.get("open_channel_ids", [])
    open_str = ", ".join(f"<#{c}>" for c in open_ids) if open_ids else "*(nessuno)*"

    embed = discord.Embed(title="⚙️ Configurazione Ping Backup", color=discord.Color.blurple())
    embed.add_field(name="Canale backup", value=ch_str, inline=False)
    embed.add_field(name="Ruoli staff autorizzati", value=staff_str, inline=False)
    embed.add_field(name="Bot nella whitelist", value=bot_str, inline=False)
    embed.add_field(name="Canali aperti", value=open_str, inline=False)
    embed.set_footer(text="Owner del server: sempre autorizzato automaticamente")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# Gestione errori permessi
@setup.error
@addstaff.error
@rmstaff.error
@addbot.error
@rmbot.error
@addchannel.error
@rmchannel.error
@config_cmd.error
async def admin_only_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Solo gli amministratori possono usare questo comando.", ephemeral=True)


tree.add_command(ping_group)


# ── Anti-Link System ───────────────────────────────────────────────────────────

URL_REGEX = re.compile(
    r'(https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(/[^\s]*)?)',
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
        "enabled": False,
        "channel_ids": [],
        "allowed_domains": [],
        "blocked_domains": [],
    })


def update_antilink_config(guild_id: int, partial: dict):
    cfg = load_config()
    key = str(guild_id)
    if key not in cfg:
        cfg[key] = {}
    if "antilink" not in cfg[key]:
        cfg[key]["antilink"] = {
            "enabled": False,
            "channel_ids": [],
            "allowed_domains": [],
            "blocked_domains": [],
        }
    cfg[key]["antilink"].update(partial)
    save_config(cfg)


def is_link_allowed(url: str, antilink_cfg: dict) -> bool:
    """Restituisce True se il link è nella whitelist."""
    domain = extract_domain(url)
    allowed = antilink_cfg.get("allowed_domains", [])
    return any(domain == a or domain.endswith("." + a) for a in allowed)


def is_link_blocked(url: str, antilink_cfg: dict) -> bool:
    """Restituisce True se il link è nella blacklist."""
    domain = extract_domain(url)
    blocked = antilink_cfg.get("blocked_domains", [])
    return any(domain == b or domain.endswith("." + b) for b in blocked)


DM_WARNING = """⚠️ **Your message was removed.**

Your message in **{server}** contained a link that is not authorized in that channel.

Only approved links from verified domains are permitted. Posting unauthorized links, including referral links, third-party listings, or external platforms, is not allowed without prior permission.

If you believe this was a mistake or think you are entitled to post your link, please [open a support ticket](https://discord.com/channels/1383358337432813618/1416824721932161025) and our team will review your request.

— *Fight Kicks Staff*"""


@bot.event
async def on_message(message: discord.Message):
    if message.author.id == bot.user.id:
        return
    if message.guild is None:
        return

    guild_cfg = get_guild_config(message.guild.id)

    # ── Anti-link check ──
    antilink_cfg = get_antilink_config(message.guild.id)
    if antilink_cfg.get("enabled") and message.channel.id in antilink_cfg.get("channel_ids", []):
        # Esenzione: admin, owner, staff, bot autorizzati
        is_exempt = (
            message.author.id == message.guild.owner_id
            or message.author.guild_permissions.administrator
            or (message.author.bot and message.author.id in guild_cfg.get("allowed_bot_ids", []))
            or (isinstance(message.author, discord.Member) and
                {r.id for r in message.author.roles} & set(guild_cfg.get("staff_role_ids", [])))
        )

        if not is_exempt:
            urls = URL_REGEX.findall(message.content)
            urls = [u[0] if isinstance(u, tuple) else u for u in urls]
            for url in urls:
                if is_link_blocked(url, antilink_cfg) or not is_link_allowed(url, antilink_cfg):
                    try:
                        await message.delete()
                    except discord.Forbidden:
                        pass
                    try:
                        await message.author.send(
                            DM_WARNING.format(server=message.guild.name)
                        )
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


@antilink_group.command(name="enable", description="Attiva l'anti-link in un canale")
@app_commands.describe(canale="Canale dove attivare l'anti-link")
@app_commands.checks.has_permissions(administrator=True)
async def antilink_enable(interaction: discord.Interaction, canale: discord.TextChannel):
    cfg = get_antilink_config(interaction.guild.id)
    ids = cfg.get("channel_ids", [])
    if canale.id not in ids:
        ids.append(canale.id)
    update_antilink_config(interaction.guild.id, {"enabled": True, "channel_ids": ids})
    await interaction.response.send_message(f"✅ Anti-link attivato in {canale.mention}.", ephemeral=True)


@antilink_group.command(name="disable", description="Disattiva l'anti-link in un canale")
@app_commands.describe(canale="Canale dove disattivare l'anti-link")
@app_commands.checks.has_permissions(administrator=True)
async def antilink_disable(interaction: discord.Interaction, canale: discord.TextChannel):
    cfg = get_antilink_config(interaction.guild.id)
    ids = cfg.get("channel_ids", [])
    if canale.id in ids:
        ids.remove(canale.id)
    update_antilink_config(interaction.guild.id, {"channel_ids": ids})
    await interaction.response.send_message(f"✅ Anti-link disattivato in {canale.mention}.", ephemeral=True)


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


@antilink_group.command(name="block", description="Aggiunge un dominio alla blacklist")
@app_commands.describe(dominio="Dominio da bloccare (es. nordicsneakers.dk)")
@app_commands.checks.has_permissions(administrator=True)
async def antilink_block(interaction: discord.Interaction, dominio: str):
    dominio = dominio.lower().strip()
    cfg = get_antilink_config(interaction.guild.id)
    blocked = cfg.get("blocked_domains", [])
    if dominio in blocked:
        await interaction.response.send_message(f"ℹ️ `{dominio}` è già nella blacklist.", ephemeral=True)
        return
    blocked.append(dominio)
    update_antilink_config(interaction.guild.id, {"blocked_domains": blocked})
    await interaction.response.send_message(f"✅ `{dominio}` aggiunto alla blacklist.", ephemeral=True)


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


@antilink_group.command(name="config", description="Mostra la configurazione anti-link")
@app_commands.checks.has_permissions(administrator=True)
async def antilink_config(interaction: discord.Interaction):
    cfg = get_antilink_config(interaction.guild.id)
    channels = ", ".join(f"<#{c}>" for c in cfg.get("channel_ids", [])) or "*(nessuno)*"
    allowed = ", ".join(f"`{d}`" for d in cfg.get("allowed_domains", [])) or "*(nessuno)*"
    blocked = ", ".join(f"`{d}`" for d in cfg.get("blocked_domains", [])) or "*(nessuno)*"

    embed = discord.Embed(title="⚙️ Configurazione Anti-Link", color=discord.Color.blurple())
    embed.add_field(name="Stato", value="✅ Attivo" if cfg.get("enabled") else "❌ Disattivo", inline=False)
    embed.add_field(name="Canali monitorati", value=channels, inline=False)
    embed.add_field(name="Domini whitelist", value=allowed, inline=False)
    embed.add_field(name="Domini blacklist", value=blocked, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@antilink_enable.error
@antilink_disable.error
@antilink_allow.error
@antilink_block.error
@antilink_unallow.error
@antilink_config.error
async def antilink_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Solo gli amministratori possono usare questo comando.", ephemeral=True)


tree.add_command(antilink_group)


# ── Disconnessione e avvio ─────────────────────────────────────────────────────

@bot.event
async def on_disconnect():
    print("⚠️  Bot disconnesso — tentativo di riconnessione automatica...")


if __name__ == "__main__":
    import time
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Variabile d'ambiente DISCORD_BOT_TOKEN non impostata.")
    while True:
        try:
            bot.run(token)
        except Exception as e:
            print(f"❌ Errore: {e} — nuovo tentativo tra 10 secondi...")
            time.sleep(10)
