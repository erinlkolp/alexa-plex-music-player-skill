import logging
import boto3
from decimal import Decimal
from difflib import get_close_matches
from plexapi.server import PlexServer
from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_core.dispatch_components import (
    AbstractRequestHandler, AbstractExceptionHandler
)
from ask_sdk_core.utils import is_request_type, is_intent_name
from ask_sdk_model import Response
from ask_sdk_model.ui import SimpleCard
from ask_sdk_model.interfaces.audioplayer import (
    PlayDirective, PlayBehavior, AudioItem, Stream, AudioItemMetadata,
    StopDirective
)

# Configure these with your Plex server details
PLEX_TOKEN = "YOUR_PLEX_TOKEN"
PLEX_SERVER_NAME = "YOUR_SERVER_NAME"

# Automatically use relay URL for local playback
USE_LOCAL_AUDIO_URL = True

# DynamoDB table name
DYNAMODB_TABLE_NAME = "PlexAlexaQueue"

# Artist name mappings for spoken variations to Plex names
# This is now optional - fuzzy matching will handle most cases automatically
ARTIST_MAPPINGS = {
    "sugar free": "Suga Free",
    "austin larolle": "Austin Larold",
    "austin larold": "Austin Larold",
    "austin": "Austin Larold",
    "doctor dre": "Dr. Dre",
    "doctor dray": "Dr. Dre",
    "the doctor": "Dr. Dre",
    "86 love": "86LOVE",
}

# Cache for Plex artists to avoid repeated API calls
artist_cache = []
artist_cache_loaded = False

# Will be populated automatically from Plex connections
LOCAL_RELAY_URL = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize DynamoDB
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

plex = None
MUSIC = None

try:
    from plexapi.myplex import MyPlexAccount
    from plexapi.server import PlexServer
    
    logger.info("Connecting to Plex via MyPlexAccount...")
    account = MyPlexAccount(token=PLEX_TOKEN)
    
    logger.info("Available Plex servers:")
    for resource in account.resources():
        logger.info(f"  Server: {resource.name}")
        for conn in resource.connections:
            logger.info(f"    Connection: {conn.uri} (local: {conn.local}, relay: {conn.relay})")
    
    logger.info(f"Attempting to connect to server: {PLEX_SERVER_NAME}")
    server_resource = account.resource(PLEX_SERVER_NAME)

    # Find relay and direct public connections
    direct_connection = None
    relay_connection = None

    for conn in server_resource.connections:
        if not conn.local and not conn.relay:
            direct_connection = conn.uri
            logger.info(f"Found direct public connection: {direct_connection}")
        elif conn.relay:
            relay_connection = conn.uri
            logger.info(f"Found relay connection: {relay_connection}")

    # Prefer relay connection for both Lambda and audio streaming (more reliable for Alexa)
    if relay_connection:
        LOCAL_RELAY_URL = relay_connection
        plex = PlexServer(relay_connection, PLEX_TOKEN, timeout=15)
        logger.info(f"Successfully connected via relay: {relay_connection}")
        logger.info(f"Using relay URL for audio streaming: {relay_connection}")
    elif direct_connection:
        plex = PlexServer(direct_connection, PLEX_TOKEN, timeout=15)
        logger.info(f"Successfully connected via direct connection: {direct_connection}")
    else:
        logger.error("No public or relay connection found, falling back to default")
        plex = server_resource.connect(timeout=15)
    
    MUSIC = plex.library.section('Music')
    logger.info("Successfully connected to Music library")
except Exception as e:
    logger.error(f"Error connecting to Plex server: {e}", exc_info=True)
    plex = None
    MUSIC = None

def get_audio_url(track):
    try:
        # Use relay URL if configured for local playback, otherwise use remote
        if USE_LOCAL_AUDIO_URL and LOCAL_RELAY_URL:
            base_url = LOCAL_RELAY_URL
            logger.info(f"Using relay URL for audio streaming: {base_url}")
        else:
            base_url = plex._baseurl
            logger.info(f"Using remote URL for audio streaming: {base_url}")
        
        if track.media and len(track.media) > 0:
            media = track.media[0]
            if media.parts and len(media.parts) > 0:
                part = media.parts[0]
                direct_url = f"{base_url}{part.key}?X-Plex-Token={PLEX_TOKEN}"
                return direct_url
        
        return track.getStreamURL()
    except Exception as e:
        logger.error(f"Error getting audio URL: {e}", exc_info=True)
        return track.getStreamURL()

