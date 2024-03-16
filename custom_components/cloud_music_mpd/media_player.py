"""Support to interact with a Music Player Daemon."""
from __future__ import annotations

from contextlib import suppress
from datetime import timedelta
import logging
from typing import Any

import mpd
from mpd.asyncio import MPDClient
import voluptuous as vol

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerDeviceClass,
)
from homeassistant.const import (
    CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT,    
    STATE_OFF, 
    STATE_ON, 
    STATE_PLAYING, 
    STATE_PAUSED,
    STATE_UNAVAILABLE
)
from homeassistant.components.media_player.const import (
    MEDIA_CLASS_ALBUM,
    MEDIA_CLASS_ARTIST,
    MEDIA_CLASS_CHANNEL,
    MEDIA_CLASS_DIRECTORY,
    MEDIA_CLASS_EPISODE,
    MEDIA_CLASS_MOVIE,
    MEDIA_CLASS_MUSIC,
    MEDIA_CLASS_PLAYLIST,
    MEDIA_CLASS_SEASON,
    MEDIA_CLASS_TRACK,
    MEDIA_CLASS_TV_SHOW,
    MEDIA_TYPE_ALBUM,
    MEDIA_TYPE_ARTIST,
    MEDIA_TYPE_CHANNEL,
    MEDIA_TYPE_EPISODE,
    MEDIA_TYPE_MUSIC,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_PLAYLIST,
    MEDIA_TYPE_SEASON,
    MEDIA_TYPE_TRACK,
    MEDIA_TYPE_TVSHOW,
    REPEAT_MODE_ALL,
    REPEAT_MODE_OFF,
    REPEAT_MODE_ONE,
    REPEAT_MODES
)
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import Throttle
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

from .manifest import manifest

PLAYLIST_UPDATE_INTERVAL = timedelta(seconds=120)

SUPPORT_MPD = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.CLEAR_PLAYLIST
    | MediaPlayerEntityFeature.REPEAT_SET
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.BROWSE_MEDIA
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    config = entry.data

    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)
    name = config.get(CONF_NAME)
    password = config.get(CONF_PASSWORD)

    entity = MpdDevice(host, port, password, name)
    async_add_entities([entity], True)


