import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import os
import asyncio
import nest_asyncio
from webserver import keep_alive  # Import the keep_alive function

nest_asyncio.apply()

intents = discord.Intents.default()
intents.message_content = True
intents.presences = True  # Enable the Presence Intent
intents.members = True    # Enable the Members Intent

bot = commands.Bot(command_prefix="!", intents=intents)

# Set up yt_dlp options
youtube_dl.utils.bug_reports_message = lambda: ''
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}
ffmpeg_options = {
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.thumbnail = data.get('thumbnail')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

queue = []

class PlayerControls(discord.ui.View):
    def __init__(self, vc):
        super().__init__(timeout=None)
        self.vc = vc

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.primary)
    async def pause(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.vc and self.vc.is_playing():
            self.vc.pause()
            await interaction.response.send_message("Paused", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing", ephemeral=True)

    @discord.ui.button(label="Play", style=discord.ButtonStyle.success)
    async def play(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.vc and self.vc.is_paused():
            self.vc.resume()
            await interaction.response.send_message("Resumed", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is paused", ephemeral=True)

    @discord.ui.button(label="Replay", style=discord.ButtonStyle.secondary)
    async def replay(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.vc and queue:
            self.vc.stop()
            queue.insert(0, queue.pop(-1))  # Move the current song to the front of the queue
            await play_next(interaction, self.vc)
            await interaction.response.send_message("Replaying", ephemeral=True)
        else:
            await interaction.response.send_message("Queue is empty", ephemeral=True)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.vc and queue:
            self.vc.stop()
            await interaction.response.send_message("Skipping to next song", ephemeral=True)
        else:
            await interaction.response.send_message("Queue is empty", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.vc:
            queue.clear()
            self.vc.stop()
            await self.vc.disconnect()
            await interaction.response.send_message("Stopped and disconnected", ephemeral=True)
        else:
            await interaction.response.send_message("No active voice client to stop", ephemeral=True)

# Bot Events
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    await bot.tree.sync()

@bot.event
async def on_voice_state_update(member, before, after):
    if not member.bot and after.channel is None and len(member.guild.voice_client.channel.members) == 1:
        await member.guild.voice_client.disconnect()

# Bot Commands
@bot.tree.command(name='play', description='Play a song from a URL')
async def play(interaction: discord.Interaction, url: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        await interaction.followup.send("You're not connected to a voice channel.")
        return

    channel = interaction.user.voice.channel
    if not interaction.guild.voice_client:
        vc = await channel.connect()
    else:
        vc = interaction.guild.voice_client

    async with interaction.channel.typing():
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        queue.append(player)
        embed = discord.Embed(title="Added to queue", description=player.title, color=discord.Color.blue())
        embed.set_thumbnail(url=player.thumbnail)
        await interaction.followup.send(embed=embed, view=PlayerControls(vc))

    if not vc.is_playing():
        await play_next(interaction, vc)

async def play_next(interaction: discord.Interaction, vc):
    if vc and queue:
        player = queue.pop(0)
        vc.play(player, after=lambda e: bot.loop.create_task(play_next(interaction, vc)))
        embed = discord.Embed(title="Now Playing", description=player.title, color=discord.Color.green())
        embed.set_thumbnail(url=player.thumbnail)
        await interaction.followup.send(embed=embed)
    elif vc:
        await vc.disconnect()

@bot.tree.command(name='stop', description='Stop the bot and clear the queue')
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        queue.clear()
        await vc.disconnect()
        await interaction.response.send_message('Stopped and disconnected.')
    else:
        await interaction.response.send_message('No active voice client to stop.')

@bot.tree.command(name='queue', description='Show the current queue')
async def show_queue(interaction: discord.Interaction):
    if queue:
        queue_list = '\n'.join([player.title for player in queue])
        await interaction.response.send_message(f'Current queue:\n{queue_list}')
    else:
        await interaction.response.send_message('The queue is empty.')

# Run the bot with the token
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

keep_alive()  # Start the web server to keep the bot alive

async def main():
    async with bot:
        await bot.start(TOKEN)

# Apply nest_asyncio to allow running the event loop within the notebook
nest_asyncio.apply()

# Run the bot
if __name__ == "__main__":
    asyncio.run(main())
