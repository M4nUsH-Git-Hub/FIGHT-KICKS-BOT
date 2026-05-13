"""
Discord Ping Backup Bot
=======================
Archivia automaticamente tutti i messaggi con @everyone, @here o menzioni di ruolo
inviati da owner, bot autorizzati e staff nel canale #ping-backup.

Comandi slash:
  /pingbackup setup    — imposta il canale di backup (crea o specifica)
  /pingbackup addstaff — aggiunge un ruolo allo staff autorizzato
  /pingbackup rmstaff  — rimuove un ruolo dallo staff
  /pingbackup addbot   — aggiunge un bot alla whitelist
  /pingbackup rmbot    — rimuove un bot dalla whitelist
  /pingbackup config   — mostra la configurazione attuale
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
        cfg[key] = {"backup_channel_id": None, "staff_role_ids": [], "allowed_bot_ids": []}
    cfg[key].update(partial)
    save_config(cfg)


# ── Setup intents ──────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # privileged intent — da abilitare nel portale developer
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ── Utility ───────────────────────────────────────────────────────────────────

def is_authorized(message: discord.Message, guild_cfg: dict) -> bool:
    """Restituisce True se il mittente è autorizzato a far archiviare il ping."""
    author = message.author
    guild = message.guild
    if guild is None:
        return False

    # Proprietario del server
    if author.id == guild.owner_id:
        return True

    # Bot nella whitelist
    if author.bot and author.id in guild_cfg.get("allowed_bot_ids", []):
        return True

    # Membro con ruolo staff autorizzato
    if isinstance(author, discord.Member):
        member_role_ids = {r.id for r in author.roles}
        if member_role_ids & set(guild_cfg.get("staff_role_ids", [])):
            return True

    return False


def has_ping(message: discord.Message) -> bool:
    """Restituisce True se il messaggio contiene @everyone, @here o una menzione di ruolo."""
    if message.mention_everyone:
        return True
    if message.role_mentions:
        return True
    # Rileva menzioni di ruoli non menzionabili pubblicamente (<@&ID>)
    if re.search(r'<@&\d+>', message.content):
        return True
    return False


def count_mentions_this_week(guild_id: int, role_label: str) -> int:
    """Conta quante volte un ruolo è stato pingato negli ultimi 7 giorni."""
    cfg = load_config()
    key = str(guild_id)
    history = cfg.get(key, {}).get("mention_history", {})
    role_history = history.get(role_label, [])
    now = datetime.now(timezone.utc)
    week_ago = now.timestamp() - 7 * 24 * 3600
    return sum(1 for t in role_history if t >= week_ago)


def record_mention(guild_id: int, role_label: str):
    """Registra un ping nel contatore settimanale."""
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
    # Pulizia: tieni solo gli ultimi 30 giorni
    week_ago = now.timestamp() - 30 * 24 * 3600
    history[role_label] = [t for t in history[role_label] if t >= week_ago]
    save_config(cfg)


def build_embed(message: discord.Message) -> discord.Embed:
    """Costruisce l'embed da inviare nel canale di backup."""
    author = message.author
    channel = message.channel

    # Determina il tipo di ping
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
    # Ruoli non menzionabili pubblicamente — rilevati tramite <@&ID>
    mentioned_role_ids = {r.id for r in message.role_mentions}
    for match in re.finditer(r'<@&(\d+)>', message.content):
        rid = int(match.group(1))
        if rid not in mentioned_role_ids:
            role_obj = message.guild.get_role(rid)
            name = role_obj.name if role_obj else str(rid)
            ping_types.append(f"@{name}")

    ping_label = ", ".join(ping_types) if ping_types else "Role mention"

    # Registra il ping e conta quante volte questa settimana
    record_mention(message.guild.id, ping_label)
    weekly_count = count_mentions_this_week(message.guild.id, ping_label)

    ping_label_no_at = ping_label.replace("@", "")
    ping_label_spaced = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', ping_label_no_at)

    embed = discord.Embed(
        title=f"New Mention : {ping_label_spaced}",
        color=discord.Color(0x6B6B6B),
        timestamp=message.created_at,
    )

    embed.add_field(name="Author", value=author.mention, inline=True)
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Message", value=f"[Click here]({message.jump_url})", inline=True)

    # Immagini — mostra solo la prima
    images = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
    if images:
        embed.set_image(url=images[0].url)

    # Footer con solo il contatore settimanale — Discord aggiunge il timestamp automaticamente
    embed.set_footer(
        text=f"{weekly_count} | Mentions this week",
        icon_url="https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS/main/SCURO.png"
    )

    return embed