class MpdDevice(MediaPlayerEntity):
    """Representation of a MPD server."""

    _attr_media_content_type = MEDIA_TYPE_MUSIC

    # pylint: disable=no-member
    def __init__(self, server, port, password, name):
        """Initialize the MPD device."""
        self.server = server
        self.port = port
        self._name = name
        self.password = password

        self._status = None
        self._currentsong = None
        self._playlists = None
        self._currentplaylist = None
        self._is_connected = False
        self._muted = False
        self._muted_volume = None
        self._media_position_updated_at = None
        self._media_position = None
        
        # Track if the song changed so image doesn't have to be loaded every update.
        self._media_image_file = None
        self._commands = None

        self._attr_media_image_remotely_accessible = True
        self._attr_device_class = MediaPlayerDeviceClass.TV.value

        # set up MPD client
        self._client = MPDClient()
        self._client.timeout = 30
        self._client.idletimeout = None

        self.playlist = []
        self.playindex = 0
        self._attr_unique_id = server
        self._attributes = {
            'platform': 'cloud_music'
        }

    @property
    def device_info(self):
        return {
            'identifiers': {
                (manifest.domain, manifest.documentation)
            },
            'name': self.name,
            'manufacturer': 'shaonianzhentan',
            'model': 'CloudMusic',
            'sw_version': manifest.version
        }

    @property
    def extra_state_attributes(self):
        return self._attributes

    async def _connect(self):
        """Connect to MPD."""
        try:
            await self._client.connect(self.server, self.port)

            if self.password != '':
                await self._client.password(self.password)
        except mpd.ConnectionError:
            return

        self._is_connected = True

    def _disconnect(self):
        """Disconnect from MPD."""
        with suppress(mpd.ConnectionError):
            self._client.disconnect()
        self._is_connected = False
        self._status = None

    async def _fetch_status(self):
        """Fetch status from MPD."""
        self._status = await self._client.status()
        self._currentsong = await self._client.currentsong()

        if (position := self._status.get("elapsed")) is None:
            position = self._status.get("time")

            if isinstance(position, str) and ":" in position:
                position = position.split(":")[0]

        if position is not None and self._media_position != position:
            self._media_position_updated_at = dt_util.utcnow()
            self._media_position = int(float(position))

        # 更新信息
        file = self._currentsong.get('file')
        if file is not None:
            arr = list(filter(lambda x:x.url == file, self.playlist))
            if len(arr) > 0:
                music_info = arr[0]
                self._attr_media_image_url = music_info.thumbnail
                self._attr_media_title = music_info.song
                self._attr_app_name = music_info.singer
                self._attr_media_artist = music_info.singer

    @property
    def available(self):
        """Return true if MPD is available and connected."""
        return self._is_connected

    async def async_update(self) -> None:
        """Get the latest data and update the state."""
        try:
            if not self._is_connected:
                await self._connect()
                self._commands = list(await self._client.commands())

            await self._fetch_status()
        except (mpd.ConnectionError, OSError, ValueError) as error:
            # Cleanly disconnect in case connection is not in valid state
            _LOGGER.debug("Error updating status: %s", error)
            self._disconnect()

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the media state."""
        if self._status is None:
            return STATE_OFF
        if self._status["state"] == "play":
            return STATE_PLAYING
        if self._status["state"] == "pause":
            return STATE_PAUSED
        if self._status["state"] == "stop":
            return STATE_OFF

        return STATE_OFF

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def media_content_id(self):
        """Return the content ID of current playing media."""
        return self._currentsong.get("file")

    @property
    def media_duration(self):
        """Return the duration of current playing media in seconds."""
        if currentsong_time := self._currentsong.get("time"):
            return currentsong_time

        time_from_status = self._status.get("time")
        if isinstance(time_from_status, str) and ":" in time_from_status:
            return time_from_status.split(":")[1]

        return None

    @property
    def media_position(self):
        """Position of current playing media in seconds.
        This is returned as part of the mpd status rather than in the details
        of the current song.
        """
        return self._media_position

    @property
    def media_position_updated_at(self):
        """Last valid time of media position."""
        return self._media_position_updated_at

    @property
    def media_album_name(self):
        """Return the album of current playing media (Music track only)."""
        return self._currentsong.get("album")

    @property
    def volume_level(self):
        """Return the volume level."""
        if "volume" in self._status:
            return int(self._status["volume"]) / 100
        return None

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        if self._status is None:
            return 0

        supported = SUPPORT_MPD
        if "volume" in self._status:
            supported |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
                | MediaPlayerEntityFeature.VOLUME_MUTE
            )
        if self._playlists is not None:
            supported |= MediaPlayerEntityFeature.SELECT_SOURCE

        return supported

    @property
    def source(self):
        """Name of the current input source."""
        return self._currentplaylist

    @property
    def source_list(self):
        """Return the list of available input sources."""
        return self._playlists

    async def async_select_source(self, source: str) -> None:
        """Choose a different available playlist and play it."""
        await self.async_play_media(MEDIA_TYPE_PLAYLIST, source)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume of media player."""
        if "volume" in self._status:
            await self._client.setvol(int(volume * 100))

    async def async_volume_up(self) -> None:
        """Service to send the MPD the command for volume up."""
        if "volume" in self._status:
            current_volume = int(self._status["volume"])

            if current_volume <= 100:
                self._client.setvol(current_volume + 5)

    async def async_volume_down(self) -> None:
        """Service to send the MPD the command for volume down."""
        if "volume" in self._status:
            current_volume = int(self._status["volume"])

            if current_volume >= 0:
                await self._client.setvol(current_volume - 5)

    async def async_media_play(self) -> None:
        """Service to send the MPD the command for play/pause."""
        if self._status["state"] == "pause":
            await self._client.pause(0)
        else:
            await self._client.play()

    async def async_media_pause(self) -> None:
        """Service to send the MPD the command for play/pause."""
        await self._client.pause(1)

    async def async_media_stop(self) -> None:
        """Service to send the MPD the command for stop."""
        await self._client.stop()

    async def async_media_next_track(self) -> None:
        """Service to send the MPD the command for next track."""
        await self._client.next()

    async def async_media_previous_track(self) -> None:
        """Service to send the MPD the command for previous track."""
        await self._client.previous()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute. Emulated with set_volume_level."""
        if "volume" in self._status:
            if mute:
                self._muted_volume = self.volume_level
                await self.async_set_volume_level(0)
            elif self._muted_volume is not None:
                await self.async_set_volume_level(self._muted_volume)
            self._muted = mute

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        cloud_music = self.hass.data.get('cloud_music')
        if cloud_music is not None:
            result = await cloud_music.async_play_media(self, cloud_music, media_id)
            if result is not None:
                if result == 'index':
                    # 播放当前列表指定项
                    await self._client.play(self.playindex)
                elif result.startswith('http'):
                    # HTTP播放链接
                    pass
                else:
                    # 添加播放列表到播放器
                    await self._client.clear()
                    await self.playlist_add(0)
                    await self._client.play(self.playindex)
    
    async def playlist_add(self, index):
        if index < len(self.playlist):
            music_info = self.playlist[index]
            # print(music_info.url)
            await self._client.add(music_info.url)
            await self.playlist_add(index + 1)
        else:
            # 索引超出了播放列表的长度，停止递归
            return

    @property
    def repeat(self):
        """Return current repeat mode."""
        if self._status["repeat"] == "1":
            if self._status["single"] == "1":
                return REPEAT_MODE_ONE
            return REPEAT_MODE_ALL
        return REPEAT_MODE_OFF

    async def async_set_repeat(self, repeat) -> None:
        """Set repeat mode."""
        if repeat == REPEAT_MODE_OFF:
            await self._client.repeat(0)
            await self._client.single(0)
        else:
            await self._client.repeat(1)
            if repeat == REPEAT_MODE_ONE:
                await self._client.single(1)
            else:
                await self._client.single(0)

    @property
    def shuffle(self):
        """Boolean if shuffle is enabled."""
        return bool(int(self._status["random"]))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Enable/disable shuffle mode."""
        await self._client.random(int(shuffle))

    async def async_turn_off(self) -> None:
        """Service to send the MPD the command to stop playing."""
        await self._client.stop()

    async def async_turn_on(self) -> None:
        """Service to send the MPD the command to start playing."""
        await self._client.play()

    async def async_clear_playlist(self) -> None:
        """Clear players playlist."""
        await self._client.clear()

    async def async_media_seek(self, position: float) -> None:
        """Send seek command."""
        await self._client.seekcur(position)

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        """Implement the websocket media browsing helper."""
        
        cloud_music = self.hass.data.get('cloud_music')
        if cloud_music is not None:
            return await cloud_music.async_browse_media(self, media_content_type, media_content_id)
