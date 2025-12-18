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
    MediaClass,
    MediaType,
    RepeatMode,
)
from homeassistant.const import (
    CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT,
    STATE_OFF,
    STATE_ON,
    STATE_PLAYING,
    STATE_PAUSED,
    STATE_UNAVAILABLE
)

# 适配 2025.12 版本的媒体类定义
MEDIA_CLASS_ALBUM = MediaClass.ALBUM
MEDIA_CLASS_ARTIST = MediaClass.ARTIST
MEDIA_CLASS_CHANNEL = MediaClass.CHANNEL
MEDIA_CLASS_DIRECTORY = MediaClass.DIRECTORY
MEDIA_CLASS_EPISODE = MediaClass.EPISODE
MEDIA_CLASS_MOVIE = MediaClass.MOVIE
MEDIA_CLASS_MUSIC = MediaClass.MUSIC
MEDIA_CLASS_PLAYLIST = MediaClass.PLAYLIST
MEDIA_CLASS_SEASON = MediaClass.SEASON
MEDIA_CLASS_TRACK = MediaClass.TRACK
MEDIA_CLASS_TV_SHOW = MediaClass.TV_SHOW

# 修复 ImportError: 从 MediaType 枚举获取常量
MEDIA_TYPE_ALBUM = MediaType.ALBUM
MEDIA_TYPE_ARTIST = MediaType.ARTIST
MEDIA_TYPE_CHANNEL = MediaType.CHANNEL
MEDIA_TYPE_EPISODE = MediaType.EPISODE
MEDIA_TYPE_MUSIC = MediaType.MUSIC
MEDIA_TYPE_MOVIE = MediaType.MOVIE
MEDIA_TYPE_PLAYLIST = MediaType.PLAYLIST
MEDIA_TYPE_SEASON = MediaType.SEASON
MEDIA_TYPE_TRACK = MediaType.TRACK
MEDIA_TYPE_TVSHOW = MediaType.TVSHOW

