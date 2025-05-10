import subprocess
import os
import json
from highrise import BaseBot
from highrise.models import SessionMetadata, User, Position
from highrise.__main__ import BotDefinition, main
import asyncio
import logging
import yt_dlp
from googleapiclient.discovery import build
import threading
import random
import glob
import time  # Added for sleep intervals
# Configure logging with more detail
logging.basicConfig(                    
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_ffmpeg_installed():
    """Check if ffmpeg is installed and available in PATH"""
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.error("FFmpeg is not installed or not in PATH. Please install FFmpeg.")
        return False

class MusicPlayer:
    def __init__(self):
        self.queue = []  # List of (url, title) tuples
        self.history = []  # List of played song titles
        self.max_history = 10  # Maximum number of songs to keep in history
        self.current_song = None
        self.current_url = None
        self.api_key = 'AIzaSyCuQwsagMLGToKlbNMEf7plFEkKhbr-DWs'
        self.volume = 100  # Default volume (percentage)
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
            'cookiefile': 'cookies.txt'
        }
        self.playback_thread = None
        self.is_playing = False
        self.stats = self.load_stats() # Load stats on initialization
        self.load_queue()  # Load queue from file on initialization
        self.ffmpeg_process = None  # Add FFmpeg process reference
        self.playlist_process = None  # Add playlist process reference
        logger.info("MusicPlayer initialized")

    def load_stats(self):
        """Load statistics from JSON file"""
        try:
            if os.path.exists('stats.json'):
                with open('stats.json', 'r') as f:
                    stats = json.load(f)
                # Repair if users is a list
                if isinstance(stats.get("users"), list):
                    stats["users"] = {}
                    with open('stats.json', 'w') as fw:
                        json.dump(stats, fw, indent=2)
                return stats
            else:
                # Create default stats file if it doesn't exist
                default_stats = {"users": {}, "songs": {}}
                with open('stats.json', 'w') as f:
                    json.dump(default_stats, f, indent=2)
                return default_stats
        except Exception as e:
            logger.error(f"Error loading stats: {e}")
            return {"users": {}, "songs": {}}

    def save_stats(self):
        """Save statistics to JSON file"""
        try:
            with open('stats.json', 'w') as f:
                json.dump(self.stats, f, indent=2)
            logger.info("Stats saved successfully")
        except Exception as e:
            logger.error(f"Error saving stats: {e}")

    def search_song(self, query):
        try:
            logger.info(f"Searching for song: {query}")
            youtube = build('youtube', 'v3', developerKey=self.api_key)
            request = youtube.search().list(
                q=query,
                part='id',
                type='video',
                maxResults=1
            )
            response = request.execute()
            logger.info(f"YouTube API response: {response}")

            if not response['items']:
                logger.warning("No search results found")
                return None, None, None

            video_id = response['items'][0]['id']['videoId']
            video_url = f'https://www.youtube.com/watch?v={video_id}'
            logger.info(f"Found video URL: {video_url}")

            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                title = info['title']
                duration = info.get('duration', 0)  # Get duration in seconds
                duration_minutes = duration / 60
                logger.info(f"Retrieved video title: {title}, duration: {duration_minutes:.2f} minutes")

                # Check if song is too long (over 10 minutes)
                if duration_minutes > 10:
                    logger.warning(f"Song too long: {duration_minutes:.2f} minutes")
                    return None, None, "too_long"

            return video_url, title, duration_minutes, None
        except Exception as e:
            logger.error(f"Error searching song: {str(e)}")
            return None, None, None

    def get_user_song_count(self, username):
        """Count the number of songs requested by the user in queue and currently playing"""
        count = 0
        # Count songs in queue
        for _, _, _, requested_by in self.queue:
            if requested_by == username:
                count += 1
        # Count currently playing song
        if self.is_playing and self.current_requested_by == username:
            count += 1
        return count

    
    def save_queue(self):
        """Save the current queue to queue.json"""
        try:
            with open('queue.json', 'w') as f:
                json.dump(self.queue, f, indent=2)
            logger.info("Queue saved to queue.json")
        except Exception as e:
            logger.error(f"Error saving queue: {e}")

    def load_queue(self):
        """Load the queue from queue.json if it exists"""
        try:
            if os.path.exists('queue.json'):
                with open('queue.json', 'r') as f:
                    self.queue = json.load(f)
                logger.info("Queue loaded from queue.json")
            else:
                self.queue = []
        except Exception as e:
            logger.error(f"Error loading queue: {e}")
            self.queue = []

    def add_to_queue(self, url, title, duration, requested_by):
  
        global music_queue
        # Check if user has reached the 3-song limit
        if self.get_user_song_count(requested_by) >= 3:
            logger.info(f"User {requested_by} has reached the 3-song limit")
            return 0, f"❌ @{requested_by}, you can only request up to 3 songs at a time. Wait until one of your songs finishes playing."
        
        self.queue.append((url, title, duration, requested_by))
        self.save_queue()  # Save queue after adding
        queue_length = len(self.queue)
        logger.info(f"Added song to queue: {title}, Queue length: {queue_length}")
        message = (
    f"\n🎧 Track successfully added!\n\n"
    f"🎼 Title: {title}\n"
    f"⏱️ Duration: {duration:.2f} min\n"
    f"🔖 Queue Position: #{queue_length}\n"
    f"👤 Requested by: {requested_by}"
        )
        return queue_length, message

    def delete_user_song(self, username):
        """Delete the most recent song requested by the user from the queue"""
        if not self.queue:
            logger.info(f"No songs in queue to delete for user: {username}")
            return False, "📋 Queue is empty"

        # Find the most recent song by this user (last occurrence in the queue)
        for i in range(len(self.queue) - 1, -1, -1):  # Iterate backwards
            url, title, duration, requested_by = self.queue[i]
            if requested_by == username:
            # Remove the song
                self.queue.pop(i)
                self.save_queue()  # Save updated queue
                logger.info(f"Deleted song '{title}' requested by {username} from queue")
                return True, f"🗑️ Removed from queue:\nTitle: {title}\nDuration: {duration:.2f} minutes\nRequested by: {username}"
    
        logger.info(f"No songs found in queue requested by {username}")
        return False, f"❌ No songs found in queue requested by {username}"

    def get_current_song(self):
        """Get information about the currently playing song"""
        if self.current_song and self.is_playing and self.current_duration is not None and self.current_requested_by is not None:
            return (
    f"\n🔊 Currently Streaming\n\n"
    f"🎵 Title: {self.current_song}\n"
    f"⏱️ Duration: {self.current_duration:.2f} min\n"
    f"👤 Requested by: {self.current_requested_by}"
            )
        return "🔇 No song currently playing"

    
    def get_queue(self):
        """Get the queue status in a professional format"""
        if not self.queue:
            return "📋 Queue is empty"

        total_tracks = len(self.queue)
        track_word = "Track" if total_tracks == 1 else "Tracks"
        queue_list = f"📋 Total {total_tracks} {track_word} in queue:\n"

        for i, (_, title, duration, requested_by) in enumerate(self.queue, 1):
            queue_list += (
    f"{i}. 𝗧𝗶𝘁𝗹𝗲: {title}\n"
    f"   🔹𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻: {duration:.2f} minutes\n"
    f"   🔹𝗥𝗲𝗾𝘂𝗲𝘀𝘁𝗲𝗱 𝗯𝘆: {requested_by}\n"
            )

        return queue_list

    def get_queue_status(self):
        """Get a formatted display of all music parts"""
        parts = []

    # Part 1: Currently Playing
        current = self.get_current_song()
        parts.append(
    f"🎧 𝗖𝗨𝗥𝗥𝗘𝗡𝗧 𝗧𝗥𝗔𝗖𝗞 🎧\n"
    f"{current}"
        )
    # Part 2: Queue
        queue = self.get_queue()
        parts.append(
    f"\n📂 𝗤𝗨𝗘𝗨𝗘 📂\n"
    f"{queue}"
        )
        return "\n".join(parts)

    def skip_song(self):
        global music_queue
        logger.info("skip_song called")
        if not self.is_playing and not self.queue:
            logger.info("No songs playing or in queue during skip")
            return False, "No songs playing or in queue"

        # Terminate FFmpeg process if running
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.ffmpeg_process.terminate()
            logger.info("FFmpeg process terminated by skip command")
            self.ffmpeg_process = None

        # Signal the playback thread to stop
        if self.playback_thread and self.playback_thread.is_alive():
            logger.info(f"Attempting to skip current song: {self.current_song}")
            self.is_playing = False
            self.playback_thread.join(timeout=2)  # Give the thread a chance to finish
            logger.info("Playback thread joined after skip")

        self.current_song = None
        self.current_url = None

        # Start next song if available
        if self.queue:
            logger.info("Songs remain in queue after skip, calling play_next()")
            success, result = self.play_next()
            if success:
                logger.info(f"Successfully skipped to next song: {result}")
                return True, f"Skipped to next song: {result}"
            logger.error(f"Error playing next song after skip: {result}")
            return False, f"Error playing next song: {result}"

        self.save_queue()  # Save queue if now empty
        logger.info("No more songs in queue after skip")
        return True, "Skipped current song"

    def start_playlist(self):
        pass  # Playlist functionality removed

    def stop_playlist(self):
        pass  # Playlist functionality removed

    def play_next(self, user=None):
        logger.info("play_next called")
        # Playlist functionality removed
        if not self.queue:
            logger.info("No songs in queue to play in play_next")
            self.is_playing = False
            self.current_song = None
            self.current_url = None
            self.current_duration = None  # Reset duration
            self.current_requested_by = None  # Reset requested_by
       
            self.save_queue()  # Save queue if now empty
            return False, "No songs in queue"
        def play_in_thread():
            try:
                import glob
                import os
                # Clean up old song files before download
                for ext in ["mp3", "webm", "m4a", "opus"]:
                    for f in glob.glob(f"song.{ext}"):
                        try:
                            os.remove(f)
                            logger.info(f"Deleted old file: {f}")
                        except Exception as e:
                            logger.error(f"Error deleting old file {f}: {e}")
                url, title, duration, requested_by = self.queue[0]  # Peek at the first song
        
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'outtmpl': 'song.%(ext)s',
                    'quiet': True,
                    'no_warnings': True,
                    'cookiefile': 'cookies.txt'
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    logger.info(f"Downloading song: {title}")
                    result = ydl.download([url])
                    logger.info("Download complete, starting playback")
                    # Check if any song file exists after download
                    found_file = False
                    for ext in ["mp3", "webm", "m4a", "opus"]:
                        if os.path.exists(f"song.{ext}"):
                            found_file = True
                            break
                    if not found_file:
                        logger.error("No song file found after download! Skipping this track.")
                        # Remove from queue and skip
                        self.queue.pop(0)
                        self.save_queue()
                        return
                    # Only now pop from queue and set current song
                    self.queue.pop(0)
                    self.save_queue()
                    self.current_song = title
                    self.current_url = url
                    self.current_duration = duration  # Set duration
                    self.current_requested_by = requested_by  # Set requested_by
        
                    self.is_playing = True
                    # Add song to history
                    self.history.insert(0, title)
                    if len(self.history) > self.max_history:
                        self.history.pop()
                    # Update stats (pass user if available)
                    self.update_stats(title, user)
                    if self.is_playing:  # Check if we should still play this song
                        self.play_websocket()
                    self.is_playing = False
                    self.current_song = None
                    self.current_url = None
                    self.current_duration = None  # Reset duration
                    self.current_requested_by = None
                    # Start next song if available
                    if self.queue:
                        logger.info("Song finished, queue not empty, calling play_next() recursively")
                        self.play_next(user)
                    else:
                        logger.info("Song finished, queue empty")
                        # Playlist functionality removed
            except Exception as e:
                logger.error(f"Error in playback thread: {str(e)}")
                self.is_playing = False
                self.current_song = None
                self.current_url = None
                self.current_duration = None
                self.current_requested_by = None
                # Playlist functionality removed

        # Always start playback in a new thread
        self.playback_thread = threading.Thread(target=play_in_thread)
        self.playback_thread.start()
        logger.info(f"Started playback thread for next song in queue")
        # Return the next song's title for confirmation
        if self.queue:
            url, title, duration, requested_by = self.queue[0]
            message = (
    f"\n✨ Now Streaming\n\n"
    f"📀 Title: {title}\n"
    f"🕒 Duration: {duration:.2f} minutes\n"
    f"🧑‍🎧 Requested by: {requested_by}"
            )
            return True, message
        else:
            return False, "No songs in queue"

    def update_stats(self, title, user=None):
        """Updates player statistics."""
        try:
            if user is not None:
                user_id = getattr(user, 'id', None) or getattr(user, 'username', None) or str(user)
            else:
                user_id = 'unknown'
            if user_id not in self.stats["users"]:
                self.stats["users"][user_id] = {"played_songs": [], "song_counts":{}}
            if title not in self.stats["users"][user_id]["song_counts"]:
                self.stats["users"][user_id]["song_counts"][title] = 0
            self.stats["users"][user_id]["song_counts"][title] += 1
            self.stats["users"][user_id]["played_songs"].append(title)
            if title not in self.stats["songs"]:
                self.stats["songs"][title] = 0
            self.stats["songs"][title] += 1
            self.save_stats()
        except Exception as e:
            logger.error(f"Error updating stats: {e}")

    def set_volume(self, volume_percentage):
        """Set the playback volume (0-300)"""
        self.volume = max(0, min(300, volume_percentage))  # Allow up to 300%
        if self.volume > 100:
            logger.warning(f"Volume set to {self.volume}% (>100% may cause distortion)")
        else:
            logger.info(f"Volume set to {self.volume}%")
        return self.volume

    def play_websocket(self):
        logger.info("Starting websocket playback")

        if not check_ffmpeg_installed():
            logger.error("Cannot play audio: FFmpeg not available")
            return

        # Find the downloaded file (song.mp3, song.webm, song.m4a, etc.)
        song_file = None
        for ext in ["mp3", "webm", "m4a", "opus"]:
            files = glob.glob(f"song.{ext}")
            if files:
                song_file = files[0]
                break
        if not song_file:
            logger.error("No downloaded song file found for playback!")
            return

        # Calculate volume filter value (ffmpeg uses scale of 0.0-3.0 for volume)
        volume_factor = self.volume / 100.0
    # Use alimiter to prevent clipping at high volumes
        audio_filter = f"volume={volume_factor},alimiter=limit=0.95:level_in=1"
    
        ffmpeg_command = [
        'ffmpeg',
        '-re',                    # Read input at native frame rate
        '-i', song_file,          # Input audio file
        '-af', audio_filter,      # Apply volume adjustment with limiter
        '-f', 'mp3',              # Output format
        '-vn',                    # Disable video recording
        '-content_type', 'audio/mpeg',  # Set content type for Icecast
        'icecast://Habibi_78:alkama789@live.radioking.com:80/pop-country-radio67'  # Icecast server URL
    ]

        try:
            logger.info(f"Executing FFmpeg command: {' '.join(ffmpeg_command)}")
            self.ffmpeg_process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if self.ffmpeg_process is not None:
                stdout, stderr = self.ffmpeg_process.communicate()
                if self.ffmpeg_process.returncode != 0:
                    logger.error(f"FFmpeg error: {stderr.decode()}")
                else:
                    logger.info("FFmpeg process completed successfully")
            else:
                logger.error("FFmpeg process did not start properly.")
        except Exception as e:
            logger.error(f"Error in play_websocket: {str(e)}")
            raise
        finally:
            self.ffmpeg_process = None

    def _test_state(self):
        """Debug method to print current state"""
        state = {
            'is_playing': self.is_playing,
            'current_song': self.current_song,
            'current_url': self.current_url,
            'queue_length': len(self.queue),
            'queue_songs': [(title, duration, requested_by) for _, title, duration, requested_by in self.queue],
            'volume': self.volume
        }
        logger.info(f"Current state: {state}")
        self.save_queue()  # Optionally save queue on state test
        return state


