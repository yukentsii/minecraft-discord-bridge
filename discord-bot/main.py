"""
Minecraft & Discord Server Integration Bot
- Mojang API Account Verification & Interactive Whitelist System
- Bidirectional Live Console Bridge (#console)
- "Join to Create" Dynamic Temporary Voice Channels
"""

import os
import re
import sys
import json
import logging
import asyncio
import urllib.request
import urllib.error
from typing import Set, List, Tuple
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("discord-bridge")

# Bot Token
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN or TOKEN == "your_discord_bot_token_here":
    logger.error("DISCORD_BOT_TOKEN is not set in environment or .env file!")
    sys.exit(1)

# Voice Channel Trigger Configuration
raw_trigger_ids = os.getenv("TRIGGER_CHANNEL_ID", "")
TRIGGER_CHANNEL_IDS: Set[int] = set()
if raw_trigger_ids:
    for item in raw_trigger_ids.split(","):
        item = item.strip()
        if item.isdigit() and int(item) > 0:
            TRIGGER_CHANNEL_IDS.add(int(item))

raw_trigger_names = os.getenv(
    "TRIGGER_CHANNEL_NAMES",
    "Create Channel,Join to Create,+ New Channel,➕ Create Channel,➕ Join to Create,Oda Oluştur,➕ Oda Oluştur"
)
TRIGGER_CHANNEL_NAMES: List[str] = [name.strip().lower() for name in raw_trigger_names.split(",") if name.strip()]

CHANNEL_NAME_TEMPLATE = os.getenv("CHANNEL_NAME_TEMPLATE", "{username}'s Channel")
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_channels.json")

# Whitelist & Console Configuration
WHITELIST_REVIEW_CHANNEL_ID = int(os.getenv("WHITELIST_REVIEW_CHANNEL_ID", "0"))
CONSOLE_CHANNEL_ID = int(os.getenv("CONSOLE_CHANNEL_ID", "0"))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
WHITELIST_ROLE_ID = int(os.getenv("WHITELIST_ROLE_ID", "0"))
MC_WHITELIST_PATH = os.getenv("MC_WHITELIST_PATH", "whitelist.json")
MC_MOD_API_URL = os.getenv("MC_MOD_API_URL", "http://127.0.0.1:25585")
SERVER_NAME = os.getenv("SERVER_NAME", "Minecraft Server")

# State Management
temp_channels: Set[int] = set()
user_locks: dict[int, asyncio.Lock] = {}
state_lock = asyncio.Lock()
whitelist_file_lock = asyncio.Lock()


def load_temp_channels() -> Set[int]:
    """Load tracked temporary channel IDs from persistent JSON storage."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(int(x) for x in data)
        except Exception as e:
            logger.warning(f"Failed to load {DATA_FILE}: {e}")
    return set()


def save_temp_channels():
    """Save tracked temporary channel IDs to persistent JSON storage."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(list(temp_channels), f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save {DATA_FILE}: {e}")


temp_channels = load_temp_channels()

# Bot Setup with required intents
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!bridge_", intents=intents)


# ==========================================================
#                  MOJANG & WHITELIST HELPERS
# ==========================================================

async def validate_mojang_account(username: str) -> Tuple[bool, str, str]:
    """
    Validate username with official Mojang API.
    Returns: (is_valid, exact_canonical_name, dashed_uuid)
    """
    clean_username = username.strip()
    if not clean_username or len(clean_username) > 16 or not re.match(r"^[a-zA-Z0-9_]{3,16}$", clean_username):
        return False, "", ""

    url = f"https://api.mojang.com/users/profiles/minecraft/{clean_username}"

    def _fetch():
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Minecraft-Discord-Bridge/1.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw)
                return None
        except urllib.error.HTTPError as err:
            if err.code in (404, 204):
                return None
            logger.warning(f"Mojang API HTTPError for {username}: {err.code}")
            return None
        except Exception as err:
            logger.error(f"Error querying Mojang API for {username}: {err}")
            return None

    data = await asyncio.to_thread(_fetch)
    if data and isinstance(data, dict) and "id" in data and "name" in data:
        raw_id = data["id"]
        dashed_uuid = f"{raw_id[:8]}-{raw_id[8:12]}-{raw_id[12:16]}-{raw_id[16:20]}-{raw_id[20:]}"
        return True, data["name"], dashed_uuid

    return False, "", ""