# ── Evento: on_message ─────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online come {bot.user} (ID: {bot.user.id})")
    print("Comandi slash sincronizzati globalmente.")


@bot.event
async def on_message(message: discord.Message):
    # Ignora i messaggi del bot stesso
    if message.author.id == bot.user.id:
        return

    # Solo nei server
    if message.guild is None:
        return

    guild_cfg = get_guild_config(message.guild.id)
    backup_channel_id = guild_cfg.get("backup_channel_id")

    if not backup_channel_id:
        return  # canale backup non configurato

    if not has_ping(message):
        return  # nessun ping nel messaggio

    # Canali aperti — archivia ping di chiunque
    open_channel_ids = guild_cfg.get("open_channel_ids", [])
    if message.channel.id not in open_channel_ids:
        if not is_authorized(message, guild_cfg):
            return  # mittente non autorizzato

    backup_channel = message.guild.get_channel(backup_channel_id)
    if backup_channel is None:
        return  # canale non trovato

    embed = build_embed(message)
    try:
        await backup_channel.send(embed=embed)
    except discord.Forbidden:
        print(f"⚠️  Permesso negato per inviare in #{backup_channel.name}")
    except discord.HTTPException as e:
        print(f"⚠️  Errore HTTP durante l'invio dell'embed: {e}")

    await bot.process_commands(message)


# ── Gruppo comandi slash /pingbackup ──────────────────────────────────────────

class PingBackupGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="pingbackup", description="Gestione del bot ping backup")


ping_group = PingBackupGroup()


@ping_group.command(name="setup", description="Imposta o crea il canale di backup per i ping")
@app_commands.describe(canale="Canale esistente da usare (opzionale — ne crea uno nuovo se omesso)")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction, canale: discord.TextChannel = None):
    guild = interaction.guild

    if canale is None:
        # Crea un nuovo canale #ping-backup
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(send_messages=False, read_messages=False),
            guild.me: discord.PermissionOverwrite(send_messages=True, read_messages=True),
        }
        # Rende il canale visibile solo agli admin
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
        f"✅ Canale di backup impostato su {canale.mention}.\n"
        "Da ora tutti i ping da owner, staff e bot autorizzati verranno archiviati lì.",
        ephemeral=True,
    )


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


@ping_group.command(name="addbot", description="Aggiunge un bot alla whitelist (inserisci l'ID)")
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
    staff_str = (
        ", ".join(f"<@&{r}>" for r in staff_ids) if staff_ids else "*(nessuno)*"
    )

    bot_ids = cfg.get("allowed_bot_ids", [])
    bot_str = ", ".join(f"`{b}`" for b in bot_ids) if bot_ids else "*(nessuno)*"

    open_ids = cfg.get("open_channel_ids", [])
    open_str = ", ".join(f"<#{c}>" for c in open_ids) if open_ids else "*(nessuno)*"

    embed = discord.Embed(title="⚙️ Configurazione Ping Backup", color=discord.Color.blurple())
    embed.add_field(name="Canale backup", value=ch_str, inline=False)
    embed.add_field(name="Ruoli staff autorizzati", value=staff_str, inline=False)
    embed.add_field(name="Bot nella whitelist", value=bot_str, inline=False)
    embed.add_field(name="Canali aperti", value=open_str, inline=False)
    embed.set_footer(text=f"Owner del server: sempre autorizzato automaticamente")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# Gestione errori permessi comandi slash
@setup.error
@addstaff.error
@rmstaff.error
@addbot.error
@rmbot.error
@config_cmd.error
async def admin_only_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Solo gli amministratori possono usare questo comando.", ephemeral=True)


# Registra il gruppo
tree.add_command(ping_group)


@bot.event
async def on_disconnect():
    print("⚠️  Bot disconnesso — tentativo di riconnessione automatica...")


# ── Avvio ─────────────────────────────────────────────────────────────────────
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