# 修复重复模式常量
REPEAT_MODE_ALL = RepeatMode.ALL
REPEAT_MODE_OFF = RepeatMode.OFF
REPEAT_MODE_ONE = RepeatMode.ONE
REPEAT_MODES = [RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE]

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
        # 2025.12 推荐直接使用枚举对象
        self._attr_device_class = MediaPlayerDeviceClass.TV

        # MPD client
        self._client = MPDClient()
        self._client.timeout = 30
        self._client.idletimeout = None

        self.playlist = []
        self.playindex = 0
        self.is_tts = False
        self._attr_unique_id = f"mpd_{server}_{port}"
        self._attributes = {
            'platform': 'cloud_music'
        }

    @property
    def device_info(self):
        return {
            'identifiers': {
                (manifest.domain, self._attr_unique_id)
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
        except (mpd.ConnectionError, OSError):
            return

        self._is_connected = True

    def _disconnect(self):
        """Disconnect from MPD."""
        with suppress(mpd.ConnectionError, OSError):
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

        if position is not None:
            try:
                float_pos = int(float(position))
                if self._media_position != float_pos:
                    self._media_position_updated_at = dt_util.utcnow()
                    self._media_position = float_pos
            except ValueError:
                pass

        # cloud_music metadata
        file = self._currentsong.get('file')
        if file is not None:
            arr = list(filter(lambda x: x.url == file, self.playlist))
            if len(arr) > 0:
                music_info = arr[0]
                self._attr_media_image_url = music_info.thumbnail
                self._attr_media_title = music_info.song
                self._attr_app_name = music_info.singer
                self._attr_media_artist = music_info.singer
                if self.is_tts:
                    await self._client.pause(0)
                    with suppress(Exception):
                        await self._client.delete(len(self.playlist))
                    self.is_tts = False

    @property
    def available(self):
        return self._is_connected

    async def async_update(self) -> None:
        try:
            if not self._is_connected:
                await self._connect()
                if self._is_connected:
                    self._commands = list(await self._client.commands())
            
            if self._is_connected:
                await self._fetch_status()
        except (mpd.ConnectionError, OSError, ValueError) as error:
            _LOGGER.debug("Error updating status: %s", error)
            self._disconnect()

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        if self._status is None:
            return STATE_OFF
        state = self._status.get("state")
        if state == "play":
            return STATE_PLAYING
        if state == "pause":
            return STATE_PAUSED
        return STATE_OFF

    @property
    def is_volume_muted(self):
        return self._muted

    @property
    def media_content_id(self):
        return self._currentsong.get("file")

    @property
    def media_duration(self):
        if currentsong_time := self._currentsong.get("time"):
            return currentsong_time

        time_from_status = self._status.get("time")
        if isinstance(time_from_status, str) and ":" in time_from_status:
            return time_from_status.split(":")[1]

        return None

    @property
    def media_position(self):
        return self._media_position

    @property
    def media_position_updated_at(self):
        return self._media_position_updated_at

    @property
    def media_album_name(self):
        return self._currentsong.get("album")

    @property
    def volume_level(self):
        if self._status and "volume" in self._status:
            return int(self._status["volume"]) / 100
        return None

    @property
    def supported_features(self):
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
        return self._currentplaylist

    @property
    def source_list(self):
        return self._playlists

    async def async_select_source(self, source: str) -> None:
        await self.async_play_media(MEDIA_TYPE_PLAYLIST, source)

    async def async_set_volume_level(self, volume: float) -> None:
        if self._status and "volume" in self._status:
            await self._client.setvol(int(volume * 100))

    async def async_volume_up(self) -> None:
        if self._status and "volume" in self._status:
            current_volume = int(self._status["volume"])
            if current_volume <= 100:
                await self._client.setvol(min(current_volume + 5, 100))

    async def async_volume_down(self) -> None:
        if self._status and "volume" in self._status:
            current_volume = int(self._status["volume"])
            if current_volume >= 0:
                await self._client.setvol(max(current_volume - 5, 0))

    async def async_media_play(self) -> None:
        if self._status and self._status.get("state") == "pause":
            await self._client.pause(0)
        else:
            await self._client.play()

    async def async_media_pause(self) -> None:
        await self._client.pause(1)

    async def async_media_stop(self) -> None:
        await self._client.stop()

    async def async_media_next_track(self) -> None:
        await self._client.next()

    async def async_media_previous_track(self) -> None:
        await self._client.previous()

    async def async_mute_volume(self, mute: bool) -> None:
        if self._status and "volume" in self._status:
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
                    await self._client.play(self.playindex)
                elif isinstance(result, str) and result.startswith('http'):
                    playindex = len(self.playlist)
                    await self._client.add(result)
                    await self._client.play(playindex)
                    self.is_tts = True
                else:
                    await self._client.clear()
                    await self.playlist_add(0)
                    await self._client.play(self.playindex)

    async def playlist_add(self, index):
        if index < len(self.playlist):
            music_info = self.playlist[index]
            await self._client.add(music_info.url)
            await self.playlist_add(index + 1)

    @property
    def repeat(self):
        if not self._status:
            return REPEAT_MODE_OFF
        if self._status.get("repeat") == "1":
            if self._status.get("single") == "1":
                return REPEAT_MODE_ONE
            return REPEAT_MODE_ALL
        return REPEAT_MODE_OFF

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
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
        return self._status.get("random") == "1" if self._status else False

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._client.random(int(shuffle))

    async def async_turn_off(self) -> None:
        await self._client.stop()

    async def async_turn_on(self) -> None:
        await self._client.play()

    async def async_clear_playlist(self) -> None:
        await self._client.clear()

    async def async_media_seek(self, position: float) -> None:
        await self._client.seekcur(int(position))

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        cloud_music = self.hass.data.get('cloud_music')
        if cloud_music is not None:
            return await cloud_music.async_browse_media(self, media_content_type, media_content_id)
        return await super().async_browse_media(media_content_type, media_content_id)
