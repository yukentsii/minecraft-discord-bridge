# 🎮 Minecraft & Discord Server Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Minecraft Fabric](https://img.shields.io/badge/Minecraft-Fabric%2026.2-brightgreen.svg)](https://fabricmc.net/)
[![Java](https://img.shields.io/badge/Java-25-orange.svg)](https://adoptium.net/)

A production-ready integration between a Minecraft Fabric server and Discord. Features automated Mojang API whitelist verification, in-game real-time synchronization with zero server downtime, and a bidirectional live console bridge.

---

## 🌟 Key Features

### 1. 🛡️ Mojang Account Verification & Automated Whitelisting
- **Interactive UI Modals**: Players click an interactive Discord button to submit their Minecraft username via private pop-up modal.
- **Official Mojang API Validation**: Verifies that the username belongs to an official, paid Minecraft account, resolves the canonical casing, dashed UUID, and 3D skin avatar preview. Rejects pirated or invalid names instantly.
- **Admin Review Pipeline**: Sends formatted review cards to staff channels with `[ ✅ Approve ]` and `[ ❌ Reject ]` buttons.
- **Instant Synchronization**: On approval, the bot writes to `whitelist.json`, dispatches an in-game live reload event to the Fabric mod, assigns the Discord `@whitelist` role, and sends a DM confirmation.

### 2. 🖥️ Bidirectional Live Console Bridge
- **Log4j 2 Streaming Appender**: Captures all server logs, player chat, command outputs, and exceptions in real time directly from the Minecraft server engine.
- **Rate-Limited Discord Feed**: Batches console output into clean, dark-themed code blocks sent to a dedicated `#console` channel.
- **Remote Console Execution**: Server administrators can type raw console commands directly into `#console` (e.g. `say Server restart in 5m`, `whitelist list`, `op PlayerName`) to execute them in-game without logging into the VPS.

---

## 🏗️ Architecture

```
[ Discord Client ]
       │
       ▼
[ Python Discord Bot ]  (discord.py)
       │  • Mojang API validation (https://api.mojang.com)
       │  • Whitelist review modals & role management
       │  • Console stream listener & command router
       │
       ▼  HTTP (127.0.0.1:25585)
[ Minecraft Server (Fabric) ]
       │  • DiscordWhitelistMod (Embedded HTTP Server)
       │  • Log4j2 Custom Console Appender
       │  • Live Whitelist Engine (Zero-downtime reloads)
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Java 25 (or version matching your Fabric Minecraft server)
- Minecraft Fabric Server (26.2+) with Fabric API

---

### 1. Fabric Mod Setup

1. Clone or download this repository:
   ```bash
   git clone https://github.com/yukentsii/minecraft-discord-bridge.git
   cd minecraft-discord-bridge/fabric-mod
   ```
2. Build the mod `.jar` with Gradle:
   ```bash
   ./gradlew build
   ```
3. Copy the compiled `.jar` from `build/libs/` into your Minecraft server's `mods/` directory:
   ```bash
   cp build/libs/*.jar /path/to/minecraft/mods/
   ```
4. Start your Minecraft server. The mod will automatically initialize its internal API listener on `127.0.0.1:25585`.

---

### 2. Discord Bot Setup

1. Navigate to the `discord-bot` directory:
   ```bash
   cd ../discord-bot
   ```
2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure your environment variables:
   ```bash
   cp .env.example .env
   nano .env
   ```
   Fill in your Discord bot token, server IDs, and paths:
   - `DISCORD_BOT_TOKEN`: Token from Discord Developer Portal.
   - `WHITELIST_REVIEW_CHANNEL_ID`: Channel ID for staff to approve/reject requests.
   - `CONSOLE_CHANNEL_ID`: Channel ID for live console feed and remote commands.
   - `ADMIN_ROLE_ID`: Role ID authorized to approve requests and execute console commands.
   - `WHITELIST_ROLE_ID`: Role ID granted to approved players.
   - `MC_WHITELIST_PATH`: Path to your Minecraft server's `whitelist.json`.
   - `MC_MOD_API_URL`: Default is `http://127.0.0.1:25585`.

5. Run the bot:
   ```bash
   python3 main.py
   ```

6. In Discord, run `/whitelist-paneli-kur` in the public channel where players should apply.

---

### 3. Production Deployment (systemd)

To run the bot as a background service on Linux:

1. Copy the unit file:
   ```bash
   sudo cp discord-bot.service.example /etc/systemd/system/discord-bridge.service
   ```
2. Edit `/etc/systemd/system/discord-bridge.service` to match your user and paths.
3. Enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now discord-bridge.service
   sudo systemctl status discord-bridge.service
   ```

---

## 🔒 Security & Privacy

- **No Public RCON Required**: The bot communicates locally over `127.0.0.1`, keeping management ports strictly off public interfaces.
- **Strict Role Verification**: Console execution and whitelist approvals verify Discord administrator permissions and configured staff role IDs on every interaction.
- **Mojang Identity Verification**: Submissions are strictly checked against official Mojang UUID endpoints to prevent spoofing.

---

## 📄 License

This project is open-source and licensed under the [MIT License](LICENSE).
