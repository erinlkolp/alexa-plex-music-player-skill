import logging
import boto3
from decimal import Decimal
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
PLEX_TOKEN = "TOKEN_GOES_HERE"
PLEX_SERVER_NAME = "SERVER_NAME_HERE"

# Automatically use relay URL for local playback
USE_LOCAL_AUDIO_URL = True

# DynamoDB table name
# Create table - primary key "user_id" (string)
DYNAMODB_TABLE_NAME = "PlexAlexaQueue"

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
    
    # Find direct public connection for Lambda
    direct_connection = None
    relay_connection = None
    
    for conn in server_resource.connections:
        if not conn.local and not conn.relay:
            direct_connection = conn.uri
            logger.info(f"Found direct public connection: {direct_connection}")
        elif conn.relay:
            relay_connection = conn.uri
            logger.info(f"Found relay connection: {relay_connection}")
    
    # Store relay URL for local audio playback
    if relay_connection:
        LOCAL_RELAY_URL = relay_connection
        logger.info(f"Using relay URL for local audio: {LOCAL_RELAY_URL}")
    
    if direct_connection:
        plex = PlexServer(direct_connection, PLEX_TOKEN, timeout=15)
        logger.info(f"Successfully connected to: {direct_connection}")
    else:
        logger.error("No direct public connection found, falling back to default")
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

def save_queue(user_id, tracks, current_index=0):
    """Save queue to DynamoDB."""
    try:
        item = {
            'user_id': user_id,
            'tracks': [{'key': int(t.ratingKey), 'title': t.title, 'artist': t.artist().title if hasattr(t, 'artist') else 'Unknown'} for t in tracks],
            'current_index': int(current_index)
        }
        table.put_item(Item=item)
        logger.info(f"Saved queue to DynamoDB for user {user_id}: {len(tracks)} tracks, index {current_index}")
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
                        # Limit playlist to 50 tracks to avoid timeout
                        all_tracks = matching_playlist.items()
                        tracks_to_play = all_tracks[:50]
                        total_tracks = len(all_tracks)
                        
                        if total_tracks > 50:
                            speech_text = f"Playing the first 50 tracks from playlist {matching_playlist.title}, which has {total_tracks} total tracks."
                        else:
                            speech_text = f"Playing playlist {matching_playlist.title}."
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
                    tracks_to_play = [results[0]]
                    speech_text = f"Playing {results[0].title} by {results[0].artist().title}."
                else:
                    speech_text = f"I couldn't find the track {track_name}."
                    return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
                    
            elif album_name:
                results = MUSIC.searchAlbums(title=album_name)
                if results:
                    album = results[0]
                    tracks_to_play = album.tracks()
                    speech_text = f"Playing the album {album.title}."
                else:
                    speech_text = f"I couldn't find the album {album_name}."
                    return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
                    
            elif artist_name:
                results = MUSIC.searchArtists(title=artist_name)
                if results:
                    artist = results[0]
                    tracks_to_play = artist.tracks()[:50]
                    speech_text = f"Playing music by {artist.title}."
                else:
                    speech_text = f"I couldn't find the artist {artist_name}."
                    return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
            else:
                speech_text = "You need to specify what to play. For example, play music by The Beatles, or play playlist Favorites."
                return handler_input.response_builder.speak(speech_text).ask(speech_text).response

            # Save the queue to DynamoDB for next/previous navigation
            save_queue(user_id, tracks_to_play, 0)

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
            
            # Update index in DynamoDB
            update_queue_index(user_id, next_index)
            
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
            
            # Update index in DynamoDB
            update_queue_index(user_id, prev_index)
            
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
            
            # Update index in DynamoDB
            update_queue_index(user_id, next_index)
            
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
        logger.error(f"Playback failed: {handler_input.request_envelope.request.error}")
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

class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        try:
            speech_text = "You can ask me to play a song, album, artist, or playlist from your Plex server. For example, say play The Beatles, play the album Abbey Road, or play playlist Favorites. You can also say next, previous, pause, or stop to control playback."
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
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

sb.add_exception_handler(AllExceptionHandler())

handler = sb.lambda_handler()

def lambda_handler(event, context):
    return handler(event, context)