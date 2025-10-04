# main handler for the Alexa skill with AudioPlayer support
# lambda_function.py
import logging
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
PLEX_TOKEN = "YOUR_PLEX_TOKEN"  # Replace with your Plex authentication token
PLEX_SERVER_NAME = "YOUR_SERVER_NAME"  # Replace with your exact Plex server name

# Optional: For local network Alexa devices, provide local plex.direct URL for audio streaming
# This won't affect Lambda connection (which is always remote), but provides better audio URLs for local Alexa
LOCAL_PLEX_DIRECT_URL = "https://YOUR-IP-ADDRESS-SEPARATED.YOUR_SUBDOMAIN.plex.direct:8443"  # Your local plex.direct relay URL
USE_LOCAL_AUDIO_URL = True  # Set to True when using Alexa devices on your local network

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize Plex server connection
plex = None
MUSIC = None

try:
    from plexapi.myplex import MyPlexAccount
    from plexapi.server import PlexServer
    
    # Connect through Plex.tv account for secure HTTPS connections
    # Lambda always uses remote connection since it can't access local networks
    logger.info("Connecting to Plex via MyPlexAccount...")
    account = MyPlexAccount(token=PLEX_TOKEN)
    
    # Debug: List all available servers
    logger.info("Available Plex servers:")
    for resource in account.resources():
        logger.info(f"  Server: {resource.name}")
        # Log all available connections
        for conn in resource.connections:
            logger.info(f"    Connection: {conn.uri} (local: {conn.local}, relay: {conn.relay})")
    
    # Get your specific server
    logger.info(f"Attempting to connect to server: {PLEX_SERVER_NAME}")
    server_resource = account.resource(PLEX_SERVER_NAME)
    
    # Lambda must use public/remote connection (not local, not relay)
    direct_connection = None
    for conn in server_resource.connections:
        if not conn.local and not conn.relay:
            direct_connection = conn.uri
            logger.info(f"Found direct public connection: {direct_connection}")
            break
    
    if direct_connection:
        # Connect directly using the public HTTPS URL
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
    """Get the direct streaming URL for a track (not HLS/m3u8)."""
    try:
        # Determine which base URL to use for audio streaming
        # Lambda always connects via remote, but we can provide local URLs to Alexa for better performance
        if USE_LOCAL_AUDIO_URL and LOCAL_PLEX_DIRECT_URL:
            base_url = LOCAL_PLEX_DIRECT_URL
            logger.info(f"Using local plex.direct URL for audio streaming: {base_url}")
        else:
            base_url = plex._baseurl
            logger.info(f"Using remote URL for audio streaming: {base_url}")
        
        # Get the direct media URL
        if track.media and len(track.media) > 0:
            media = track.media[0]
            if media.parts and len(media.parts) > 0:
                part = media.parts[0]
                
                # Check the container/codec
                container = media.container if hasattr(media, 'container') else 'unknown'
                codec = media.audioCodec if hasattr(media, 'audioCodec') else 'unknown'
                bitrate = media.bitrate if hasattr(media, 'bitrate') else 'unknown'
                logger.info(f"Track format - Container: {container}, Codec: {codec}, Bitrate: {bitrate}")
                
                # Use direct file URL - back to what was working
                direct_url = f"{base_url}{part.key}?X-Plex-Token={PLEX_TOKEN}"
                logger.info(f"Direct audio URL: {direct_url}")
                return direct_url
        
        # Fallback to stream URL if direct URL fails
        logger.warning("Could not get direct URL, falling back to stream URL")
        media_url = track.getStreamURL()
        logger.info(f"Fallback stream URL: {media_url}")
        return media_url
        
    except Exception as e:
        logger.error(f"Error getting audio URL: {e}", exc_info=True)
        # Last resort fallback
        return track.getStreamURL()

class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        try:
            # Check Plex connection status and inform user
            if not plex or not MUSIC:
                speech_text = "Warning: I couldn't connect to your Plex server. Please check your configuration and try again later."
                return handler_input.response_builder.speak(speech_text).set_card(
                    SimpleCard("Plex Connection Error", speech_text)).set_should_end_session(True).response
            
            # Connection successful
            speech_text = "Plex Music is ready. You can ask me to play music from your Plex server."
            reprompt_text = "You can say, for example, play music by Queen, or play the album Abbey Road."
            
            return handler_input.response_builder.speak(speech_text).ask(reprompt_text).set_card(
                SimpleCard("Plex Music", speech_text)).response
        except Exception as e:
            logger.error(f"Error in LaunchRequestHandler: {e}", exc_info=True)
            speech_text = "Sorry, there was an error starting the skill."
            return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response

