import discord
from discord.ext import commands

import youtube_dl

import asyncio
from collections import deque
from dotenv import load_dotenv, find_dotenv
import os


ytdl_format_options = {
  'format': 'bestaudio/best',
  'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
  'restrictfilenames': True,
  'extract_flat': True,
  'nocheckcertificate': True,
  'ignoreerrors': False,
  'logtostderr': False,
  'quiet': True,
  'no_warnings': True,
  'default_search': 'ytsearch',
  'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
  'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
  'options': '-vn -rtbufsize 30M'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.FFmpegPCMAudio):
  def __init__(self, source, *, data, **ffmpeg_options):
    
    super().__init__(source, **ffmpeg_options)

    self.data = data

    self.title = data.get('title')
    self.url = data.get('url')
    self.webpage_url = data.get('webpage_url')

  @classmethod
  async def from_url(cls, url, *, loop=None):
    
    loop = loop or asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))

    filename = data['url']
    return cls(filename, **ffmpeg_options, data=data)


#----------


bot = commands.Bot(command_prefix='!')

@bot.event
async def on_ready():

  print(f'Logged in as {bot.user} (ID: {bot.user.id})')


#----------


# Music player instance.
# A player instance is created for every voice channel the bot is connected to.
class Music_player():
  
  @classmethod
  async def create(cls, channel):

    self = Music_player()

    self.voice_client = await channel.connect()
    self.voice_client.stop()

    self.id = self.voice_client.guild.id
    self.queue = deque()
    self.playing = False
    self.alive = True

    bot.loop.create_task(self.player_loop())
    return self

  async def player_loop(self):

    while(self.alive):
      await asyncio.sleep(0.5)

      if not self.voice_client.is_playing():
        
        if len(self.queue) == 0:
          self.playing = False

        else:
          url = self.queue.popleft()['url']
          self.playing = True
          source = await YTDLSource.from_url(url, loop=bot.loop)
          self.voice_client.play(source, after=lambda e: print(f'Player error: {e}') if e else None) 


  async def add_song(self, search_term):

    data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search_term, download=False))

    # data has entries if link was playlist
    if 'entries' in data:

      for video in data['entries']:
        url = video['url']

        if 'title' in video:
          title = video['title']
        else:
          title = '-'

        self.queue.append({'url': url, 'title': title})
      
      playlist_length = len(data['entries'])
      return (f'Added {playlist_length} songs to queue.')
    
    url = data['url']
    title = data['title']

    if self.playing:
      self.queue.append({'url': url, 'title': title})
      return (f'Added {title} to queue.')

    else:  
      self.queue.append({'url': url, 'title': title})
      return (f'Now playing: {title}')        

  async def leave(self):

    await self.voice_client.disconnect()
    self.alive = False

  def skip(self):

    if self.playing:
      self.voice_client.stop()
      return True
    else:
      return False

  def get_queue(self):
    return list(self.queue)


#----------


