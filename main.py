import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import logging
import datetime
import asyncio

# Configure logging
# This sets up both file and console logging to track bot operations and errors
# File logs persist across restarts while console logs provide real-time monitoring
logging.basicConfig(
    level=logging.INFO,  # Only log messages with severity INFO or higher
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # Structured format for easier parsing
    handlers=[
        logging.FileHandler("discord.log"),  # Persistent logs for post-mortem analysis
        logging.StreamHandler()  # Console output for real-time monitoring
    ]
)

# Bot configuration
class ModBot(commands.Bot):
    """
    Custom Bot class that extends discord.ext.commands.Bot
    
    This class handles:
    1. Configuration management (loading/saving settings to JSON)
    2. Initialization with required permissions (members, message_content)
    3. Command registration and synchronization
    
    The configuration is persisted between restarts and contains:
    - Log channel mappings (per guild)
    - Warning thresholds for automated actions
    - User warning history
    """
    def __init__(self):
        # Set up intents (permissions) the bot needs to function
        # members: Required to access member objects for moderation commands
        # message_content: Required to read message content for moderation
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        
        # Initialize the parent Bot class with our configuration
        super().__init__(command_prefix="!", intents=intents)
        self.config_file = "config.json"
        self.load_config()
        
    def load_config(self):
        """
        Load configuration from JSON file or create default if not exists
        
        The configuration structure is:
        {
            "log_channels": {guild_id: channel_id, ...},
            "warn_thresholds": {"3": "timeout", "5": "kick", "7": "ban"},
            "warnings": {guild_id: {user_id: [warning_objects], ...}, ...}
        }
        """
        if os.path.exists(self.config_file):
            with open(self.config_file, "r") as f:
                self.config = json.load(f)
        else:
            # Default configuration with escalating consequences for warnings
            self.config = {
                "log_channels": {},  # Maps guild IDs to log channel IDs
                "warn_thresholds": {  # Defines automated actions at warning thresholds
                    "3": "timeout",   # 3 warnings = timeout
                    "5": "kick",      # 5 warnings = kick
                    "7": "ban"        # 7 warnings = ban
                },
                "warnings": {}        # Stores warning history per guild and user
            }
            self.save_config()
    
    def save_config(self):
        """
        Save current configuration to JSON file
        Uses indentation for human readability of the config file
        """
        with open(self.config_file, "w") as f:
            json.dump(self.config, f, indent=4)
    
    async def on_ready(self):
        """
        Event handler triggered when bot successfully connects to Discord
        
        This method:
        1. Logs the successful connection
        2. Synchronizes slash commands with Discord's API
        3. Handles any errors during command synchronization
        """
        logging.info(f"Bot is ready! Logged in as {self.user}")
        try:
            # Sync slash commands with Discord - required for app_commands to work
            synced = await self.tree.sync()
            logging.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logging.error(f"Failed to sync commands: {e}")

# Initialize the bot instance
bot = ModBot()

# Moderation functionality
async def log_action(guild, action, target, moderator, reason, duration=None):
    """
    Log a moderation action to the configured log channel
    
    This function creates a standardized embed for all moderation actions
    with consistent formatting and information, ensuring proper audit trails.
    
    Parameters:
        guild: The Discord guild where the action occurred
        action: Type of moderation action (e.g., "Ban", "Kick")
        target: User or object that was moderated
        moderator: Staff member who performed the action
        reason: Justification for the action
        duration: Optional time period for temporary actions (timeout)
    
    Returns:
        None - but sends a message to the configured log channel if available
    """
    guild_id = str(guild.id)
    # Skip logging if no log channel is configured for this guild
    if guild_id not in bot.config["log_channels"]:
        return
    
    log_channel_id = bot.config["log_channels"][guild_id]
    log_channel = guild.get_channel(log_channel_id)
    
    # Skip if channel no longer exists or bot can't access it
    if not log_channel:
        return
    
    # Create a standardized embed for the log entry
    embed = discord.Embed(
        title=f"Moderation Action: {action}",
        color=discord.Color.red(),  # Red color consistently used for moderation actions
        timestamp=datetime.datetime.now()  # Timestamp for accurate audit logs
    )
    
    # Handle both user objects and other targets (like channels)
    if hasattr(target, 'mention'):
        # Handle Discord's new username system (no discriminators)
        user_display = f"{target.mention} ({target.name})"
        # Add discriminator if available (for older Discord versions)
        if hasattr(target, 'discriminator') and target.discriminator != '0':
            user_display = f"{target.mention} ({target.name}#{target.discriminator})"
        embed.add_field(name="User", value=user_display, inline=False)
    else:
        embed.add_field(name="Target", value=f"{target}", inline=False)
    
    # Handle moderator display name with compatibility for new username system
    mod_display = f"{moderator.mention} ({moderator.name})"
    if hasattr(moderator, 'discriminator') and moderator.discriminator != '0':
        mod_display = f"{moderator.mention} ({moderator.name}#{moderator.discriminator})"
    embed.add_field(name="Moderator", value=mod_display, inline=False)
    
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    
    # Only include duration field for temporary actions
    if duration:
        embed.add_field(name="Duration", value=duration, inline=False)
    
    # Include user ID in footer for reference and audit purposes
    if hasattr(target, 'id'):
        embed.set_footer(text=f"User ID: {target.id}")
    
    await log_channel.send(embed=embed)