def get_user_id(handler_input):
    return handler_input.request_envelope.context.system.user.user_id

def save_queue(user_id, tracks, current_index=0, shuffle=False):
    """Save queue to DynamoDB."""
    try:
        item = {
            'user_id': user_id,
            'tracks': [{'key': int(t.ratingKey), 'title': t.title, 'artist': t.artist().title if hasattr(t, 'artist') else 'Unknown'} for t in tracks],
            'current_index': int(current_index),
            'shuffle': shuffle
        }
        table.put_item(Item=item)
        logger.info(f"Saved queue to DynamoDB for user {user_id}: {len(tracks)} tracks, index {current_index}, shuffle {shuffle}")
    except Exception as e:
        logger.error(f"Error saving queue to DynamoDB: {e}", exc_info=True)

def get_queue(user_id):
    """Get queue from DynamoDB."""
    try:
        response = table.get_item(Key={'user_id': user_id})
        if 'Item' in response:
            logger.info(f"Retrieved queue from DynamoDB for user {user_id}")
            return response['Item']
        else:
            logger.info(f"No queue found in DynamoDB for user {user_id}")
            return None
    except Exception as e:
        logger.error(f"Error getting queue from DynamoDB: {e}", exc_info=True)
        return None

def update_queue_index(user_id, new_index):
    """Update just the current index in DynamoDB."""
    try:
        table.update_item(
            Key={'user_id': user_id},
            UpdateExpression='SET current_index = :index',
            ExpressionAttributeValues={':index': int(new_index)}
        )
        logger.info(f"Updated queue index for user {user_id} to {new_index}")
    except Exception as e:
        logger.error(f"Error updating queue index in DynamoDB: {e}", exc_info=True)

def get_track_by_key(rating_key):
    try:
        return plex.fetchItem(int(rating_key))
    except Exception as e:
        logger.error(f"Error fetching track {rating_key}: {e}")
        return None

def filter_tracks_by_rating(tracks):
    """
    Filter tracks to exclude 1-star rated songs.
    Includes: 2+ star songs and unrated songs.
    Excludes: 1-star songs (rating 2.0 on Plex's 0-10 scale).
    """
    filtered_tracks = []
    for track in tracks:
        try:
            # Check if track has userRating attribute
            if hasattr(track, 'userRating') and track.userRating is not None:
                # userRating is on a 0-10 scale (1 star = 2.0, 2 stars = 4.0, etc.)
                # Exclude 1-star songs (rating 2.0)
                if track.userRating >= 4.0 or track.userRating == 0:
                    # Include 2+ stars or unrated (0)
                    filtered_tracks.append(track)
                else:
                    # Exclude 1-star songs
                    logger.info(f"Filtering out 1-star track: {track.title} (rating: {track.userRating})")
            else:
                # Track has no rating, include it
                filtered_tracks.append(track)
        except Exception as e:
            # If there's any error checking the rating, include the track to be safe
            logger.warning(f"Error checking rating for track, including anyway: {e}")
            filtered_tracks.append(track)

    logger.info(f"Filtered tracks: {len(tracks)} -> {len(filtered_tracks)} (excluded {len(tracks) - len(filtered_tracks)} 1-star songs)")
    return filtered_tracks

def load_artist_cache():
    """Load all artist names from Plex for fuzzy matching."""
    global artist_cache, artist_cache_loaded
    
    if artist_cache_loaded:
        return artist_cache
    
    try:
        logger.info("Loading artist cache from Plex...")
        artists = MUSIC.searchArtists()
        artist_cache = [artist.title for artist in artists]
        artist_cache_loaded = True
        logger.info(f"Loaded {len(artist_cache)} artists into cache")
        return artist_cache
    except Exception as e:
        logger.error(f"Error loading artist cache: {e}", exc_info=True)
        return []