def read_whitelist_entries() -> list:
    """Read whitelist.json file safely."""
    if os.path.exists(MC_WHITELIST_PATH):
        try:
            with open(MC_WHITELIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.error(f"Error reading whitelist file ({MC_WHITELIST_PATH}): {e}")
    return []


def add_to_whitelist_file(uuid_str: str, name_str: str) -> bool:
    """Add player to Minecraft server whitelist.json file."""
    try:
        entries = read_whitelist_entries()
        for entry in entries:
            if entry.get("uuid") == uuid_str or entry.get("name", "").lower() == name_str.lower():
                return True

        entries.append({
            "uuid": uuid_str,
            "name": name_str
        })
        os.makedirs(os.path.dirname(os.path.abspath(MC_WHITELIST_PATH)), exist_ok=True)
        with open(MC_WHITELIST_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
        logger.info(f"Added {name_str} ({uuid_str}) to {MC_WHITELIST_PATH}")
        return True
    except Exception as e:
        logger.error(f"Error saving to whitelist file ({MC_WHITELIST_PATH}): {e}")
        return False


async def notify_minecraft_mod(name: str, uuid: str):
    """Notify the Fabric mod in-game API to immediately add player in real time."""
    try:
        payload = json.dumps({"name": name, "uuid": uuid}).encode("utf-8")
        def _post():
            req = urllib.request.Request(
                f"{MC_MOD_API_URL}/api/whitelist/add",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "DiscordBridgeBot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status

        status = await asyncio.to_thread(_post)
        logger.info(f"Notified Fabric mod for '{name}': HTTP {status}")
    except Exception as e:
        logger.debug(f"Fabric mod API not active (server may be offline): {e}")


def is_admin(member: discord.Member) -> bool:
    """Check if a member is administrator or has admin role."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    if ADMIN_ROLE_ID and any(role.id == ADMIN_ROLE_ID for role in member.roles):
        return True
    return False


# ==========================================================
#                   DISCORD UI COMPONENTS
# ==========================================================

class WhitelistModal(discord.ui.Modal, title="Minecraft Whitelist Başvurusu"):
    mc_username = discord.ui.TextInput(
        label="Minecraft Kullanıcı Adınız",
        placeholder="Örn: Steve",
        min_length=3,
        max_length=16,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_name = self.mc_username.value.strip()

        # 1. Validate with Mojang
        is_valid, exact_name, dashed_uuid = await validate_mojang_account(raw_name)
        if not is_valid:
            await interaction.followup.send(
                f"❌ **`{raw_name}`** adında geçerli bir orijinal Minecraft/Mojang hesabı bulunamadı!\n"
                "Lütfen kullanıcı adınızı doğru yazdığınızdan ve orijinal bir hesaba sahip olduğunuzdan emin olun.",
                ephemeral=True
            )
            return

        # 2. Check if already whitelisted
        entries = read_whitelist_entries()
        if any(e.get("name", "").lower() == exact_name.lower() or e.get("uuid") == dashed_uuid for e in entries):
            await interaction.followup.send(
                f"⚠️ **`{exact_name}`** hesabı sunucumuzun whitelist listesinde zaten kayıtlı!",
                ephemeral=True
            )
            return

        # 3. Locate Staff Review Channel
        if not WHITELIST_REVIEW_CHANNEL_ID:
            await interaction.followup.send("⚠️ Yetkili inceleme kanalı yapılandırılmamış.", ephemeral=True)
            return

        review_channel = bot.get_channel(WHITELIST_REVIEW_CHANNEL_ID)
        if review_channel is None:
            try:
                review_channel = await bot.fetch_channel(WHITELIST_REVIEW_CHANNEL_ID)
            except Exception:
                review_channel = None

        if not review_channel:
            await interaction.followup.send(
                "⚠️ Yetkili inceleme kanalı bulunamadı. Lütfen sunucu yöneticileriyle iletişime geçin.",
                ephemeral=True
            )
            return

        # 4. Build Review Embed
        avatar_url = f"https://mc-heads.net/avatar/{exact_name}/128.png"
        embed = discord.Embed(
            title="📋 Yeni Whitelist Başvurusu",
            description=f"{interaction.user.mention} Minecraft sunucusu için whitelist başvurusunda bulundu.",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="👤 Discord Kullanıcısı", value=f"{interaction.user.mention} (`{interaction.user.name}`)", inline=False)
        embed.add_field(name="🎮 Minecraft Kullanıcı Adı", value=f"**`{exact_name}`**", inline=True)
        embed.add_field(name="🆔 Mojang UUID", value=f"`{dashed_uuid}`", inline=True)
        embed.set_footer(text=f"Kullanıcı ID: {interaction.user.id}")

        view = WhitelistReviewView(applicant_id=interaction.user.id, mc_name=exact_name, mc_uuid=dashed_uuid)
        await review_channel.send(embed=embed, view=view)

        await interaction.followup.send(
            f"✅ **`{exact_name}`** hesabı için başvurunuz yetkililere iletildi!\n"
            "İnceleme tamamlandığında Discord üzerinden bilgilendirileceksiniz.",
            ephemeral=True
        )


class WhitelistLaunchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Whitelist Başvurusu Yap",
        style=discord.ButtonStyle.success,
        emoji="🎮",
        custom_id="btn_whitelist_apply_main"
    )
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WhitelistModal())


class WhitelistReviewView(discord.ui.View):
    def __init__(self, applicant_id: int, mc_name: str, mc_uuid: str):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.mc_name = mc_name
        self.mc_uuid = mc_uuid

        self.approve_btn = discord.ui.Button(
            label="Ekle",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"wlap_{applicant_id}_{mc_name}_{mc_uuid}"
        )
        self.approve_btn.callback = self.approve_callback
        self.add_item(self.approve_btn)

        self.reject_btn = discord.ui.Button(
            label="Reddet",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"wldn_{applicant_id}_{mc_name}_{mc_uuid}"
        )
        self.reject_btn.callback = self.reject_callback
        self.add_item(self.reject_btn)

    async def approve_callback(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ Bu işlemi gerçekleştirme yetkiniz yok!", ephemeral=True)
            return

        await interaction.response.defer()

        # 1. Add to whitelist.json
        async with whitelist_file_lock:
            success = add_to_whitelist_file(self.mc_uuid, self.mc_name)

        if not success:
            await interaction.followup.send("⚠️ whitelist.json dosyasına kayıt yapılamadı!", ephemeral=True)
            return

        # 2. Trigger in-game Fabric mod API
        asyncio.create_task(notify_minecraft_mod(self.mc_name, self.mc_uuid))

        # 3. Grant Discord Whitelist Role
        guild = interaction.guild
        applicant = guild.get_member(self.applicant_id) if guild else None
        if applicant is None and guild:
            try:
                applicant = await guild.fetch_member(self.applicant_id)
            except Exception:
                applicant = None

        if applicant and WHITELIST_ROLE_ID:
            role = guild.get_role(WHITELIST_ROLE_ID)
            if role:
                try:
                    await applicant.add_roles(role, reason=f"Whitelist onaylandı ({self.mc_name}) - Yetkili: {interaction.user}")
                except Exception as e:
                    logger.warning(f"Whitelist rolü verilemedi: {e}")

        # 4. Update staff embed
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.title = "✅ Whitelist Başvurusu Onaylandı"
            embed.add_field(name="🛡️ Onaylayan Yetkili", value=f"{interaction.user.mention}", inline=False)

            for item in self.children:
                item.disabled = True
            await interaction.message.edit(embed=embed, view=self)

        # 5. Send DM to applicant
        if applicant:
            try:
                dm_embed = discord.Embed(
                    title="🎉 Whitelist Başvurunuz Onaylandı!",
                    description=f"Merhaba **{applicant.display_name}**,\n**{SERVER_NAME}** için yaptığınız whitelist başvurusu onaylandı.",
                    color=discord.Color.green()
                )
                dm_embed.add_field(name="🎮 Kayıt Edilen Kullanıcı Adı", value=f"`{self.mc_name}`", inline=False)
                dm_embed.add_field(name="🌐 Durum", value="Whitelist'e eklendiniz ve rolünüz verildi. İyi oyunlar dileriz!", inline=False)
                dm_embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{self.mc_name}/100.png")
                await applicant.send(embed=dm_embed)
            except Exception:
                pass

    async def reject_callback(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ Bu işlemi gerçekleştirme yetkiniz yok!", ephemeral=True)
            return

        await interaction.response.defer()

        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.title = "❌ Whitelist Başvurusu Reddedildi"
            embed.add_field(name="🛡️ Reddeden Yetkili", value=f"{interaction.user.mention}", inline=False)

            for item in self.children:
                item.disabled = True
            await interaction.message.edit(embed=embed, view=self)

        guild = interaction.guild
        applicant = guild.get_member(self.applicant_id) if guild else None
        if applicant is None and guild:
            try:
                applicant = await guild.fetch_member(self.applicant_id)
            except Exception:
                applicant = None

        if applicant:
            try:
                dm_embed = discord.Embed(
                    title="❌ Whitelist Başvurunuz Reddedildi",
                    description=f"Merhaba **{applicant.display_name}**,\n**{SERVER_NAME}** için yaptığınız `{self.mc_name}` whitelist başvurusu yetkililer tarafından onaylanmadı.",
                    color=discord.Color.red()
                )
                await applicant.send(embed=dm_embed)
            except Exception:
                pass


# Background listener for custom dynamic IDs across restarts
@bot.listen("on_interaction")
async def handle_dynamic_review_buttons(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("wlap_") or cid.startswith("wldn_"):
            parts = cid.split("_", 3)
            if len(parts) == 4:
                prefix, app_id_str, mc_name, mc_uuid = parts
                if app_id_str.isdigit():
                    view = WhitelistReviewView(int(app_id_str), mc_name, mc_uuid)
                    if prefix == "wlap":
                        await view.approve_callback(interaction)
                    else:
                        await view.reject_callback(interaction)


# ==========================================================
#                   CONSOLE BRIDGE LOGIC
# ==========================================================

async def console_log_worker():
    """Background task to stream logs from Minecraft Fabric mod to Discord #console."""
    await bot.wait_until_ready()
    if not CONSOLE_CHANNEL_ID:
        logger.info("CONSOLE_CHANNEL_ID not set. Console log stream worker inactive.")
        return

    logger.info(f"Started Minecraft console log streaming worker for channel {CONSOLE_CHANNEL_ID}")

    while not bot.is_closed():
        try:
            channel = bot.get_channel(CONSOLE_CHANNEL_ID)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(CONSOLE_CHANNEL_ID)
                except Exception:
                    channel = None

            if channel is not None:
                def _get_logs():
                    try:
                        req = urllib.request.Request(
                            f"{MC_MOD_API_URL}/api/console/logs",
                            headers={"User-Agent": "DiscordBridgeBot/1.0"}
                        )
                        with urllib.request.urlopen(req, timeout=2) as resp:
                            if resp.status == 200:
                                return json.loads(resp.read().decode("utf-8"))
                    except Exception:
                        return None
                    return None

                data = await asyncio.to_thread(_get_logs)
                if data and isinstance(data, dict) and data.get("status") == "ok":
                    logs = data.get("logs", [])
                    if logs:
                        current_chunk = []
                        current_len = 0
                        for line in logs:
                            clean_line = line.strip()
                            if not clean_line:
                                continue
                            line_len = len(clean_line) + 1
                            if current_len + line_len > 1800 and current_chunk:
                                chunk_text = "```\n" + "\n".join(current_chunk) + "\n```"
                                await channel.send(chunk_text)
                                current_chunk = []
                                current_len = 0
                                await asyncio.sleep(0.3)

                            current_chunk.append(clean_line)
                            current_len += line_len

                        if current_chunk:
                            chunk_text = "```\n" + "\n".join(current_chunk) + "\n```"
                            await channel.send(chunk_text)
        except Exception as e:
            logger.debug(f"console_log_worker error: {e}")

        await asyncio.sleep(0.8)


@bot.event
async def on_message(message: discord.Message):
    """Handle console commands typed by admins in #console."""
    if message.author.bot:
        return

    # Check if message is in console channel
    if CONSOLE_CHANNEL_ID and message.channel.id == CONSOLE_CHANNEL_ID:
        if is_admin(message.author):
            command = message.content.strip()
            if command:
                if command.startswith("/"):
                    command = command[1:].strip()

                def _execute_cmd():
                    try:
                        payload = json.dumps({"command": command}).encode("utf-8")
                        req = urllib.request.Request(
                            f"{MC_MOD_API_URL}/api/console/execute",
                            data=payload,
                            headers={"Content-Type": "application/json", "User-Agent": "DiscordBridgeBot/1.0"}
                        )
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            return resp.status
                    except Exception as err:
                        logger.debug(f"Console execute error: {err}")
                        return None

                await asyncio.to_thread(_execute_cmd)
        return

    await bot.process_commands(message)


# ==========================================================
#                   VOICE CHANNEL LOGIC
# ==========================================================

def is_trigger_channel(channel: discord.VoiceChannel) -> bool:
    """Determine whether a voice channel is a 'Join to Create' generator channel."""
    if not isinstance(channel, discord.VoiceChannel):
        return False
    if channel.id in TRIGGER_CHANNEL_IDS:
        return True
    if channel.name.lower() in TRIGGER_CHANNEL_NAMES:
        return True
    return False


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Retrieve or create an asyncio lock for a user to prevent creation race conditions."""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


@bot.event
async def on_ready():
    """Lifecycle event when bot successfully connects to Discord Gateway."""
    logger.info(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guild(s)")
    if TRIGGER_CHANNEL_IDS:
        logger.info(f"Trigger Channel IDs configured: {list(TRIGGER_CHANNEL_IDS)}")
    logger.info(f"Trigger Channel Names matching: {TRIGGER_CHANNEL_NAMES}")
    logger.info(f"Whitelist Review Channel: {WHITELIST_REVIEW_CHANNEL_ID}")
    logger.info(f"Console Bridge Channel: {CONSOLE_CHANNEL_ID}")

    # Register persistent view so Whitelist button keeps working across restarts
    bot.add_view(WhitelistLaunchView())

    # Start background console log streaming worker
    asyncio.create_task(console_log_worker())

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"Successfully synced {len(synced)} slash command(s).")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")

    # Set activity presence
    activity = discord.Activity(
        type=discord.ActivityType.listening,
        name="Join to Create & Whitelist"
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)

    # Reconcile persisted temporary channels and clean up orphaned ones
    async with state_lock:
        stale_channels = set()
        for ch_id in list(temp_channels):
            channel = bot.get_channel(ch_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(ch_id)
                except (discord.NotFound, discord.Forbidden):
                    channel = None

            if channel is None:
                stale_channels.add(ch_id)
            elif isinstance(channel, discord.VoiceChannel):
                if len(channel.members) == 0:
                    try:
                        logger.info(f"Reconciliation: Deleting empty orphaned channel '{channel.name}' ({channel.id})")
                        await channel.delete(reason="Reconciliation: Empty temporary channel on startup")
                    except Exception as e:
                        logger.warning(f"Could not delete channel {ch_id}: {e}")
                    stale_channels.add(ch_id)
                else:
                    logger.info(f"Reconciliation: Keeping active temporary channel '{channel.name}' ({channel.id}) with {len(channel.members)} member(s)")

        for ch_id in stale_channels:
            temp_channels.discard(ch_id)
        save_temp_channels()


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Handle member voice updates (join, leave, switch)."""
    if member.bot:
        return

    # Scenario 1: Member left or moved away from a temporary channel
    if before.channel is not None and before.channel != after.channel:
        if before.channel.id in temp_channels:
            if len(before.channel.members) == 0:
                async with state_lock:
                    temp_channels.discard(before.channel.id)
                    save_temp_channels()
                try:
                    logger.info(f"Cleaning up empty temporary channel '{before.channel.name}' (ID: {before.channel.id})")
                    await before.channel.delete(reason="Temporary voice channel is empty")
                except discord.NotFound:
                    pass
                except Exception as e:
                    logger.warning(f"Failed to delete channel '{before.channel.name}': {e}")

    # Scenario 2: Member joined a trigger ("Join to Create") channel
    if after.channel is not None and before.channel != after.channel:
        if is_trigger_channel(after.channel):
            asyncio.create_task(handle_channel_creation(member, after.channel))


async def handle_channel_creation(member: discord.Member, trigger_channel: discord.VoiceChannel):
    """Create a temporary channel for the user, grant permissions, and move the member."""
    lock = get_user_lock(member.id)
    if lock.locked():
        return

    async with lock:
        guild = member.guild
        category = trigger_channel.category

        clean_name = member.display_name.strip()
        channel_name = CHANNEL_NAME_TEMPLATE.format(
            username=clean_name,
            name=clean_name,
            user=member.name
        )
        if len(channel_name) > 100:
            channel_name = channel_name[:97] + "..."

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                connect=True,
                speak=True,
                view_channel=True
            ),
            member: discord.PermissionOverwrite(
                manage_channels=True,
                connect=True,
                speak=True,
                view_channel=True,
                mute_members=False,
                deafen_members=False,
                move_members=False
            ),
            guild.me: discord.PermissionOverwrite(
                manage_channels=True,
                move_members=True,
                connect=True,
                view_channel=True
            )
        }

        if category:
            for target, overwrite in category.overwrites.items():
                if target not in overwrites:
                    overwrites[target] = overwrite

        try:
            new_channel = await guild.create_voice_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                bitrate=min(trigger_channel.bitrate, guild.bitrate_limit),
                user_limit=trigger_channel.user_limit,
                reason=f"Temporary channel created for {member.display_name} ({member.id})"
            )
            logger.info(f"Created temporary voice channel '{new_channel.name}' (ID: {new_channel.id}) for {member.display_name}")

            async with state_lock:
                temp_channels.add(new_channel.id)
                save_temp_channels()

            if member.voice is None or member.voice.channel != trigger_channel:
                logger.info(f"User {member.display_name} left or switched before moving. Checking channel status...")
                if len(new_channel.members) == 0:
                    async with state_lock:
                        temp_channels.discard(new_channel.id)
                        save_temp_channels()
                    await new_channel.delete(reason="User disconnected before moving into created channel")
                    logger.info(f"Cleaned up empty channel '{new_channel.name}' (ID: {new_channel.id})")
                return

            await member.move_to(new_channel, reason="Moved creator into newly created temporary channel")
            logger.info(f"Successfully moved {member.display_name} to '{new_channel.name}'")

        except discord.Forbidden as e:
            logger.error(f"Permission error creating or managing channel: {e}")
        except discord.HTTPException as e:
            logger.error(f"Discord HTTP exception during channel creation: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in handle_channel_creation: {e}", exc_info=True)


# ==========================================================
#                   SLASH / PREFIX COMMANDS
# ==========================================================

@bot.tree.command(name="whitelist-paneli-kur", description="Minecraft Whitelist başvuru panelini bu kanala kurar.")
@discord.app_commands.default_permissions(administrator=True)
async def slash_setup_whitelist_panel(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Bu komutu sadece yöneticiler kullanabilir.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(
        "Whitelist'e eklenmek için aşağıdaki butona tıklayın.",
        view=WhitelistLaunchView()
    )
    await interaction.followup.send("✅ Whitelist paneli kuruldu!", ephemeral=True)


@bot.command(name="whitelist_kur")
@commands.has_permissions(administrator=True)
async def prefix_setup_whitelist_panel(ctx: commands.Context):
    if not is_admin(ctx.author):
        await ctx.send("❌ Bu komutu sadece yöneticiler kullanabilir.")
        return

    await ctx.send(
        "Whitelist'e eklenmek için aşağıdaki butona tıklayın.",
        view=WhitelistLaunchView()
    )


if __name__ == "__main__":
    logger.info("Starting Discord Auto Voice Channel, Whitelist & Console Bot...")
    bot.run(TOKEN)