class Bot(BaseBot):
    def __init__(self, room_id: str, token: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.room_id = room_id
        self.token = token
        self.music_player = MusicPlayer()
        self.tickets = self.load_tickets()
        self.owners = self.load_owners()
        self.wallet = self.load_wallet()
        self.ticket_mode = False  # Default: free music mode
        self.last_request_time = {}  # Track last song request time per user
        logger.info("Bot initialized")

    async def run(self):
        try:
            bot_definition = BotDefinition(self, self.room_id, self.token)
            await main([bot_definition])
        except Exception as e:
            logger.error(f"Error in bot run: {str(e)}")
            raise

    async def on_start(self, session_metadata: SessionMetadata) -> None:
        self._user_id = session_metadata.user_id
        logger.info("Bot started, sending welcome message")
        # Teleport to saved position if available
        try:
            if os.path.exists('bot_position.json'):
                with open('bot_position.json', 'r') as f:
                    pos_data = json.load(f)
                pos = Position(
                    x=pos_data['x'],
                    y=pos_data['y'],
                    z=pos_data['z'],
                    facing=pos_data.get('facing', 'FrontRight')
                )
                await self.highrise.teleport(self._user_id, pos)
                logger.info(f"Teleported bot to saved position: {pos_data}")
        except Exception as e:
            logger.error(f"Error teleporting to saved position: {e}")
        mode_status = "🎫 Ticket mode enabled" if self.ticket_mode else "🆓 Free mode enabled"
        await self.highrise.chat(f"🎵 Music Bot is ready! Use !play <song name> to play music! {mode_status}")

    async def on_user_join(self, user: User, position: Position) -> None:
        logger.info(f"User joined: {user.username}")
        await self.highrise.chat(f"👋 Welcome @{user.username}! Use !play <song name> to play music!")

    async def handle_play_command(self, user: User, search_query: str):
        if self.ticket_mode and not self.is_owner(user.username):
            tickets_count = self.get_user_tickets(user.username)
            if tickets_count <= 0:
                logger.info(f"User {user.username} has no tickets")
                await self.highrise.chat(f"❌ {user.username}, you don't have any tickets to request songs. Your wallet has 0 tickets.")
                return
            success = self.use_ticket(user.username)
            if not success:
                logger.error(f"Ticket deduction failed for {user.username}")
                await self.highrise.chat(f"❌ {user.username}, ticket deduction failed. Please try again.")
                return
            remaining = self.get_user_tickets(user.username)
            await self.highrise.chat(f"🎫 {user.username} used 1 music ticket. {remaining} ticket(s) remaining in wallet.")

        logger.info(f"Processing play command for query: {search_query} by {user.username}")
        await self.highrise.chat(f"🔍 Searching for: {search_query}")
        url, title, duration, error = self.music_player.search_song(search_query)

        if error == "too_long":
            logger.info(f"Song too long for query: {search_query}")
            await self.highrise.chat("❌ This song exceeds the 10-minute duration limit")
        elif url and title and duration is not None:
            position, queue_message = self.music_player.add_to_queue(url, title, duration, user.username)
            self.music_player._test_state()
            if position == 0:  # Song not added due to limit
                await self.highrise.chat(queue_message)
            else:
                await self.highrise.chat(queue_message)
                if self.music_player.is_playing:
                    logger.info(f"Song currently playing, added '{title}' to queue at position {position}")
                else:
                    logger.info(f"No song playing, starting '{title}' immediately")
                    success, result = self.music_player.play_next(user)
                    self.music_player._test_state()
                    if success:
                        await self.highrise.chat(result)
                    else:
                        logger.error(f"Error playing song: {result}")
                        await self.highrise.chat(f"❌ Error playing song: {result}")
        else:
            logger.error(f"Failed to find song for query: {search_query}")
            await self.highrise.chat("❌ Could not find the song. Please try again or check the query.")

    async def on_chat(self, user: User, message: str) -> None:
        logger.info(f"Received chat message from {user.username}: {message}")

        if message.startswith('!play') or message.startswith('!p'):
            search_query = message[6:].strip() if message.startswith('!play') else message[3:].strip()
            await self.handle_play_command(user, search_query)
     
        
        elif message.startswith('!q'):
            logger.info("Processing queue command")
            self.music_player._test_state()

        # Send full queue status (current track + queue)
            queue_status = self.music_player.get_queue_status()
            await self.send_long_message(queue_status, chunk_size=250)


        elif message.startswith('!np'):
            logger.info("Processing now playing command")
            current_song = self.music_player.get_current_song()
            await self.highrise.chat(current_song)

        elif message.startswith('!delq'):
            logger.info(f"Processing delete song command for user: {user.username}")
            success, result = self.music_player.delete_user_song(user.username)
            await self.highrise.send_whisper(user.id, result)
        
        elif message.startswith('!help'):
            logger.info("Processing help command")
            help_text_header = "🎵 **Music Bot Commands** 🎵"
            help_text_music = (
                "!p <song> - Play a song or add to queue (10-min limit)\n"
                "!q - Show current queue\n"
                "!np - Show currently playing song\n"
                "!skip - Skip to next song\n"
                "!delq - Delete your recent song from the queue\n"
                "!clearq - Clear the song queue\n"
                "!help - Show this help message\n"
                "!stats - Show player statistics\n"
            )
            help_text_ticket = (
                "🎫 Ticket System Commands 🎫\n"
                "!tickets - Check how many music tickets you have (1 ticket = 1 song)\n"
                "!ticketsystem - Enable ticket mode (users need tickets to request songs) [Owner only]\n"
                "!freesystem - Return to free music mode (anyone can request songs) [Owner only]\n"
            )
            help_text_owner = (
                "👑 Owner Commands👑\n"
                "!give @username 100tk - Give tickets to a user\n"
            )
            await self.send_long_message(help_text_header, chunk_size=150)
            await self.send_long_message(help_text_music, chunk_size=150)
            await self.send_long_message(help_text_ticket, chunk_size=150)
            await self.send_long_message(help_text_owner, chunk_size=150)

        elif message.startswith('!ticketsystem') and self.is_owner(user.username):
            self.ticket_mode = True
            await self.highrise.chat("🎵 Music system changed to ticket mode. Users need tickets to request songs.")

        elif message.startswith('!freesystem') and self.is_owner(user.username):
            self.ticket_mode = False
            await self.highrise.chat("🎵 Music system changed to free mode. Anyone can request songs.")

        elif message.startswith('!give') and self.is_owner(user.username):
            parts = message.split()
            if len(parts) >= 3:
                target_user = parts[1]
                # Remove @ symbol if present
                if target_user.startswith('@'):
                    target_user = target_user[1:]

                try:
                    amount = int(parts[2].lower().replace('tk', ''))
                    if amount > 0:
                        new_balance = self.add_tickets(target_user, amount)
                        await self.highrise.chat(f"🎫 {amount} tickets given to {target_user}. New balance: {new_balance} tickets")
                    else:
                        await self.highrise.chat("❌ Amount must be greater than 0")
                except ValueError:
                    await self.highrise.chat("❌ Invalid amount format. Use !give @username 100tk")
            else:
                await self.highrise.chat("❌ Invalid command format. Use !give @username 100tk")

        elif message.startswith('!history'):
            logger.info("Processing history command")
            if not self.music_player.history:
                await self.highrise.chat("📋 No song history available")
            else:
                history_text = "🕒 Recently Played Songs:\n"
                for i, title in enumerate(self.music_player.history, 1):
                    history_text += f"{i}. {title}\n"
                await self.highrise.chat(history_text)

        elif message.startswith('!clearq'):
            logger.info("Processing clear queue command")
            queue_length = len(self.music_player.queue)
            self.music_player.queue = []
            await self.highrise.chat(f"🧹 Cleared {queue_length} songs from the queue")

        elif message.startswith('!skip'):
            if not self.is_owner(user.username):
                await self.highrise.chat("❌ Only owners can use the !skip command.")
                return
            logger.info("Skipping the song!")
            # Log the state before skip
            self.music_player._test_state()
            success, msg = self.music_player.skip_song()
            # Log the state after skip
            self.music_player._test_state()
            await self.highrise.chat(msg)

        elif message.startswith('!volume'):
            try:
                volume_level = message.split(' ')[1]
                if volume_level.isdigit() and 0 <= int(volume_level) <= 300:  # Allow up to 300
                    logger.info(f"Setting volume to {volume_level}%")
                    self.music_player.set_volume(int(volume_level))
                    if int(volume_level) > 100:
                        await self.highrise.chat(f"🔊 Volume set to {volume_level}% (⚠️ High volumes may cause distortion)")
                    else:
                        await self.highrise.chat(f"🔊 Volume set to {volume_level}%")
                else:
                    await self.highrise.chat("❌ Volume must be a number between 0 and 300")
            except IndexError:
                await self.highrise.chat("❌ Please specify a volume level, e.g., !volume 150")


        
        elif message.startswith('!stats'):
            logger.info("Processing stats command")
            stats_text = "\U0001F4CA Player Statistics:\n"
            # Add code here to display stats from self.music_player.stats
            stats_text += self.format_stats(self.music_player.stats)
            await self.send_long_message(stats_text)

        # Ticket System Commands
        elif message == '!tickets':
            # Get tickets from wallet
            user_tickets_count = self.get_user_tickets(user.username)
            await self.highrise.chat(f"🎫 {user.username}, you have {user_tickets_count} music ticket(s) in your wallet.")

        elif message.startswith('!setpos'):
            try:
                # Get all users and their positions
                room_users = await self.highrise.get_room_users()
                user_pos = None
                for u, pos in room_users.content:
                    if u.id == user.id:
                        user_pos = pos
                        break
                if user_pos is None:
                    await self.highrise.chat("❌ Could not get your position.")
                else:
                    # Save position to file
                    with open('bot_position.json', 'w') as f:
                        json.dump({'x': user_pos.x, 'y': user_pos.y, 'z': user_pos.z, 'facing': getattr(user_pos, 'facing', 'FrontRight')}, f)
                    await self.highrise.teleport(self._user_id, user_pos)
                    await self.highrise.chat(f"✅ Bot position set to your location: x={user_pos.x}, y={user_pos.y}, z={user_pos.z}")
            except Exception as e:
                import traceback
                logger.error(f"Error setting bot position: {e}\n{traceback.format_exc()}")
                await self.highrise.chat(f"❌ Failed to set bot position: {e}")

    async def send_long_message(self, message, chunk_size=450):
        """Send a long message in chunks to avoid exceeding message length limits, even if a single line is too long."""
        lines = message.split('\n')
        chunk = ""
        for line in lines:
            while len(line) > chunk_size:
                if chunk:
                    await self.highrise.chat(chunk)
                    chunk = ""
                await self.highrise.chat(line[:chunk_size])
                line = line[chunk_size:]
            if len(chunk) + len(line) + 1 > chunk_size:
                await self.highrise.chat(chunk)
                chunk = ""
            if chunk:
                chunk += "\n"
            chunk += line
        if chunk:
            await self.highrise.chat(chunk)

    def format_stats(self, stats):
        """Formats the statistics for display."""
        formatted_stats = ""
        users = stats.get("users", {})
        if not isinstance(users, dict):
            users = {}
        for user_id, user_data in users.items():
            formatted_stats += f"User {user_id}:\n"
            # Ensure played_songs is a list
            played_songs = user_data.get('played_songs', [])
            if isinstance(played_songs, list):
                formatted_stats += f"  Played Songs: {', '.join(played_songs)}\n"
            else:
                formatted_stats += f"  Played Songs: {played_songs}\n"
            # Ensure song_counts is a dict
            song_counts = user_data.get('song_counts', {})
            if isinstance(song_counts, dict):
                formatted_stats += f"  Song Counts: {song_counts}\n"
            else:
                formatted_stats += f"  Song Counts: {str(song_counts)}\n"
        formatted_stats += "\nOverall Song Counts:\n"
        formatted_stats += f"  {stats['songs']}\n"
        return formatted_stats

    def load_tickets(self):
        """Load tickets from JSON file"""
        try:
            if os.path.exists('tickets.json'):
                with open('tickets.json', 'r') as f:
                    return json.load(f)
            else:
                # Create default tickets file if it doesn't exist
                default_tickets = {
                    "next_id": 1,
                    "open": {},
                    "closed": {}
                }
                with open('tickets.json', 'w') as f:
                    json.dump(default_tickets, f, indent=2)
                return default_tickets
        except Exception as e:
            logger.error(f"Error loading tickets: {e}")
            return {"next_id": 1, "open": {}, "closed": {}}

    def save_tickets(self):
        """Save tickets to JSON file"""
        try:
            with open('tickets.json', 'w') as f:
                json.dump(self.tickets, f, indent=2)
            logger.info("Tickets saved successfully")
        except Exception as e:
            logger.error(f"Error saving tickets: {e}")

    def create_ticket(self, username, issue):
        """Create a new ticket"""
        ticket_id = self.tickets["next_id"]
        self.tickets["next_id"] += 1

        timestamp = asyncio.get_event_loop().time()

        new_ticket = {
            "id": ticket_id,
            "username": username,
            "issue": issue,
            "status": "open",
            "created_at": timestamp,
            "updated_at": timestamp
        }

        self.tickets["open"][str(ticket_id)] = new_ticket
        self.save_tickets()
        return ticket_id

    def close_ticket(self, ticket_id):
        """Close a ticket by ID"""
        ticket_id = str(ticket_id)
        if ticket_id in self.tickets["open"]:
            ticket = self.tickets["open"][ticket_id]
            ticket["status"] = "closed"
            ticket["updated_at"] = asyncio.get_event_loop().time()

            self.tickets["closed"][ticket_id] = ticket
            del self.tickets["open"][ticket_id]
            self.save_tickets()
            return True
        return False

    def get_ticket(self, ticket_id):
        """Get a ticket by ID"""
        ticket_id = str(ticket_id)
        if ticket_id in self.tickets["open"]:
            return self.tickets["open"][ticket_id]
        elif ticket_id in self.tickets["closed"]:
            return self.tickets["closed"][ticket_id]
        return None

    def list_tickets(self, status="open"):
        """List all tickets with given status"""
        if status == "open":
            return self.tickets["open"]
        elif status == "closed":
            return self.tickets["closed"]
        else:
            # Combine both open and closed tickets
            all_tickets = {}
            all_tickets.update(self.tickets["open"])
            all_tickets.update(self.tickets["closed"])
            return all_tickets

    def load_owners(self):
        """Load owner information from JSON file"""
        try:
            if os.path.exists('owner.json'):
                with open('owner.json', 'r') as f:
                    return json.load(f)
            else:
                # Create default owner file if it doesn't exist
                default_owners = {
                    "owners": [],
                    "admins": []
                }
                with open('owner.json', 'w') as f:
                    json.dump(default_owners, f, indent=2)
                return default_owners
        except Exception as e:
            logger.error(f"Error loading owners: {e}")
            return {"owners": [], "admins": []}

    def save_owners(self):
        """Save owner information to JSON file"""
        try:
            with open('owner.json', 'w') as f:
                json.dump(self.owners, f, indent=2)
            logger.info("Owners saved successfully")
        except Exception as e:
            logger.error(f"Error saving owners: {e}")

    def is_owner(self, username):
        """Check if a user is an owner"""
        for owner in self.owners.get("owners", []):
            if isinstance(owner, dict):
                if owner.get("username") == username:
                    return True
            elif isinstance(owner, str):
                if owner == username:
                    return True
        return False

    def load_wallet(self):
        """Load wallet data from JSON file"""
        try:
            if os.path.exists('wallet.json'):
                with open('wallet.json', 'r') as f:
                    return json.load(f)
            else:
                # Create default wallet file if it doesn't exist
                default_wallet = {"users": {}}
                with open('wallet.json', 'w') as f:
                    json.dump(default_wallet, f, indent=2)
                return default_wallet
        except Exception as e:
            logger.error(f"Error loading wallet: {e}")
            return {"users": {}}

    def save_wallet(self):
        """Save wallet data to JSON file"""
        try:
            with open('wallet.json', 'w') as f:
                json.dump(self.wallet, f, indent=2)
            logger.info("Wallet saved successfully")
        except Exception as e:
            logger.error(f"Error saving wallet: {e}")

    def get_user_tickets(self, username):
        """Get the number of tickets a user has"""
        return self.wallet["users"].get(username, 0)

    def add_tickets(self, username, amount):
        """Add tickets to a user's wallet"""
        current_amount = self.get_user_tickets(username)
        self.wallet["users"][username] = current_amount + amount
        self.save_wallet()
        return self.wallet["users"][username]

    def check_user_tickets(self, username):
        """Check if a user has available tickets for music requests"""
        tickets = self.get_user_tickets(username)
        logger.info(f"Checking tickets for {username}: {tickets}")
        return tickets > 0

    def use_ticket(self, username):
        """Use a ticket for a music request"""
        tickets = self.get_user_tickets(username)
        logger.info(f"Attempting to use ticket for {username}. Current tickets: {tickets}")
        if tickets > 0:
            self.wallet["users"][username] = tickets - 1
            self.save_wallet()
            remaining = self.get_user_tickets(username)
            logger.info(f"Used ticket for user {username}, remaining: {remaining}")
            return True
        logger.warning(f"User {username} has no tickets to use")
        return False


if __name__ == "__main__":
    bot_token = "37b470e5b0d53f4ce9788b0e9e0e9ed2b9e3b86d3ebcdcb0a363acdf2bc9e24d"
    room_id = "66e70157bbf763a3d9957b77"
    asyncio.run(Bot(room_id, bot_token).run())
