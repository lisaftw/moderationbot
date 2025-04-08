import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import logging
import datetime
import asyncio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("discord.log"),
        logging.StreamHandler()
    ]
)

# Bot configuration
class ModBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        
        super().__init__(command_prefix="!", intents=intents)
        self.config_file = "config.json"
        self.load_config()
        
    def load_config(self):
        # Load configuration or create default if not exists
        if os.path.exists(self.config_file):
            with open(self.config_file, "r") as f:
                self.config = json.load(f)
        else:
            self.config = {
                "log_channels": {},
                "warn_thresholds": {
                    "3": "timeout",
                    "5": "kick",
                    "7": "ban"
                },
                "warnings": {}
            }
            self.save_config()
    
    def save_config(self):
        with open(self.config_file, "w") as f:
            json.dump(self.config, f, indent=4)
    
    async def on_ready(self):
        logging.info(f"Bot is ready! Logged in as {self.user}")
        try:
            synced = await self.tree.sync()
            logging.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logging.error(f"Failed to sync commands: {e}")

bot = ModBot()

# Moderation functionality
async def log_action(guild, action, target, moderator, reason, duration=None):
    """Log a moderation action to the configured log channel"""
    guild_id = str(guild.id)
    if guild_id not in bot.config["log_channels"]:
        return
    
    log_channel_id = bot.config["log_channels"][guild_id]
    log_channel = guild.get_channel(log_channel_id)
    
    if not log_channel:
        return
    
    embed = discord.Embed(
        title=f"Moderation Action: {action}",
        color=discord.Color.red(),
        timestamp=datetime.datetime.now()
    )
    
    if hasattr(target, 'mention'):
        embed.add_field(name="User", value=f"{target.mention} ({target.name}#{target.discriminator})", inline=False)
    else:
        embed.add_field(name="Target", value=f"{target}", inline=False)
    
    embed.add_field(name="Moderator", value=f"{moderator.mention} ({moderator.name}#{moderator.discriminator})", inline=False)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    
    if duration:
        embed.add_field(name="Duration", value=duration, inline=False)
    
    if hasattr(target, 'id'):
        embed.set_footer(text=f"User ID: {target.id}")
    
    await log_channel.send(embed=embed)

# Helper function to create error embeds
async def send_error(interaction, message):
    embed = discord.Embed(
        title="Error",
        description=message,
        color=discord.Color.red()
    )
    
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)

# Setup command
@bot.tree.command(name="setup", description="Set up the moderation bot for this server")
@app_commands.default_permissions(administrator=True)
async def setup(interaction: discord.Interaction, log_channel: discord.TextChannel):
    """Set up the moderation bot for this server"""
    guild_id = str(interaction.guild_id)
    bot.config["log_channels"][guild_id] = log_channel.id
    bot.save_config()
    
    embed = discord.Embed(
        title="Setup Complete",
        description=f"Moderation logs will be sent to {log_channel.mention}",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)

# Ban command
@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.default_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = None, delete_days: int = 0):
    """Ban a user from the server"""
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await send_error(interaction, "You cannot ban someone with a role higher than or equal to yours.")
        return
    
    try:
        await user.ban(reason=reason, delete_message_days=delete_days)
        
        embed = discord.Embed(
            title="User Banned",
            description=f"{user.mention} has been banned from the server.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason or "No reason provided")
        
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "Ban", user, interaction.user, reason)
        
    except discord.Forbidden:
        await send_error(interaction, "I don't have permission to ban that user.")
    except Exception as e:
        await send_error(interaction, f"An error occurred: {str(e)}")

# Unban command
@bot.tree.command(name="unban", description="Unban a user from the server")
@app_commands.default_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str, reason: str = None):
    """Unban a user from the server using their ID"""
    try:
        user_id = int(user_id)
        banned_users = [entry async for entry in interaction.guild.bans()]
        user = discord.utils.get(banned_users, user=discord.Object(id=user_id))
        
        if not user:
            await send_error(interaction, "This user is not banned.")
            return
        
        await interaction.guild.unban(discord.Object(id=user_id), reason=reason)
        
        embed = discord.Embed(
            title="User Unbanned",
            description=f"User with ID {user_id} has been unbanned from the server.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason or "No reason provided")
        
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "Unban", discord.Object(id=user_id), interaction.user, reason)
        
    except ValueError:
        await send_error(interaction, "Please provide a valid user ID.")
    except discord.Forbidden:
        await send_error(interaction, "I don't have permission to unban users.")
    except Exception as e:
        await send_error(interaction, f"An error occurred: {str(e)}")