def fuzzy_match_artist(spoken_name):
    """
    Use fuzzy matching to find the closest artist name in Plex library.
    Returns the matched artist name or the original if no good match found.
    """
    # First check manual mappings
    manual_match = ARTIST_MAPPINGS.get(spoken_name.lower())
    if manual_match:
        logger.info(f"Manual mapping: '{spoken_name}' -> '{manual_match}'")
        return manual_match
    
    # Load artist cache if needed
    artists = load_artist_cache()
    
    if not artists:
        logger.warning("Artist cache is empty, returning original name")
        return spoken_name
    
    # Use fuzzy matching with cutoff of 0.6 (60% similarity)
    matches = get_close_matches(spoken_name, artists, n=1, cutoff=0.6)
    
    if matches:
        matched_name = matches[0]
        logger.info(f"Fuzzy match: '{spoken_name}' -> '{matched_name}'")
        return matched_name
    else:
        logger.info(f"No fuzzy match found for '{spoken_name}', using original")
        return spoken_name

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        try:
            if not plex or not MUSIC:
                speech_text = "Warning: I couldn't connect to your Plex server. Please check your configuration and try again later."
                return handler_input.response_builder.speak(speech_text).set_card(
                    SimpleCard("Plex Connection Error", speech_text)).set_should_end_session(True).response
            
            speech_text = "Plex Music is ready. You can ask me to play music from your Plex server."
            reprompt_text = "You can say, for example, play music by Queen, or play the album Abbey Road."
            
            return handler_input.response_builder.speak(speech_text).ask(reprompt_text).set_card(
                SimpleCard("Plex Music", speech_text)).response
        except Exception as e:
            logger.error(f"Error in LaunchRequestHandler: {e}", exc_info=True)
            speech_text = "Sorry, there was an error starting the skill."
            return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response

class PlayMusicIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("PlayMusicIntent")(handler_input)

    def handle(self, handler_input):
        speech_text = "Sorry, something went wrong."
        
        try:
            slots = handler_input.request_envelope.request.intent.slots
            user_id = get_user_id(handler_input)
            
            artist_name = None
            album_name = None
            track_name = None
            playlist_name = None
            
            if slots and "artist" in slots:
                slot = slots["artist"]
                if slot and hasattr(slot, 'value') and slot.value:
                    artist_name = slot.value
                    # Use fuzzy matching to find closest artist in Plex
                    artist_name = fuzzy_match_artist(artist_name)
                    
            if slots and "album" in slots:
                slot = slots["album"]
                if slot and hasattr(slot, 'value') and slot.value:
                    album_name = slot.value
                    
            if slots and "track" in slots:
                slot = slots["track"]
                if slot and hasattr(slot, 'value') and slot.value:
                    track_name = slot.value
            
            if slots and "playlist" in slots:
                slot = slots["playlist"]
                if slot and hasattr(slot, 'value') and slot.value:
                    playlist_name = slot.value
            
            logger.info(f"Received request - Artist: {artist_name}, Album: {album_name}, Track: {track_name}, Playlist: {playlist_name}")

            # Check if shuffle is requested (from utterance like "shuffle artist")
            should_shuffle = "shuffle" in str(handler_input.request_envelope.request.intent).lower()
            
            logger.info(f"Shuffle requested: {should_shuffle}")

            if not plex or not MUSIC:
                speech_text = "I couldn't connect to your Plex server. Please check the configuration."
                return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
            
            tracks_to_play = []
            
            if playlist_name:
                try:
                    playlists = plex.playlists()
                    matching_playlist = None
                    for playlist in playlists:
                        if playlist_name.lower() in playlist.title.lower():
                            matching_playlist = playlist
                            break
                    
                    if matching_playlist:
                        # Get all playlist tracks and filter out 1-star songs
                        all_tracks = matching_playlist.items()
                        filtered_tracks = filter_tracks_by_rating(all_tracks)
                        # Limit to 150 tracks to avoid timeout
                        tracks_to_play = filtered_tracks[:150]
                        total_tracks = len(filtered_tracks)

                        if total_tracks > 150:
                            speech_text = f"Playing the first 150 tracks from playlist {matching_playlist.title}, which has {total_tracks} total tracks."
                        else:
                            speech_text = f"Playing playlist {matching_playlist.title}."

                        if should_shuffle:
                            speech_text = f"Shuffling playlist {matching_playlist.title}."
                    else:
                        speech_text = f"I couldn't find a playlist named {playlist_name}."
                        return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
                except Exception as e:
                    logger.error(f"Error searching playlists: {e}")
                    speech_text = f"I had trouble searching for the playlist {playlist_name}."
                    return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
            
            elif track_name:
                results = MUSIC.searchTracks(title=track_name)
                if results:
                    # Filter out 1-star songs from results
                    filtered_results = filter_tracks_by_rating(results)
                    if filtered_results:
                        tracks_to_play = [filtered_results[0]]
                        speech_text = f"Playing {filtered_results[0].title} by {filtered_results[0].artist().title}."
                    else:
                        speech_text = f"I found {track_name}, but it's rated 1 star. Skipping."
                        return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
                else:
                    speech_text = f"I couldn't find the track {track_name}."
                    return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
                    
            elif album_name:
                results = MUSIC.searchAlbums(title=album_name)
                if results:
                    album = results[0]
                    all_album_tracks = album.tracks()
                    # Filter out 1-star songs
                    tracks_to_play = filter_tracks_by_rating(all_album_tracks)
                    speech_text = f"Playing the album {album.title}."
                    if should_shuffle:
                        speech_text = f"Shuffling the album {album.title}."
                else:
                    speech_text = f"I couldn't find the album {album_name}."
                    return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
                    
            elif artist_name:
                results = MUSIC.searchArtists(title=artist_name)
                if results:
                    artist = results[0]
                    all_artist_tracks = artist.tracks()

                    # Remove duplicates by ratingKey (same track on multiple albums)
                    seen_keys = set()
                    unique_tracks = []
                    for track in all_artist_tracks:
                        if track.ratingKey not in seen_keys:
                            seen_keys.add(track.ratingKey)
                            unique_tracks.append(track)

                    logger.info(f"Artist tracks: {len(all_artist_tracks)} total, {len(unique_tracks)} unique")

                    # Filter out 1-star songs
                    filtered_artist_tracks = filter_tracks_by_rating(unique_tracks)
                    tracks_to_play = filtered_artist_tracks[:150]
                    speech_text = f"Playing music by {artist.title}."
                    if should_shuffle:
                        speech_text = f"Shuffling music by {artist.title}."
                else:
                    speech_text = f"I couldn't find the artist {artist_name}."
                    return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
            else:
                speech_text = "You need to specify what to play. For example, play music by The Beatles, or play playlist Favorites."
                return handler_input.response_builder.speak(speech_text).ask(speech_text).response

            # Shuffle if requested
            if should_shuffle and len(tracks_to_play) > 1:
                import random
                tracks_to_play = list(tracks_to_play)
                random.shuffle(tracks_to_play)
                logger.info(f"Shuffled queue of {len(tracks_to_play)} tracks")

            # Save the queue to DynamoDB for next/previous navigation
            save_queue(user_id, tracks_to_play, 0, should_shuffle)

            if tracks_to_play:
                first_track = tracks_to_play[0]
                audio_url = get_audio_url(first_track)
                
                logger.info(f"Playing track: {first_track.title} from URL: {audio_url}")
                
                metadata = AudioItemMetadata(
                    title=first_track.title,
                    subtitle=first_track.artist().title if hasattr(first_track, 'artist') else "Unknown Artist"
                )
                
                stream = Stream(
                    token=str(first_track.ratingKey),
                    url=audio_url,
                    offset_in_milliseconds=0
                )
                
                audio_item = AudioItem(
                    stream=stream,
                    metadata=metadata
                )
                
                play_directive = PlayDirective(
                    play_behavior=PlayBehavior.REPLACE_ALL,
                    audio_item=audio_item
                )
                
                return handler_input.response_builder.speak(speech_text).add_directive(
                    play_directive).set_card(
                    SimpleCard("Now Playing", f"{first_track.title} by {first_track.artist().title}")).response

        except Exception as e:
            logger.error(f"Error in PlayMusicIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble processing your request."

        return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response

class NextIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.NextIntent")(handler_input)
    
    def handle(self, handler_input):
        try:
            user_id = get_user_id(handler_input)
            queue_data = get_queue(user_id)
            
            if not queue_data or not queue_data.get('tracks'):
                speech_text = "There's no queue. Please play something first."
                return handler_input.response_builder.speak(speech_text).response
            
            current_index = int(queue_data.get('current_index', 0))
            tracks = queue_data['tracks']
            
            next_index = current_index + 1
            
            if next_index >= len(tracks):
                speech_text = "You've reached the end of the queue."
                return handler_input.response_builder.speak(speech_text).response
            
            # Don't update index here - PlaybackStarted will handle it
            
            track_info = tracks[next_index]
            track = get_track_by_key(track_info['key'])
            
            if not track:
                speech_text = "Sorry, I couldn't load the next track."
                return handler_input.response_builder.speak(speech_text).response
            
            audio_url = get_audio_url(track)
            logger.info(f"Playing next track: {track.title}")
            
            metadata = AudioItemMetadata(
                title=track.title,
                subtitle=track.artist().title if hasattr(track, 'artist') else "Unknown Artist"
            )
            
            stream = Stream(
                token=str(track.ratingKey),
                url=audio_url,
                offset_in_milliseconds=0
            )
            
            audio_item = AudioItem(
                stream=stream,
                metadata=metadata
            )
            
            play_directive = PlayDirective(
                play_behavior=PlayBehavior.REPLACE_ALL,
                audio_item=audio_item
            )
            
            return handler_input.response_builder.add_directive(play_directive).response
            
        except Exception as e:
            logger.error(f"Error in NextIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble skipping to the next track."
            return handler_input.response_builder.speak(speech_text).response

class PreviousIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.PreviousIntent")(handler_input)
    
    def handle(self, handler_input):
        try:
            user_id = get_user_id(handler_input)
            queue_data = get_queue(user_id)
            
            if not queue_data or not queue_data.get('tracks'):
                speech_text = "There's no queue. Please play something first."
                return handler_input.response_builder.speak(speech_text).response
            
            current_index = int(queue_data.get('current_index', 0))
            tracks = queue_data['tracks']
            
            prev_index = current_index - 1
            
            if prev_index < 0:
                speech_text = "You're at the beginning of the queue."
                return handler_input.response_builder.speak(speech_text).response
            
            # Don't update index here - PlaybackStarted will handle it
            
            track_info = tracks[prev_index]
            track = get_track_by_key(track_info['key'])
            
            if not track:
                speech_text = "Sorry, I couldn't load the previous track."
                return handler_input.response_builder.speak(speech_text).response
            
            audio_url = get_audio_url(track)
            logger.info(f"Playing previous track: {track.title}")
            
            metadata = AudioItemMetadata(
                title=track.title,
                subtitle=track.artist().title if hasattr(track, 'artist') else "Unknown Artist"
            )
            
            stream = Stream(
                token=str(track.ratingKey),
                url=audio_url,
                offset_in_milliseconds=0
            )
            
            audio_item = AudioItem(
                stream=stream,
                metadata=metadata
            )
            
            play_directive = PlayDirective(
                play_behavior=PlayBehavior.REPLACE_ALL,
                audio_item=audio_item
            )
            
            return handler_input.response_builder.add_directive(play_directive).response
            
        except Exception as e:
            logger.error(f"Error in PreviousIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble going back to the previous track."
            return handler_input.response_builder.speak(speech_text).response

class PlaybackStartedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackStarted")(handler_input)
    
    def handle(self, handler_input):
        logger.info("Playback started")
        
        # Update the index when a new track actually starts playing
        try:
            current_token = handler_input.request_envelope.request.token
            user_id = get_user_id(handler_input)
            queue_data = get_queue(user_id)
            
            if queue_data and queue_data.get('tracks'):
                tracks = queue_data['tracks']
                
                # Find the track in the queue by matching the token (rating key)
                for index, track_info in enumerate(tracks):
                    if str(track_info['key']) == str(current_token):
                        current_queue_index = int(queue_data.get('current_index', 0))
                        
                        # Only update if this is a different track
                        if index != current_queue_index:
                            update_queue_index(user_id, index)
                            logger.info(f"Updated queue index to {index} for track {track_info['title']}")
                        break
        except Exception as e:
            logger.error(f"Error updating index in PlaybackStarted: {e}", exc_info=True)
        
        return handler_input.response_builder.response

class PlaybackNearlyFinishedHandler(AbstractRequestHandler):
    """Handler for AudioPlayer.PlaybackNearlyFinished - Auto-queue next track."""
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackNearlyFinished")(handler_input)
    
    def handle(self, handler_input):
        try:
            # Get the current track token from the request
            current_token = handler_input.request_envelope.request.token
            user_id = get_user_id(handler_input)
            
            logger.info(f"Playback nearly finished for track token: {current_token}")
            
            # Get queue from DynamoDB
            queue_data = get_queue(user_id)
            
            if not queue_data or not queue_data.get('tracks'):
                logger.info("No queue found, not enqueuing next track")
                return handler_input.response_builder.response
            
            current_index = int(queue_data.get('current_index', 0))
            tracks = queue_data['tracks']
            
            # Calculate next track index
            next_index = current_index + 1
            
            if next_index >= len(tracks):
                logger.info("Reached end of queue, not enqueuing")
                return handler_input.response_builder.response
            
            # DON'T update index yet - wait until PlaybackStarted
            # This way "what's playing" shows the correct current track
            
            # Get next track
            track_info = tracks[next_index]
            track = get_track_by_key(track_info['key'])
            
            if not track:
                logger.error("Could not fetch next track from Plex")
                return handler_input.response_builder.response
            
            audio_url = get_audio_url(track)
            logger.info(f"Auto-queuing next track: {track.title}")
            
            # Create metadata
            metadata = AudioItemMetadata(
                title=track.title,
                subtitle=track.artist().title if hasattr(track, 'artist') else "Unknown Artist"
            )
            
            # Create stream
            stream = Stream(
                token=str(track.ratingKey),
                url=audio_url,
                offset_in_milliseconds=0,
                expected_previous_token=current_token
            )
            
            # Create audio item
            audio_item = AudioItem(
                stream=stream,
                metadata=metadata
            )
            
            # Create play directive with ENQUEUE behavior
            play_directive = PlayDirective(
                play_behavior=PlayBehavior.ENQUEUE,
                audio_item=audio_item
            )
            
            return handler_input.response_builder.add_directive(play_directive).response
            
        except Exception as e:
            logger.error(f"Error in PlaybackNearlyFinishedHandler: {e}", exc_info=True)
            return handler_input.response_builder.response

class PlaybackFinishedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackFinished")(handler_input)
    
    def handle(self, handler_input):
        logger.info("Playback finished")
        # Index is now updated in PlaybackStarted instead
        return handler_input.response_builder.response

class PlaybackStoppedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackStopped")(handler_input)
    
    def handle(self, handler_input):
        logger.info("Playback stopped")
        return handler_input.response_builder.response

class PlaybackFailedHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackFailed")(handler_input)
    
    def handle(self, handler_input):
        error = handler_input.request_envelope.request.error
        logger.error(f"Playback failed: {error}")
        
        # Check if it's a service unavailable error
        if error and hasattr(error, 'type') and 'SERVICE_UNAVAILABLE' in str(error.type):
            logger.info("Service unavailable error detected, attempting to skip to next track")
            
            try:
                user_id = get_user_id(handler_input)
                queue_data = get_queue(user_id)
                
                if queue_data and queue_data.get('tracks'):
                    current_index = int(queue_data.get('current_index', 0))
                    tracks = queue_data['tracks']
                    
                    # Try next track
                    next_index = current_index + 1
                    
                    if next_index < len(tracks):
                        logger.info(f"Retrying with next track at index {next_index}")
                        
                        # Update index
                        update_queue_index(user_id, next_index)
                        
                        # Get next track
                        track_info = tracks[next_index]
                        track = get_track_by_key(track_info['key'])
                        
                        if track:
                            audio_url = get_audio_url(track)
                            logger.info(f"Retry: Playing track {track.title}")
                            
                            metadata = AudioItemMetadata(
                                title=track.title,
                                subtitle=track.artist().title if hasattr(track, 'artist') else "Unknown Artist"
                            )
                            
                            stream = Stream(
                                token=str(track.ratingKey),
                                url=audio_url,
                                offset_in_milliseconds=0
                            )
                            
                            audio_item = AudioItem(
                                stream=stream,
                                metadata=metadata
                            )
                            
                            play_directive = PlayDirective(
                                play_behavior=PlayBehavior.REPLACE_ALL,
                                audio_item=audio_item
                            )
                            
                            return handler_input.response_builder.add_directive(play_directive).response
                        else:
                            logger.error("Could not fetch next track for retry")
                    else:
                        logger.info("No more tracks to retry, end of queue")
                else:
                    logger.info("No queue available for retry")
            except Exception as e:
                logger.error(f"Error during playback retry: {e}", exc_info=True)
        
        return handler_input.response_builder.response

class PauseIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.PauseIntent")(handler_input)
    
    def handle(self, handler_input):
        return handler_input.response_builder.add_directive(StopDirective()).response

class ResumeIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ResumeIntent")(handler_input)
    
    def handle(self, handler_input):
        speech_text = "Resume is not yet implemented. Please ask me to play something."
        return handler_input.response_builder.speak(speech_text).response

class ShuffleOnIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ShuffleOnIntent")(handler_input)
    
    def handle(self, handler_input):
        try:
            import random
            user_id = get_user_id(handler_input)
            queue_data = get_queue(user_id)
            
            if not queue_data or not queue_data.get('tracks'):
                speech_text = "There's no queue to shuffle. Please play something first."
                return handler_input.response_builder.speak(speech_text).response
            
            tracks = queue_data['tracks']
            current_index = int(queue_data.get('current_index', 0))
            current_track = tracks[current_index]
            
            # Shuffle the tracks
            random.shuffle(tracks)
            
            # Put current track at the beginning
            tracks = [t for t in tracks if t['key'] != current_track['key']]
            tracks.insert(0, current_track)
            
            # Update queue in DynamoDB
            queue_data['tracks'] = tracks
            queue_data['current_index'] = 0
            queue_data['shuffle'] = True
            
            table.put_item(Item=queue_data)
            
            speech_text = "Shuffle is now on."
            return handler_input.response_builder.speak(speech_text).response
            
        except Exception as e:
            logger.error(f"Error in ShuffleOnIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble turning on shuffle."
            return handler_input.response_builder.speak(speech_text).response

class ShuffleOffIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ShuffleOffIntent")(handler_input)
    
    def handle(self, handler_input):
        try:
            user_id = get_user_id(handler_input)
            queue_data = get_queue(user_id)
            
            if queue_data:
                queue_data['shuffle'] = False
                table.put_item(Item=queue_data)
            
            speech_text = "Shuffle is now off. Note: The current queue order won't change, but new content will play in order."
            return handler_input.response_builder.speak(speech_text).response
            
        except Exception as e:
            logger.error(f"Error in ShuffleOffIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble turning off shuffle."
            return handler_input.response_builder.speak(speech_text).response

class WhatsPlayingIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("WhatsPlayingIntent")(handler_input)
    
    def handle(self, handler_input):
        try:
            user_id = get_user_id(handler_input)
            queue_data = get_queue(user_id)
            
            if not queue_data or not queue_data.get('tracks'):
                speech_text = "Nothing is currently playing."
                return handler_input.response_builder.speak(speech_text).response
            
            current_index = int(queue_data.get('current_index', 0))
            tracks = queue_data['tracks']
            
            if current_index < len(tracks):
                current_track = tracks[current_index]
                track_title = current_track.get('title', 'Unknown')
                artist_name = current_track.get('artist', 'Unknown Artist')
                
                speech_text = f"You're listening to {track_title} by {artist_name}."
                
                return handler_input.response_builder.speak(speech_text).set_card(
                    SimpleCard("Now Playing", f"{track_title}\nby {artist_name}")).response
            else:
                speech_text = "I couldn't determine what's currently playing."
                return handler_input.response_builder.speak(speech_text).response
                
        except Exception as e:
            logger.error(f"Error in WhatsPlayingIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble getting the current track information."
            return handler_input.response_builder.speak(speech_text).response

class RateSongIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        can_handle_result = is_intent_name("RateSongIntent")(handler_input)
        logger.info(f"RateSongIntentHandler.can_handle called: {can_handle_result}")
        return can_handle_result

    def handle(self, handler_input):
        logger.info("=== RateSongIntentHandler.handle called ===")
        try:
            user_id = get_user_id(handler_input)
            logger.info(f"User ID: {user_id}")
            queue_data = get_queue(user_id)
            logger.info(f"Queue data retrieved: {queue_data is not None}")

            if not queue_data or not queue_data.get('tracks'):
                speech_text = "Nothing is currently playing. Please play a song first."
                return handler_input.response_builder.speak(speech_text).response

            # Get the rating from the slot
            slots = handler_input.request_envelope.request.intent.slots
            logger.info(f"Slots: {slots}")
            rating_value = None

            if slots and "rating" in slots:
                slot = slots["rating"]
                logger.info(f"Rating slot found: {slot}")
                if slot and hasattr(slot, 'value') and slot.value:
                    try:
                        rating_value = float(slot.value)
                        logger.info(f"Rating value parsed: {rating_value}")
                    except ValueError:
                        logger.error(f"Could not parse rating value: {slot.value}")
                        speech_text = "I didn't understand the rating. Please say a number from 1 to 5."
                        return handler_input.response_builder.speak(speech_text).response
                else:
                    logger.warning("Rating slot exists but has no value")
            else:
                logger.warning("No rating slot found in request")

            if rating_value is None:
                logger.warning("Rating value is None, returning error")
                speech_text = "Please specify a rating from 1 to 5 stars."
                return handler_input.response_builder.speak(speech_text).response

            # Validate rating is between 1 and 5
            if rating_value < 0 or rating_value > 5:
                speech_text = "Please provide a rating between 1 and 5 stars."
                return handler_input.response_builder.speak(speech_text).response

            # Get the current track
            current_index = int(queue_data.get('current_index', 0))
            tracks = queue_data['tracks']

            if current_index < len(tracks):
                current_track_info = tracks[current_index]
                track_title = current_track_info.get('title', 'Unknown')

                # Fetch the actual track from Plex
                track = get_track_by_key(current_track_info['key'])

                if not track:
                    speech_text = "Sorry, I couldn't access the current track to rate it."
                    return handler_input.response_builder.speak(speech_text).response

                # Convert 1-5 star rating to Plex's 0-10 scale
                plex_rating = rating_value * 2.0

                # Rate the track using Plex API
                try:
                    track.rate(plex_rating)
                    logger.info(f"Rated track '{track_title}' with {rating_value} stars (Plex rating: {plex_rating})")

                    if rating_value == 1:
                        speech_text = f"I've rated {track_title} 1 star."
                    else:
                        speech_text = f"I've rated {track_title} {int(rating_value)} stars."

                    return handler_input.response_builder.speak(speech_text).set_card(
                        SimpleCard("Song Rated", f"{track_title}\nRating: {int(rating_value)} stars")).response
                except Exception as e:
                    logger.error(f"Error rating track: {e}", exc_info=True)
                    speech_text = "Sorry, I had trouble setting the rating in Plex."
                    return handler_input.response_builder.speak(speech_text).response
            else:
                speech_text = "I couldn't determine what's currently playing."
                return handler_input.response_builder.speak(speech_text).response

        except Exception as e:
            logger.error(f"Error in RateSongIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble rating the song."
            return handler_input.response_builder.speak(speech_text).response

class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        try:
            speech_text = "You can ask me to play a song, album, artist, or playlist from your Plex server. For example, say play The Beatles, play the album Abbey Road, or play playlist Favorites. You can also say shuffle, next, previous, pause, or stop to control playback. You can rate the current song by saying rate this song 3 stars."
            return handler_input.response_builder.speak(speech_text).ask(speech_text).response
        except Exception as e:
            logger.error(f"Error in HelpIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble with that."
            return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response

class CancelOrStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        try:
            speech_text = "Goodbye!"
            return handler_input.response_builder.speak(speech_text).add_directive(
                StopDirective()).set_should_end_session(True).response
        except Exception as e:
            logger.error(f"Error in CancelOrStopIntentHandler: {e}", exc_info=True)
            return handler_input.response_builder.speak("Goodbye").set_should_end_session(True).response

class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        try:
            logger.info("Session ended")
            return handler_input.response_builder.response
        except Exception as e:
            logger.error(f"Error in SessionEndedRequestHandler: {e}", exc_info=True)
            return handler_input.response_builder.response

class AllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(f"Unhandled exception: {exception}", exc_info=True)
        
        try:
            speech_text = "Sorry, there was an error. Please try again."
            return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
        except:
            logger.error("Failed to create error response")
            return handler_input.response_builder.speak("Error").response

sb = CustomSkillBuilder()

sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PlayMusicIntentHandler())
sb.add_request_handler(NextIntentHandler())
sb.add_request_handler(PreviousIntentHandler())
sb.add_request_handler(PlaybackStartedHandler())
sb.add_request_handler(PlaybackNearlyFinishedHandler())
sb.add_request_handler(PlaybackFinishedHandler())
sb.add_request_handler(PlaybackStoppedHandler())
sb.add_request_handler(PlaybackFailedHandler())
sb.add_request_handler(PauseIntentHandler())
sb.add_request_handler(ResumeIntentHandler())
sb.add_request_handler(ShuffleOnIntentHandler())
sb.add_request_handler(ShuffleOffIntentHandler())
sb.add_request_handler(WhatsPlayingIntentHandler())
sb.add_request_handler(RateSongIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

sb.add_exception_handler(AllExceptionHandler())

handler = sb.lambda_handler()

def lambda_handler(event, context):
    # Log the incoming request for debugging
    request_type = event.get('request', {}).get('type', 'Unknown')
    logger.info(f"========== NEW REQUEST ==========")
    logger.info(f"Received request type: {request_type}")

    # Log intent name if this is an intent request
    if request_type == 'IntentRequest':
        intent_name = event.get('request', {}).get('intent', {}).get('name', 'Unknown')
        logger.info(f"Intent name: {intent_name}")
        logger.info(f"Intent slots: {event.get('request', {}).get('intent', {}).get('slots', {})}")

    logger.info(f"Full request object: {event.get('request', {})}")
    logger.info(f"=================================")
    return handler(event, context)