# Helper function to create error embeds
async def send_error(interaction, message):
    """
    Send a standardized error message to the user
    
    This centralizes error handling to ensure consistent user experience
    and handles both responded and unresponded interactions correctly.
    
    Parameters:
        interaction: The Discord interaction object
        message: The error message to display
    """
    embed = discord.Embed(
        title="Error",
        description=message,
        color=discord.Color.red()  # Red consistently indicates errors
    )
    
    # Handle the case where we've already sent an initial response
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)

# Setup command
@bot.tree.command(name="setup", description="Set up the moderation bot for this server")
@app_commands.default_permissions(administrator=True)  # Restrict to administrators
async def setup(interaction: discord.Interaction, log_channel: discord.TextChannel):
    """
    Initial setup command to configure the moderation bot for a server
    
    This command:
    1. Registers the log channel where moderation actions will be recorded
    2. Saves the configuration to persist across bot restarts
    3. Confirms setup to the user
    
    Required permissions: Administrator
    """
    guild_id = str(interaction.guild_id)
    # Store the log channel ID in the configuration
    bot.config["log_channels"][guild_id] = log_channel.id
    bot.save_config()
    
    # Confirm setup to the user
    embed = discord.Embed(
        title="Setup Complete",
        description=f"Moderation logs will be sent to {log_channel.mention}",
        color=discord.Color.red()  # Consistent color scheme with other mod actions
    )
    await interaction.response.send_message(embed=embed)