# Kick command
@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.default_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    """Kick a user from the server"""
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await send_error(interaction, "You cannot kick someone with a role higher than or equal to yours.")
        return
    
    try:
        await user.kick(reason=reason)
        
        embed = discord.Embed(
            title="User Kicked",
            description=f"{user.mention} has been kicked from the server.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason or "No reason provided")
        
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "Kick", user, interaction.user, reason)
        
    except discord.Forbidden:
        await send_error(interaction, "I don't have permission to kick that user.")
    except Exception as e:
        await send_error(interaction, f"An error occurred: {str(e)}")

# Timeout command
@bot.tree.command(name="timeout", description="Timeout a user for a specified duration")
@app_commands.default_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str = None):
    """Timeout a user for a specified duration (e.g., 1h, 1d, 7d)"""
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await send_error(interaction, "You cannot timeout someone with a role higher than or equal to yours.")
        return
    
    try:
        # Parse duration
        duration_seconds = 0
        if duration.endswith("s"):
            duration_seconds = int(duration[:-1])
        elif duration.endswith("m"):
            duration_seconds = int(duration[:-1]) * 60
        elif duration.endswith("h"):
            duration_seconds = int(duration[:-1]) * 3600
        elif duration.endswith("d"):
            duration_seconds = int(duration[:-1]) * 86400
        else:
            await send_error(interaction, "Invalid duration format. Use s, m, h, or d (e.g., 30m, 1h, 1d).")
            return
        
        # Max timeout is 28 days
        if duration_seconds > 2419200:
            duration_seconds = 2419200
        
        until = discord.utils.utcnow() + datetime.timedelta(seconds=duration_seconds)
        await user.timeout(until, reason=reason)
        
        embed = discord.Embed(
            title="User Timed Out",
            description=f"{user.mention} has been timed out.",
            color=discord.Color.red()
        )
        embed.add_field(name="Duration", value=duration)
        embed.add_field(name="Reason", value=reason or "No reason provided")
        
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "Timeout", user, interaction.user, reason, duration)
        
    except discord.Forbidden:
        await send_error(interaction, "I don't have permission to timeout that user.")
    except Exception as e:
        await send_error(interaction, f"An error occurred: {str(e)}")