class PlayMusicIntentHandler(AbstractRequestHandler):
    """Handler for Play Music Intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("PlayMusicIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speech_text = "Sorry, something went wrong."
        
        try:
            slots = handler_input.request_envelope.request.intent.slots
            
            # Safer slot value extraction
            artist_name = None
            album_name = None
            track_name = None
            
            # Check each slot carefully
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
            
            logger.info(f"Received request - Artist: {artist_name}, Album: {album_name}, Track: {track_name}")

            if not plex or not MUSIC:
                speech_text = "I couldn't connect to your Plex server. Please check the configuration."
                return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
            
            tracks_to_play = []
            
            if track_name:
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
                    # Get all tracks from the artist (limit to 50 for performance)
                    tracks_to_play = artist.tracks()[:50]
                    speech_text = f"Playing music by {artist.title}."
                else:
                    speech_text = f"I couldn't find the artist {artist_name}."
                    return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
            else:
                speech_text = "You need to specify what to play. For example, play music by The Beatles."
                return handler_input.response_builder.speak(speech_text).ask(speech_text).response

            # Play the first track using AudioPlayer
            if tracks_to_play:
                first_track = tracks_to_play[0]
                audio_url = get_audio_url(first_track)
                
                logger.info(f"Playing track: {first_track.title} from URL: {audio_url}")
                
                # Create metadata
                metadata = AudioItemMetadata(
                    title=first_track.title,
                    subtitle=first_track.artist().title if hasattr(first_track, 'artist') else "Unknown Artist"
                )
                
                # Create stream
                stream = Stream(
                    token=str(first_track.ratingKey),
                    url=audio_url,
                    offset_in_milliseconds=0
                )
                
                # Create audio item
                audio_item = AudioItem(
                    stream=stream,
                    metadata=metadata
                )
                
                # Create play directive
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

class PlaybackStartedHandler(AbstractRequestHandler):
    """Handler for AudioPlayer.PlaybackStarted."""
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackStarted")(handler_input)
    
    def handle(self, handler_input):
        logger.info("Playback started")
        return handler_input.response_builder.response

class PlaybackFinishedHandler(AbstractRequestHandler):
    """Handler for AudioPlayer.PlaybackFinished."""
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackFinished")(handler_input)
    
    def handle(self, handler_input):
        logger.info("Playback finished")
        return handler_input.response_builder.response

class PlaybackStoppedHandler(AbstractRequestHandler):
    """Handler for AudioPlayer.PlaybackStopped."""
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackStopped")(handler_input)
    
    def handle(self, handler_input):
        logger.info("Playback stopped")
        return handler_input.response_builder.response

class PlaybackFailedHandler(AbstractRequestHandler):
    """Handler for AudioPlayer.PlaybackFailed."""
    def can_handle(self, handler_input):
        return is_request_type("AudioPlayer.PlaybackFailed")(handler_input)
    
    def handle(self, handler_input):
        logger.error(f"Playback failed: {handler_input.request_envelope.request.error}")
        return handler_input.response_builder.response

class PauseIntentHandler(AbstractRequestHandler):
    """Handler for AMAZON.PauseIntent."""
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.PauseIntent")(handler_input)
    
    def handle(self, handler_input):
        return handler_input.response_builder.add_directive(StopDirective()).response

class ResumeIntentHandler(AbstractRequestHandler):
    """Handler for AMAZON.ResumeIntent."""
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ResumeIntent")(handler_input)
    
    def handle(self, handler_input):
        # Note: Resume functionality would require storing playback state
        speech_text = "Resume is not yet implemented. Please ask me to play something."
        return handler_input.response_builder.speak(speech_text).response

class HelpIntentHandler(AbstractRequestHandler):
    """Handler for Help Intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        try:
            speech_text = "You can ask me to play a song, album, or artist from your Plex server. For example, say play The Beatles, or play the album Abbey Road. You can also say pause or stop to control playback."
            return handler_input.response_builder.speak(speech_text).ask(speech_text).response
        except Exception as e:
            logger.error(f"Error in HelpIntentHandler: {e}", exc_info=True)
            speech_text = "Sorry, I had trouble with that."
            return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response

class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Handler for Cancel and Stop Intents."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        try:
            speech_text = "Goodbye!"
            return handler_input.response_builder.speak(speech_text).add_directive(
                StopDirective()).set_should_end_session(True).response
        except Exception as e:
            logger.error(f"Error in CancelOrStopIntentHandler: {e}", exc_info=True)
            return handler_input.response_builder.speak("Goodbye").set_should_end_session(True).response

class SessionEndedRequestHandler(AbstractRequestHandler):
    """Handler for Session End."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        try:
            logger.info("Session ended")
            return handler_input.response_builder.response
        except Exception as e:
            logger.error(f"Error in SessionEndedRequestHandler: {e}", exc_info=True)
            return handler_input.response_builder.response

class AllExceptionHandler(AbstractExceptionHandler):
    """Catch all exception handler, log exception and respond with generic message."""
    def can_handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> bool
        return True

    def handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> Response
        logger.error(f"Unhandled exception: {exception}", exc_info=True)
        
        try:
            speech_text = "Sorry, there was an error. Please try again."
            return handler_input.response_builder.speak(speech_text).set_should_end_session(True).response
        except:
            # Last resort - return minimal response
            logger.error("Failed to create error response")
            return handler_input.response_builder.speak("Error").response

# Skill Builder object - Changed to CustomSkillBuilder for AudioPlayer support
sb = CustomSkillBuilder()

# Add all request handlers to the skill builder
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PlayMusicIntentHandler())
sb.add_request_handler(PlaybackStartedHandler())
sb.add_request_handler(PlaybackFinishedHandler())
sb.add_request_handler(PlaybackStoppedHandler())
sb.add_request_handler(PlaybackFailedHandler())
sb.add_request_handler(PauseIntentHandler())
sb.add_request_handler(ResumeIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

# Add exception handler
sb.add_exception_handler(AllExceptionHandler())

# Expose the lambda handler for AWS Lambda to call
handler = sb.lambda_handler()

def lambda_handler(event, context):
    return handler(event, context)