# Ban command
@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.default_permissions(ban_members=True)  # Restrict to users with ban permission
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = None, delete_days: int = 0):
    """
    Ban a user from the server
    
    This command:
    1. Checks permission hierarchy to prevent moderation abuse
    2. Executes the ban with optional message deletion
    3. Notifies the channel of the action
    4. Logs the action to the designated log channel
    
    Parameters:
        user: The member to ban
        reason: Optional justification for the ban
        delete_days: Number of days of messages to delete (0-7)
    
    Required permissions: Ban Members
    """
    # Permission hierarchy check - prevents moderators from banning higher-ranked users
    # Exception made for server owner who can ban anyone
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await send_error(interaction, "You cannot ban someone with a role higher than or equal to yours.")
        return
    
    try:
        # Validate delete_days is within Discord's allowed range (0-7)
        delete_days = max(0, min(7, delete_days))
        
        # Execute the ban with the specified message deletion period
        await user.ban(reason=reason, delete_message_days=delete_days)
        
        # Notify the channel of successful action
        embed = discord.Embed(
            title="User Banned",
            description=f"{user.mention} has been banned from the server.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason or "No reason provided")
        
        await interaction.response.send_message(embed=embed)
        # Log the action for audit purposes
        await log_action(interaction.guild, "Ban", user, interaction.user, reason)
        
    except discord.Forbidden:
        # Bot lacks permission to perform the action
        await send_error(interaction, "I don't have permission to ban that user.")
    except Exception as e:
        # Catch-all for unexpected errors
        await send_error(interaction, f"An error occurred: {str(e)}")

# Unban command
@bot.tree.command(name="unban", description="Unban a user from the server")
@app_commands.default_permissions(ban_members=True)  # Restrict to users with ban permission
async def unban(interaction: discord.Interaction, user_id: str, reason: str = None):
    """
    Unban a user from the server using their ID
    
    This command:
    1. Validates the user ID format
    2. Checks if the user is actually banned
    3. Executes the unban
    4. Notifies the channel of the action
    5. Logs the action to the designated log channel
    
    Parameters:
        user_id: The ID of the banned user (string to handle copy-paste)
        reason: Optional justification for the unban
    
    Required permissions: Ban Members
    """
    try:
        # Convert string ID to integer
        user_id = int(user_id)
        
        # Check if the user is actually banned
        # This approach is more efficient than fetching all bans first
        try:
            # Try to fetch the ban entry directly
            ban_entry = await interaction.guild.fetch_ban(discord.Object(id=user_id))
            # If we get here, the user is banned
        except discord.NotFound:
            # User is not banned
            await send_error(interaction, "This user is not banned.")
            return
        
        # Execute the unban
        await interaction.guild.unban(discord.Object(id=user_id), reason=reason)
        
        # Notify the channel of successful action
        embed = discord.Embed(
            title="User Unbanned",
            description=f"User with ID {user_id} has been unbanned from the server.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason or "No reason provided")
        
        await interaction.response.send_message(embed=embed)
        # Log the action for audit purposes
        await log_action(interaction.guild, "Unban", f"User ID: {user_id}", interaction.user, reason)
        
    except ValueError:
        # Handle non-numeric user IDs
        await send_error(interaction, "Please provide a valid user ID.")
    except discord.Forbidden:
        # Bot lacks permission to perform the action
        await send_error(interaction, "I don't have permission to unban users.")
    except Exception as e:
        # Catch-all for unexpected errors
        await send_error(interaction, f"An error occurred: {str(e)}")

# Kick command
@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.default_permissions(kick_members=True)  # Restrict to users with kick permission
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    """
    Kick a user from the server
    
    This command:
    1. Checks permission hierarchy to prevent moderation abuse
    2. Executes the kick
    3. Notifies the channel of the action
    4. Logs the action to the designated log channel
    
    Parameters:
        user: The member to kick
        reason: Optional justification for the kick
    
    Required permissions: Kick Members
    """
    # Permission hierarchy check - prevents moderators from kicking higher-ranked users
    # Exception made for server owner who can kick anyone
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await send_error(interaction, "You cannot kick someone with a role higher than or equal to yours.")
        return
    
    try:
        # Execute the kick
        await user.kick(reason=reason)
        
        # Notify the channel of successful action
        embed = discord.Embed(
            title="User Kicked",
            description=f"{user.mention} has been kicked from the server.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason or "No reason provided")
        
        await interaction.response.send_message(embed=embed)
        # Log the action for audit purposes
        await log_action(interaction.guild, "Kick", user, interaction.user, reason)
        
    except discord.Forbidden:
        # Bot lacks permission to perform the action
        await send_error(interaction, "I don't have permission to kick that user.")
    except Exception as e:
        # Catch-all for unexpected errors
        await send_error(interaction, f"An error occurred: {str(e)}")

# Timeout command
@bot.tree.command(name="timeout", description="Timeout a user for a specified duration")
@app_commands.default_permissions(moderate_members=True)  # Restrict to users with timeout permission
async def timeout(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str = None):
    """
    Timeout a user for a specified duration
    
    This command:
    1. Checks permission hierarchy to prevent moderation abuse
    2. Parses the duration string into seconds
    3. Applies the timeout with the specified duration
    4. Notifies the channel of the action
    5. Logs the action to the designated log channel
    
    Parameters:
        user: The member to timeout
        duration: Time period in format like "30s", "5m", "2h", "1d"
        reason: Optional justification for the timeout
    
    Required permissions: Moderate Members
    """
    # Permission hierarchy check - prevents moderators from timing out higher-ranked users
    # Exception made for server owner who can timeout anyone
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await send_error(interaction, "You cannot timeout someone with a role higher than or equal to yours.")
        return
    
    try:
        # Parse duration string into seconds
        # This flexible parsing allows moderators to use natural time formats
        duration_seconds = 0
        
        # Improved duration parsing with better error handling
        if duration.endswith("s"):
            try:
                duration_seconds = int(duration[:-1])
            except ValueError:
                await send_error(interaction, f"Invalid duration format: '{duration}'. Use numbers followed by s, m, h, or d (e.g., 30m, 1h, 1d).")
                return
        elif duration.endswith("m"):
            try:
                duration_seconds = int(duration[:-1]) * 60
            except ValueError:
                await send_error(interaction, f"Invalid duration format: '{duration}'. Use numbers followed by s, m, h, or d (e.g., 30m, 1h, 1d).")
                return
        elif duration.endswith("h"):
            try:
                duration_seconds = int(duration[:-1]) * 3600
            except ValueError:
                await send_error(interaction, f"Invalid duration format: '{duration}'. Use numbers followed by s, m, h, or d (e.g., 30m, 1h, 1d).")
                return
        elif duration.endswith("d"):
            try:
                duration_seconds = int(duration[:-1]) * 86400
            except ValueError:
                await send_error(interaction, f"Invalid duration format: '{duration}'. Use numbers followed by s, m, h, or d (e.g., 30m, 1h, 1d).")
                return
        else:
            await send_error(interaction, "Invalid duration format. Use s, m, h, or d (e.g., 30m, 1h, 1d).")
            return
        
        # Ensure duration is positive
        if duration_seconds <= 0:
            await send_error(interaction, "Duration must be positive.")
            return
        
        # Discord has a maximum timeout duration of 28 days
        # This ensures we don't exceed that limit
        if duration_seconds > 2419200:  # 28 days in seconds
            duration_seconds = 2419200
            await interaction.followup.send("Duration exceeded maximum of 28 days. Setting timeout to 28 days.", ephemeral=True)
        
        # Calculate the end time and apply the timeout
        until = discord.utils.utcnow() + datetime.timedelta(seconds=duration_seconds)
        await user.timeout(until, reason=reason)
        
        # Notify the channel of successful action
        embed = discord.Embed(
            title="User Timed Out",
            description=f"{user.mention} has been timed out.",
            color=discord.Color.red()
        )
        embed.add_field(name="Duration", value=duration)
        embed.add_field(name="Reason", value=reason or "No reason provided")
        
        await interaction.response.send_message(embed=embed)
        # Log the action for audit purposes
        await log_action(interaction.guild, "Timeout", user, interaction.user, reason, duration)
        
    except discord.Forbidden:
        # Bot lacks permission to perform the action
        await send_error(interaction, "I don't have permission to timeout that user.")
    except Exception as e:
        # Catch-all for unexpected errors
        await send_error(interaction, f"An error occurred: {str(e)}")

# Clear command
@bot.tree.command(name="clear", description="Clear a specified number of messages")
@app_commands.default_permissions(manage_messages=True)  # Restrict to users with message management permission
async def clear(interaction: discord.Interaction, amount: int, user: discord.Member = None):
    """
    Clear a specified number of messages, optionally from a specific user
    
    This command:
    1. Validates the requested amount is within limits
    2. Defers the response to allow time for message deletion
    3. Deletes messages (either all or from a specific user)
    4. Reports the number of messages deleted
    5. Logs the action to the designated log channel
    
    Parameters:
        amount: Number of messages to check for deletion (1-100)
        user: Optional user to filter messages by
    
    Required permissions: Manage Messages
    """
    # Validate the requested amount is within Discord's limits
    if amount <= 0 or amount > 100:
        await send_error(interaction, "Please provide a number between 1 and 100.")
        return
    
    # Defer the response since message deletion might take time
    # This prevents the interaction from timing out
    await interaction.response.defer(ephemeral=True)
    
    try:
        if user:
            # Filter messages by the specified user
            def check(message):
                return message.author.id == user.id
            
            # Delete messages that pass the filter
            # Note: Discord only allows bulk deletion of messages less than 14 days old
            deleted = await interaction.channel.purge(limit=amount, check=check, before=datetime.datetime.now())
            
            # Report the number of messages deleted
            embed = discord.Embed(
                title="Messages Cleared",
                description=f"Deleted {len(deleted)} messages from {user.mention}.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            # Log the action for audit purposes
            await log_action(interaction.guild, "Clear", user, interaction.user, f"Cleared {len(deleted)} messages from {user.name}")
        else:
            # Delete messages without filtering
            # Note: Discord only allows bulk deletion of messages less than 14 days old
            deleted = await interaction.channel.purge(limit=amount, before=datetime.datetime.now())
            
            # Report the number of messages deleted
            embed = discord.Embed(
                title="Messages Cleared",
                description=f"Deleted {len(deleted)} messages.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            # Log the action for audit purposes
            await log_action(interaction.guild, "Clear", interaction.channel, interaction.user, f"Cleared {len(deleted)} messages from {interaction.channel.name}")
            
    except discord.Forbidden:
        # Bot lacks permission to perform the action
        await send_error(interaction, "I don't have permission to delete messages.")
    except discord.HTTPException as e:
        # Handle specific HTTP exceptions
        if e.code == 50034:
            await send_error(interaction, "Cannot delete messages older than 14 days.")
        else:
            await send_error(interaction, f"An error occurred: {str(e)}")
    except Exception as e:
        # Catch-all for unexpected errors
        await send_error(interaction, f"An error occurred: {str(e)}")

# Warn command
@bot.tree.command(name="warn", description="Warn a user")
@app_commands.default_permissions(moderate_members=True)  # Restrict to users with moderation permission
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    """
    Warn a user and apply automatic actions based on warning count
    
    This command implements a progressive discipline system:
    1. Checks permission hierarchy to prevent moderation abuse
    2. Records the warning in the persistent configuration
    3. Notifies the channel of the warning
    4. Checks if the warning count has reached a threshold for automatic action
    5. Applies automatic actions (timeout, kick, ban) based on configured thresholds
    
    Parameters:
        user: The member to warn
        reason: Optional justification for the warning
    
    Required permissions: Moderate Members
    """
    # Permission hierarchy check - prevents moderators from warning higher-ranked users
    # Exception made for server owner who can warn anyone
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await send_error(interaction, "You cannot warn someone with a role higher than or equal to yours.")
        return
    
    guild_id = str(interaction.guild.id)
    user_id = str(user.id)
    
    # Initialize warnings structure if this is the first warning in this guild
    if guild_id not in bot.config["warnings"]:
        bot.config["warnings"][guild_id] = {}
    
    # Initialize warnings array if this is the first warning for this user
    if user_id not in bot.config["warnings"][guild_id]:
        bot.config["warnings"][guild_id][user_id] = []
    
    # Create and store the warning object
    warning = {
        "reason": reason or "No reason provided",
        "moderator": interaction.user.id,
        "timestamp": datetime.datetime.now().isoformat()  # Store as ISO format for serialization
    }
    
    bot.config["warnings"][guild_id][user_id].append(warning)
    bot.save_config()
    
    warning_count = len(bot.config["warnings"][guild_id][user_id])
    
    # Notify the channel of the warning
    embed = discord.Embed(
        title="User Warned",
        description=f"{user.mention} has been warned.",
        color=discord.Color.red()
    )
    embed.add_field(name="Reason", value=reason or "No reason provided")
    embed.add_field(name="Warning Count", value=str(warning_count))
    
    await interaction.response.send_message(embed=embed)
    # Log the action for audit purposes
    await log_action(interaction.guild, "Warning", user, interaction.user, reason)
    
    # Check if automatic action should be taken based on warning count
    # This implements the progressive discipline system
    for threshold, action in bot.config["warn_thresholds"].items():
        if warning_count == int(threshold):
            if action == "timeout":
                # Apply 1-hour timeout
                until = discord.utils.utcnow() + datetime.timedelta(hours=1)
                try:
                    await user.timeout(until, reason=f"Automatic timeout after {threshold} warnings")
                    
                    # Notify the channel of the automatic action
                    auto_embed = discord.Embed(
                        title="Automatic Action",
                        description=f"{user.mention} has been automatically timed out for 1 hour after receiving {threshold} warnings.",
                        color=discord.Color.red()
                    )
                    await interaction.channel.send(embed=auto_embed)
                    # Log the automatic action
                    await log_action(interaction.guild, "Auto-Timeout", user, bot.user, f"Automatic timeout after {threshold} warnings", "1 hour")
                except discord.Forbidden:
                    await interaction.channel.send(f"Failed to timeout {user.mention}: Missing permissions.")
                except Exception as e:
                    await interaction.channel.send(f"Failed to timeout {user.mention}: {str(e)}")
            
            elif action == "kick":
                # Apply automatic kick
                try:
                    await user.kick(reason=f"Automatic kick after {threshold} warnings")
                    
                    # Notify the channel of the automatic action
                    auto_embed = discord.Embed(
                        title="Automatic Action",
                        description=f"{user.mention} has been automatically kicked after receiving {threshold} warnings.",
                        color=discord.Color.red()
                    )
                    await interaction.channel.send(embed=auto_embed)
                    # Log the automatic action
                    await log_action(interaction.guild, "Auto-Kick", user, bot.user, f"Automatic kick after {threshold} warnings")
                except discord.Forbidden:
                    await interaction.channel.send(f"Failed to kick {user.mention}: Missing permissions.")
                except Exception as e:
                    await interaction.channel.send(f"Failed to kick {user.mention}: {str(e)}")
            
            elif action == "ban":
                # Apply automatic ban
                try:
                    await user.ban(reason=f"Automatic ban after {threshold} warnings")
                    
                    # Notify the channel of the automatic action
                    auto_embed = discord.Embed(
                        title="Automatic Action",
                        description=f"{user.mention} has been automatically banned after receiving {threshold} warnings.",
                        color=discord.Color.red()
                    )
                    await interaction.channel.send(embed=auto_embed)
                    # Log the automatic action
                    await log_action(interaction.guild, "Auto-Ban", user, bot.user, f"Automatic ban after {threshold} warnings")
                except discord.Forbidden:
                    await interaction.channel.send(f"Failed to ban {user.mention}: Missing permissions.")
                except Exception as e:
                    await interaction.channel.send(f"Failed to ban {user.mention}: {str(e)}")

# Warnings command
@bot.tree.command(name="warnings", description="View warnings for a user")
@app_commands.default_permissions(moderate_members=True)  # Restrict to users with moderation permission
async def warnings(interaction: discord.Interaction, user: discord.Member):
    """
    View warnings for a user
    
    This command:
    1. Checks if the user has any warnings
    2. If no warnings, reports that to the moderator
    3. If warnings exist, displays them in a formatted embed
    4. Shows each warning with reason, moderator, and timestamp
    
    Parameters:
        user: The member whose warnings to view
    
    Required permissions: Moderate Members
    """
    guild_id = str(interaction.guild.id)
    user_id = str(user.id)
    
    # Check if the user has any warnings
    if (guild_id not in bot.config["warnings"] or 
        user_id not in bot.config["warnings"][guild_id] or
        not bot.config["warnings"][guild_id][user_id]):
        
        # Report no warnings
        embed = discord.Embed(
            title="No Warnings",
            description=f"{user.mention} has no warnings.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    warnings_list = bot.config["warnings"][guild_id][user_id]
    
    # Create an embed to display the warnings
    embed = discord.Embed(
        title=f"Warnings for {user.name}",
        description=f"{user.mention} has {len(warnings_list)} warning(s).",
        color=discord.Color.red()
    )
    
    # Add each warning as a field in the embed
    for i, warning in enumerate(warnings_list, 1):
        # Get the moderator who issued the warning
        moderator = interaction.guild.get_member(warning["moderator"])
        moderator_name = "Unknown Moderator"
        
        # Handle case where moderator is no longer in the server
        if moderator:
            moderator_name = moderator.name
        
        # Parse and format the timestamp
        timestamp = datetime.datetime.fromisoformat(warning["timestamp"])
        formatted_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        
        # Add the warning details
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {warning['reason']}\n**Moderator:** {moderator_name}\n**Date:** {formatted_time}",
            inline=False
        )
    
    # Send the warnings list (only visible to the command user)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Clear warnings command
@bot.tree.command(name="clearwarnings", description="Clear warnings for a user")
@app_commands.default_permissions(administrator=True)  # Restrict to administrators only
async def clearwarnings(interaction: discord.Interaction, user: discord.Member):
    """
    Clear all warnings for a user
    
    This command:
    1. Checks if the user has any warnings
    2. If no warnings, reports that to the administrator
    3. If warnings exist, removes them all from the configuration
    4. Reports the number of warnings cleared
    5. Logs the action to the designated log channel
    
    Parameters:
        user: The member whose warnings to clear
    
    Required permissions: Administrator
    """
    guild_id = str(interaction.guild.id)
    user_id = str(user.id)
    
    # Check if the user has any warnings
    if (guild_id not in bot.config["warnings"] or 
        user_id not in bot.config["warnings"][guild_id] or
        not bot.config["warnings"][guild_id][user_id]):
        
        # Report no warnings to clear
        embed = discord.Embed(
            title="No Warnings",
            description=f"{user.mention} has no warnings to clear.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Count warnings before clearing them
    warning_count = len(bot.config["warnings"][guild_id][user_id])
    # Clear all warnings for the user
    bot.config["warnings"][guild_id][user_id] = []
    bot.save_config()
    
    # Report successful clearing of warnings
    embed = discord.Embed(
        title="Warnings Cleared",
        description=f"Cleared {warning_count} warning(s) for {user.mention}.",
        color=discord.Color.red()
    )
    
    await interaction.response.send_message(embed=embed)
    # Log the action for audit purposes
    await log_action(interaction.guild, "Clear Warnings", user, interaction.user, f"Cleared {warning_count} warnings")

# Run the bot
if __name__ == "__main__":
    # Get the bot token from environment variables
    # This is a security best practice to avoid hardcoding tokens
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        logging.error("No token provided. Set the DISCORD_TOKEN environment variable.")
        exit(1)
    
    # Start the bot with the provided token
    bot.run(TOKEN)
