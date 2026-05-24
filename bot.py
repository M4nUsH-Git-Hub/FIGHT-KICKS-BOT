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


# ── Comando test sport ────────────────────────────────────────────────────────

@tree.command(name="sportnow", description="Invia subito le partite in chiaro di oggi (test)")
@app_commands.checks.has_permissions(administrator=True)
async def sportnow(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await send_sport_notification()
    await interaction.followup.send("✅ Notifica sport inviata!", ephemeral=True)

@sportnow.error
async def sportnow_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Solo gli amministratori possono usare questo comando.", ephemeral=True)


# ── Sistema notifiche partite in chiaro ───────────────────────────────────────

SPORT_CHANNEL_ID = 1505567358507421737
NOTIFY_HOUR = 8  # ora invio giornaliero (08:00)

CANALI_TARGET = [
    "como tv", "nove", "raiplay", "tv8", "canale 5",
    "italia 1", "dazn free", "rai 1", "rai 2", "rai sport", "sportitalia",
    "cielo", "lba tv", "lbatv"
]

FOOTER_ICON = "https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS/main/SCURO.png"


async def scrape_sport_chiaro(url: str) -> list:
    """Scrapa diretta.it per un dato sport e restituisce le partite in chiaro."""
    from playwright.async_api import async_playwright
    from bs4 import BeautifulSoup

    risultati = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(5000)
            content = await page.content()
            await browser.close()

        soup = BeautifulSoup(content, "html.parser")

        # Nuova struttura: trova tutti i blocchi "Canale TV" tramite data-testid
        tv_labels = soup.find_all(attrs={"data-testid": "wcl-scores-overline-02"})

        for label in tv_labels:
            if "Canale TV" not in label.get_text():
                continue

            # Il contenitore dei canali è il fratello successivo (wcl-links)
            canali_container = label.find_next_sibling(class_=lambda c: c and "wcl-links" in " ".join(c))
            if not canali_container:
                # prova col genitore
                parent = label.parent
                canali_container = parent.find(class_=lambda c: c and "wcl-links" in " ".join(c)) if parent else None

            if not canali_container:
                continue

            # Estrai i nomi dei canali dai link tv
            canale_links = canali_container.find_all("a", class_=lambda c: c and "wcl-tvStationLink" in " ".join(c))
            if not canale_links:
                continue

            # Filtra solo canali in chiaro
            canali_trovati = []
            for a_ch in canale_links:
                nome = a_ch.get("title", "") or a_ch.get_text(strip=True)
                href = a_ch.get("href", "")
                if any(c in nome.lower() for c in CANALI_TARGET):
                    canali_trovati.append({"nome": nome, "href": href})

            if not canali_trovati:
                continue

            # Risali al row della partita
            row = label.find_parent(class_="event__match")
            if not row:
                continue

            a = row.find("a", class_="eventRowLink")
            if not a:
                continue

            match_name = a.get("aria-label", "Partita sconosciuta")
            link = a.get("href", "")

            time_el = row.find(class_=lambda c: c and "event__time" in c)
            orario = time_el.get_text(strip=True) if time_el else "?"

            competition = "?"
            prev = row.find_previous_sibling()
            for _ in range(20):
                if prev is None:
                    break
                if prev.get("class") and "headerLeague__wrapper" in prev.get("class", []):
                    title_el = prev.find(id=lambda i: i and "header-league-title" in i)
                    if title_el:
                        competition = title_el.get_text(strip=True)
                    break
                prev = prev.find_previous_sibling()

            # Costruisci stringa canali con link diretti presi dal sito
            canali_str = ", ".join(
                f"[{c['nome']}]({c['href']})" if c['href'] else c['nome']
                for c in canali_trovati
            )

            risultati.append({
                "match": match_name,
                "orario": orario,
                "competition": competition,
                "canali": canali_str,
                "link": link
            })

    except Exception as e:
        print(f"⚠️ Errore scraping {url}: {e}")

    return risultati


async def scrape_partite_chiaro() -> list:
    return await scrape_sport_chiaro("https://www.diretta.it/calcio/")


async def scrape_tennis_chiaro() -> list:
    return await scrape_sport_chiaro("https://www.diretta.it/tennis/")


async def scrape_f1_chiaro() -> list:
    return await scrape_sport_chiaro("https://www.diretta.it/formula-1/")


async def scrape_motogp_chiaro() -> list:
    return await scrape_sport_chiaro("https://www.diretta.it/motogp/")


async def scrape_basket_chiaro() -> list:
    return await scrape_sport_chiaro("https://www.diretta.it/basket/")


async def scrape_ciclismo_chiaro() -> list:
    return await scrape_sport_chiaro("https://www.diretta.it/ciclismo/")



CANALE_LINKS = {
    "como tv": "https://tv.comofootball.com/",
    "raiplay": "https://www.raiplay.it/",
    "rai sport": "https://www.raiplay.it/dirette/raisport",
    "nove": "https://nove.tv/",
    "tv8": "https://www.tv8.it/streaming",
    "sportitalia": "https://www.sportitalia.it/",
    "rai 1": "https://www.raiplay.it/dirette/rai1",
    "rai 2": "https://www.raiplay.it/dirette/rai2",
    "canale 5": "https://www.mediasetplay.mediaset.it/diretta/canale5",
    "italia 1": "https://www.mediasetplay.mediaset.it/diretta/italia1",
    "dazn free": "https://www.dazn.com/",
    "cielo": "https://www.cielotv.it/streaming",
    "lba tv": "https://www.lbatv.com/",
    "lbatv": "https://www.lbatv.com/",
}


def format_canali(canali_str: str) -> str:
    """I canali arrivano già formattati con link dal sito, restituisce la stringa così com'è."""
    return canali_str


async def send_sport_notification():
    """Invia l'embed delle partite in chiaro nel canale sport."""
    channel = bot.get_channel(SPORT_CHANNEL_ID)
    if not channel:
        print("⚠️ Canale sport non trovato")
        return

    print("🔍 Scraping partite in chiaro...")
    calcio = await scrape_partite_chiaro()
    tennis = await scrape_tennis_chiaro()
    f1 = await scrape_f1_chiaro()
    motogp = await scrape_motogp_chiaro()
    basket = await scrape_basket_chiaro()
    ciclismo = await scrape_ciclismo_chiaro()

    if not calcio and not tennis and not f1 and not motogp and not basket and not ciclismo:
        print("ℹ️ Nessuna partita in chiaro oggi")
        return

    today = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    embed = discord.Embed(
        title=f"📺 Free Matches Today — {today}",
        description="Matches available for free on the following channels",
        color=discord.Color(0x6B6B6B),
        timestamp=datetime.now(timezone.utc),
    )

    for p in calcio:
        canali_formatted = format_canali(p["canali"])
        value = f"🕐 **{p['orario']}** | {canali_formatted}\n📊 Check on [Diretta.it]({p['link']})"
        embed.add_field(
            name=f"⚽ {p['match']} — {p['competition']}",
            value=value,
            inline=False
        )

    for p in tennis:
        canali_formatted = format_canali(p["canali"])
        value = f"🕐 **{p['orario']}** | {canali_formatted}\n📊 Check on [Diretta.it]({p['link']})"
        embed.add_field(
            name=f"🎾 {p['match']} — {p['competition']}",
            value=value,
            inline=False
        )

    for p in f1:
        canali_formatted = format_canali(p["canali"])
        value = f"🕐 **{p['orario']}** | {canali_formatted}\n📊 Check on [Diretta.it]({p['link']})"
        embed.add_field(
            name=f"🏎️ {p['match']} — {p['competition']}",
            value=value,
            inline=False
        )

    for p in motogp:
        canali_formatted = format_canali(p["canali"])
        value = f"🕐 **{p['orario']}** | {canali_formatted}\n📊 Check on [Diretta.it]({p['link']})"
        embed.add_field(
            name=f"🏍️ {p['match']} — {p['competition']}",
            value=value,
            inline=False
        )

    for p in basket:
        canali_formatted = format_canali(p["canali"])
        value = f"🕐 **{p['orario']}** | {canali_formatted}\n📊 Check on [Diretta.it]({p['link']})"
        embed.add_field(
            name=f"🏀 {p['match']} — {p['competition']}",
            value=value,
            inline=False
        )

    for p in ciclismo:
        canali_formatted = format_canali(p["canali"])
        value = f"🕐 **{p['orario']}** | {canali_formatted}\n📊 Check on [Diretta.it]({p['link']})"
        embed.add_field(
            name=f"🚴 {p['match']} — {p['competition']}",
            value=value,
            inline=False
        )

    embed.set_footer(text="Sport News", icon_url=FOOTER_ICON)
    await channel.send(embed=embed)
    print(f"✅ Inviate — {len(calcio)} calcio, {len(tennis)} tennis, {len(f1)} F1, {len(motogp)} MotoGP, {len(basket)} basket, {len(ciclismo)} ciclismo")


async def daily_sport_loop():
    """Loop giornaliero che invia le notifiche alle 08:00."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.now(timezone.utc)
        # Calcola prossime 08:00 UTC (07:00 Italia inverno, 06:00 estate — aggiusta se serve)
        target = now.replace(hour=NOTIFY_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target.replace(day=target.day + 1)
        wait_seconds = (target - now).total_seconds()
        print(f"⏰ Prossima notifica sport tra {int(wait_seconds//3600)}h {int((wait_seconds%3600)//60)}m")
        await asyncio.sleep(wait_seconds)
        await send_sport_notification()



# ── WTB Command ───────────────────────────────────────────────────────────────

WTB_CHANNEL_ID = 1416219889303027722  # ⚠️ Sostituisci con l'ID reale di #wtb-monitor

FOOTER_ICON_WTB = "https://raw.githubusercontent.com/M4nUsH-Git-Hub/FIGHT-KICKS/main/SCURO.png"

WTB_SERVER_LINK = "https://discord.gg/2aetYnaNSy"  # ⚠️ Sostituisci con il link reale



async def fetch_sneaker_image(nome: str, codice: str) -> str | None:
    """
    Cerca immagine su Bing con query nome + codice + StockX.
    Molto preciso grazie al codice SKU univoco.
    """
    import aiohttp
    import re
    import urllib.parse

    query = f"{nome} {codice} StockX"
    encoded = urllib.parse.quote(query)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        url = f"https://www.bing.com/images/search?q={encoded}&form=HDRSC2"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                text = await resp.text()

        matches = re.findall(r'murl&quot;:&quot;(https://[^&]+\.(?:jpg|jpeg|png|webp))&quot;', text)
        if matches:
            print(f"✅ Immagine trovata: {matches[0][:80]}")
            return matches[0]
    except Exception as e:
        print(f"⚠️ Bing Images fallito: {e}")

    print("❌ Nessuna immagine trovata")
    return None

@tree.command(name="wtb", description="Posta un annuncio WTB nel canale wtb-monitor")
@app_commands.describe(
    nome="Nome del prodotto (es. Air Jordan 4 Retro OG SP Nigel Sylvester)",
    taglia="Taglia EU (es. 43 1/3)",
    codice="Codice SKU (es. HF4340-800)",
    link="Link StockX del prodotto",
    condizione="Condizione (default: DSWT)",
)
async def wtb(
    interaction: discord.Interaction,
    nome: str,
    taglia: str,
    codice: str,
    link: str,
    condizione: str = "DSWT",
):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Solo il proprietario del server può usare questo comando.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    print(f"🔍 Cercando immagine: {nome} {codice}")
    img_url = await fetch_sneaker_image(nome, codice)

    channel = interaction.guild.get_channel(WTB_CHANNEL_ID)
    if not channel:
        await interaction.followup.send("❌ Canale wtb-monitor non trovato.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"{nome} - {taglia}",
        color=discord.Color(0x575553),
    )
    embed.description = (
        f"- {codice} – {condizione} – [STOCKX]({link})\n"
        f"- Contact {interaction.user.mention} privately via DM\n"
        f"- [FIGHT KICKS OFFICIAL WTB SERVER]({WTB_SERVER_LINK})"
    )

    if img_url:
        embed.set_image(url=img_url)

    embed.set_footer(text="WTB Monitor", icon_url=FOOTER_ICON_WTB)

    await channel.send(embed=embed)
    await interaction.followup.send(f"✅ WTB inviato — {nome} {taglia}", ephemeral=True)
    print(f"✅ WTB inviato — {nome} | img:{'✅' if img_url else '❌'}")


@wtb.error
async def wtb_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await interaction.response.send_message("❌ Errore nel comando WTB.", ephemeral=True)

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

    while True:
        try:
            bot.run(token)
        except Exception as e:
            print(f"❌ Errore: {e} — nuovo tentativo tra 10 secondi...")
            time.sleep(10)