# Bot commands class
class Music_commands(commands.Cog, name = 'Music commands'):

  def __init__(self, bot):

    self.bot = bot
    self.players = {}

  
  @commands.Cog.listener()
  async def on_voice_state_update(self, member, before, after):
    """Leaves the voice channel once there are no users connected to it"""

    id = member.guild.id

    if id in self.players:   
      if len(self.players.get(id).voice_client.channel.members) == 1:
        await self.players.pop(id).leave()


  @commands.command(name='hello')
  async def hello(self, ctx):
    """Says hello"""

    await ctx.send('Hello!')

  @commands.command(aliases=['j'])
  async def join(self, ctx):
    """Joins the users voice channel"""
    
    await self._create_player(ctx.author.voice.channel)

  @commands.command(aliases=['l', 'kys'])
  async def leave(self, ctx):
    """Leaves the users voice channel"""

    id = ctx.voice_client.guild.id
    await self.players.pop(id).leave()

  @commands.command(aliases=['stream', 'p'])
  async def play(self, ctx, *, search_term):
    """
    Plays audio from a url or searches and plays audio from Youtube.
    If already playing the command appends stuff to a queue.
    """

    async with ctx.typing():

      id = ctx.voice_client.guild.id
      player = self.players.get(id)
      
      response = await player.add_song(search_term)
      await ctx.send(response)

  @commands.command(aliases=['s'])
  async def skip(self, ctx):
    """Skips the song that is currently playing"""

    id = ctx.voice_client.guild.id

    if self.players.get(id).skip():
      await ctx.send('Skipped.')

    else:
      await ctx.send('Nothing to skip.')
      raise commands.CommandError('Nothing to skip.')

  @commands.command(aliases=['clear', 'c'])
  async def clean(self, ctx):
    """Clears the bots messages from a channel"""

    async for message in ctx.channel.history(limit=200):
      if message.author == self.bot.user:
        await message.delete()
    await ctx.send('Cleared history.', delete_after=2)

  @commands.command(aliases=['q'])
  async def queue(self, ctx):
    """Shows the song queue"""

    id = ctx.voice_client.guild.id
    queue = self.players.get(id).get_queue()
    q_len = len(queue)
    message = 'Songs in queue:\n'

    if q_len == 0:
      await ctx.send('The queue is empty.')
      return

    elif q_len <= 10:
      position = 1
      for item in queue:
        message += str(position) + '. ' + item['title'] + '\n'
        position += 1

    else:
      for i in range(1,11):
        title = queue[i]['title']
        message += str(i) + '. ' + title + '\n'
      message += 'And ' + str(q_len-10) + ' more.'

    await ctx.send(message)


  @play.before_invoke
  async def ensure_author_and_bot_voice_before_play(self, ctx):

    if ctx.author.voice is None:
      await ctx.send('You must be connected to a voice channel to use this command.')
      raise commands.CommandError('Author not connected to a voice channel.')

    if ctx.voice_client is None:
      await self._create_player(ctx.author.voice.channel)
      return

    id = ctx.voice_client.guild.id
    if not self.players.get(id):
      await self._create_player(ctx.author.voice.channel)
      return

  @skip.before_invoke
  @queue.before_invoke
  async def ensure_author_and_bot_voice_before_skip(self, ctx):

    if ctx.author.voice is None:
      await ctx.send('You must be connected to a voice channel to use this command.')
      raise commands.CommandError('Author not connected to a voice channel.')

    if ctx.voice_client is None:
      await ctx.send('You must be connected to a voice channel with the bot to use this command.')
      raise commands.CommandError('Author not connected to a voice channel with the bot.')

    id = ctx.voice_client.guild.id
    if not self.players.get(id) or self.players.get(id).voice_client.channel != ctx.author.voice.channel:
      await ctx.send('You must be connected to a voice channel with the bot to use this command.')
      raise commands.CommandError('Author not connected to a voice channel with the bot.')

  @leave.before_invoke
  async def ensure_bot_connected_to_voice(self, ctx):

    if ctx.voice_client is None:
      await ctx.send('Not connected to a voice channel.')
      raise commands.CommandError('Not connected to a voice channel.')

  @join.before_invoke
  async def ensure_bot_not_connected_to_voice(self, ctx):

    if ctx.voice_client == ctx.author.voice:
      await ctx.send('Already connected.')
      raise commands.CommandError('Already connected to this channel.')

  @play.after_invoke
  @skip.after_invoke
  @leave.after_invoke
  @join.after_invoke
  @clean.after_invoke
  @queue.after_invoke
  async def delete_command_message(self, ctx):
    """Removes the command messages once the commands are completed"""

    await ctx.message.delete()

  async def _create_player(self, channel):
    """Creates a new player instance"""

    player = await Music_player.create(channel)
    id = player.id
    self.players.update({id: player})


bot.add_cog(Music_commands(bot))

load_dotenv(find_dotenv())
bot.run(os.getenv('TOKEN'))