# Clear command
@bot.tree.command(name="clear", description="Clear a specified number of messages")
@app_commands.default_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int, user: discord.Member = None):
    """Clear a specified number of messages, optionally from a specific user"""
    if amount <= 0 or amount > 100:
        await send_error(interaction, "Please provide a number between 1 and 100.")
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        if user:
            def check(message):
                return message.author.id == user.id
            
            deleted = await interaction.channel.purge(limit=amount, check=check)
            
            embed = discord.Embed(
                title="Messages Cleared",
                description=f"Deleted {len(deleted)} messages from {user.mention}.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            await log_action(interaction.guild, "Clear", user, interaction.user, f"Cleared {len(deleted)} messages from {user.name}")
        else:
            deleted = await interaction.channel.purge(limit=amount)
            
            embed = discord.Embed(
                title="Messages Cleared",
                description=f"Deleted {len(deleted)} messages.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            await log_action(interaction.guild, "Clear", interaction.channel, interaction.user, f"Cleared {len(deleted)} messages from {interaction.channel.name}")
            
    except discord.Forbidden:
        await send_error(interaction, "I don't have permission to delete messages.")
    except Exception as e:
        await send_error(interaction, f"An error occurred: {str(e)}")

# Warn command
@bot.tree.command(name="warn", description="Warn a user")
@app_commands.default_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    """Warn a user and apply automatic actions based on warning count"""
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await send_error(interaction, "You cannot warn someone with a role higher than or equal to yours.")
        return
    
    guild_id = str(interaction.guild.id)
    user_id = str(user.id)
    
    # Initialize warnings for this guild if not exists
    if guild_id not in bot.config["warnings"]:
        bot.config["warnings"][guild_id] = {}
    
    # Initialize warnings for this user if not exists
    if user_id not in bot.config["warnings"][guild_id]:
        bot.config["warnings"][guild_id][user_id] = []
    
    # Add warning
    warning = {
        "reason": reason or "No reason provided",
        "moderator": interaction.user.id,
        "timestamp": datetime.datetime.now().isoformat()
    }
    
    bot.config["warnings"][guild_id][user_id].append(warning)
    bot.save_config()
    
    warning_count = len(bot.config["warnings"][guild_id][user_id])
    
    embed = discord.Embed(
        title="User Warned",
        description=f"{user.mention} has been warned.",
        color=discord.Color.red()
    )
    embed.add_field(name="Reason", value=reason or "No reason provided")
    embed.add_field(name="Warning Count", value=str(warning_count))
    
    await interaction.response.send_message(embed=embed)
    await log_action(interaction.guild, "Warning", user, interaction.user, reason)
    
    # Check if automatic action should be taken
    for threshold, action in bot.config["warn_thresholds"].items():
        if warning_count == int(threshold):
            if action == "timeout":
                until = discord.utils.utcnow() + datetime.timedelta(hours=1)
                await user.timeout(until, reason=f"Automatic timeout after {threshold} warnings")
                
                auto_embed = discord.Embed(
                    title="Automatic Action",
                    description=f"{user.mention} has been automatically timed out for 1 hour after receiving {threshold} warnings.",
                    color=discord.Color.red()
                )
                await interaction.channel.send(embed=auto_embed)
                await log_action(interaction.guild, "Auto-Timeout", user, bot.user, f"Automatic timeout after {threshold} warnings", "1 hour")
            
            elif action == "kick":
                await user.kick(reason=f"Automatic kick after {threshold} warnings")
                
                auto_embed = discord.Embed(
                    title="Automatic Action",
                    description=f"{user.mention} has been automatically kicked after receiving {threshold} warnings.",
                    color=discord.Color.red()
                )
                await interaction.channel.send(embed=auto_embed)
                await log_action(interaction.guild, "Auto-Kick", user, bot.user, f"Automatic kick after {threshold} warnings")
            
            elif action == "ban":
                await user.ban(reason=f"Automatic ban after {threshold} warnings")
                
                auto_embed = discord.Embed(
                    title="Automatic Action",
                    description=f"{user.mention} has been automatically banned after receiving {threshold} warnings.",
                    color=discord.Color.red()
                )
                await interaction.channel.send(embed=auto_embed)
                await log_action(interaction.guild, "Auto-Ban", user, bot.user, f"Automatic ban after {threshold} warnings")

# Warnings command
@bot.tree.command(name="warnings", description="View warnings for a user")
@app_commands.default_permissions(moderate_members=True)
async def warnings(interaction: discord.Interaction, user: discord.Member):
    """View warnings for a user"""
    guild_id = str(interaction.guild.id)
    user_id = str(user.id)
    
    if (guild_id not in bot.config["warnings"] or 
        user_id not in bot.config["warnings"][guild_id] or
        not bot.config["warnings"][guild_id][user_id]):
        
        embed = discord.Embed(
            title="No Warnings",
            description=f"{user.mention} has no warnings.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    warnings_list = bot.config["warnings"][guild_id][user_id]
    
    embed = discord.Embed(
        title=f"Warnings for {user.name}",
        description=f"{user.mention} has {len(warnings_list)} warning(s).",
        color=discord.Color.red()
    )
    
    for i, warning in enumerate(warnings_list, 1):
        moderator = interaction.guild.get_member(warning["moderator"])
        moderator_name = moderator.name if moderator else "Unknown Moderator"
        
        timestamp = datetime.datetime.fromisoformat(warning["timestamp"])
        formatted_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {warning['reason']}\n**Moderator:** {moderator_name}\n**Date:** {formatted_time}",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Clear warnings command
@bot.tree.command(name="clearwarnings", description="Clear warnings for a user")
@app_commands.default_permissions(administrator=True)
async def clearwarnings(interaction: discord.Interaction, user: discord.Member):
    """Clear all warnings for a user"""
    guild_id = str(interaction.guild.id)
    user_id = str(user.id)
    
    if (guild_id not in bot.config["warnings"] or 
        user_id not in bot.config["warnings"][guild_id] or
        not bot.config["warnings"][guild_id][user_id]):
        
        embed = discord.Embed(
            title="No Warnings",
            description=f"{user.mention} has no warnings to clear.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    warning_count = len(bot.config["warnings"][guild_id][user_id])
    bot.config["warnings"][guild_id][user_id] = []
    bot.save_config()
    
    embed = discord.Embed(
        title="Warnings Cleared",
        description=f"Cleared {warning_count} warning(s) for {user.mention}.",
        color=discord.Color.red()
    )
    
    await interaction.response.send_message(embed=embed)
    await log_action(interaction.guild, "Clear Warnings", user, interaction.user, f"Cleared {warning_count} warnings")

# Run the bot
if __name__ == "__main__":
    # Replace with your actual token
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        logging.error("No token provided. Set the DISCORD_TOKEN environment variable.")
        exit(1)
    
    bot.run(TOKEN)

console.log("bot is up.")
