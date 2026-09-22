#!/usr/bin/python

""" YouTube upload for IMN bolide videos (YouTube Data API v3).

    Credentials live OUTSIDE this repo and are never committed. Default
    location: ~/.config/IMN/youtube.json  (override with $IMN_YOUTUBE_CONFIG):

        {
          "client_id":     "....apps.googleusercontent.com",
          "client_secret": "....",
          "refresh_token": "....",
          "privacy":       "unlisted",    # optional; unlisted (default) | public | private
          "playlist_title": "IMN/{station}/{month}",       # optional; see below
          "trackstack_playlist_title": "IMN/{station}/{shower}-{year}",
          "playlist_privacy": "public",   # optional; visibility of created playlists
          "common_playlist_id": "PL....", # optional; every upload also goes here
          "playlist_id":   "PL....",      # optional; one fixed playlist, overrides per-station
          "channel_id":    "UC....",      # written by this module: whose playlists these are
          "playlists":     {}             # written by this module: title -> playlist id
        }

    Comments above are for readability only -- JSON does not allow them.

    YouTube has no folder hierarchy: playlists are flat and cannot nest, so the
    "IMN/<station>" path lives in the playlist title. To group them into a shelf
    on the channel page, use Studio -> Customization -> Layout.

    A single playlist per station grows without bound, so the title is a template
    split by period. It is formatted against {station} plus whatever keys the
    caller supplies -- {night}, {month}, {year}, and {shower} for track stacks.
    Periods come from the capture night, never from today: the nightly hook runs
    after dawn, so a night captured on the 31st is published on the 1st.

    Templates are per upload kind: a "<kind>_playlist_title" is used if present,
    otherwise "playlist_title". str.format ignores keys a template does not
    mention, so the old flat "IMN/{station}" keeps resolving to exactly the
    playlist it always did -- splitting is opt-in, one config key per station.
    Month keys are zero-padded so titles sort lexicographically, which is the
    only ordering the channel page offers.

    One-time setup to mint the refresh_token from an OAuth client secret:
        1. Create an OAuth 2.0 "Desktop app" client in Google Cloud, download
           it to ~/.config/IMN/client_secret.json
        2. Run (on a machine with a browser):
               python youtube.py authorize
           This writes ~/.config/IMN/youtube.json with the refresh token.
    Then copy youtube.json to the station.

    Requires (in the ~/vRMS venv): google-api-python-client, google-auth,
    google-auth-oauthlib  (see requirements-bolides.txt).
"""

import os
import json
import logging

log = logging.getLogger("IMN.youtube")

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# The pre-split title. Kept as the default so a station whose youtube.json has no
# "playlist_title" behaves exactly as it did before periods existed.
DEFAULT_PLAYLIST_TITLE = "IMN/{station}"

_CONFIG_DIR = os.path.expanduser("~/.config/IMN")
_CONFIG_PATH = os.environ.get("IMN_YOUTUBE_CONFIG", os.path.join(_CONFIG_DIR, "youtube.json"))
_CLIENT_SECRET_PATH = os.path.join(_CONFIG_DIR, "client_secret.json")


def _load_config():
    """ Return the youtube.json dict, or None if it is absent or unreadable.
        A malformed config must not raise: the nightly flow treats None as
        "skip uploading". """
    if not os.path.isfile(_CONFIG_PATH):
        return None
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except ValueError as e:
        log.error("YouTube config %s is not valid JSON: %s", _CONFIG_PATH, e)
        return None


def _save_config(cfg):
    """ Rewrite youtube.json (0600, atomically). Best effort: a station with a
        read-only config dir just loses the playlist-id cache. """
    tmp = _CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.chmod(tmp, 0o600)
        os.rename(tmp, _CONFIG_PATH)
    except (IOError, OSError) as e:
        log.warning("could not update %s: %r", _CONFIG_PATH, e)
        try:
            os.remove(tmp)
        except OSError:
            pass


class Uploader(object):
    """ Thin wrapper over the YouTube Data API v3 for uploading a video and
        (optionally) adding it to a playlist. """

    def __init__(self, cfg):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        self.cfg = cfg
        self.playlist_id = cfg.get("playlist_id")
        self.common_playlist_id = cfg.get("common_playlist_id")
        self.playlist_title = cfg.get("playlist_title", DEFAULT_PLAYLIST_TITLE)
        self.playlist_privacy = cfg.get("playlist_privacy", "public")
        self.privacy = cfg.get("privacy", "unlisted")

        creds = Credentials(
            token=None,
            refresh_token=cfg["refresh_token"],
            token_uri=TOKEN_URI,
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            scopes=SCOPES,
        )
        # cache_discovery=False avoids a file-cache warning on headless installs.
        self.youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    def upload_video(self, path, title, description, tags=None, station=None,
                     kind=None, playlist_keys=None):
        """ Upload one video file, add it to the station's playlist, and return
            its YouTube video id. """
        video_id, _items = self.publish_video(path, title, description, tags,
                                              station, kind, playlist_keys)
        return video_id

    def publish_video(self, path, title, description, tags=None, station=None,
                      kind=None, playlist_keys=None):
        """ Upload one video file and return (video_id, playlist_item_ids).

            The item ids matter to anything that later retires the video: the
            API has no way to find the playlist entry for a video short of
            listing or searching, and search.list costs 100 units against a
            100/day cap. Callers that mean to retire what they upload must
            persist these; upload_video() above is the shorthand for callers
            that never will.

            kind selects the playlist title template ("<kind>_playlist_title",
            falling back to "playlist_title"), and playlist_keys are the period
            fields that template may use. See the module docstring.
        """
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {
                "title": title[:100],            # YouTube title hard limit
                "description": description,
                "tags": tags or ["meteor", "bolide", "fireball", "IMN"],
                "categoryId": "28",              # Science & Technology
            },
            "status": {
                "privacyStatus": self.privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = self.youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _status, response = request.next_chunk()
        video_id = response["id"]

        item_ids = []
        for playlist_id in self._resolve_playlists(station, kind, playlist_keys):
            item_id = self._add_to_playlist(playlist_id, video_id)
            if item_id:
                item_ids.append(item_id)

        return video_id, item_ids

    def _resolve_playlists(self, station, kind=None, playlist_keys=None):
        """ Return every playlist id this video belongs in: the station's own,
            plus the channel-wide "common_playlist_id" if one is configured.
            Playlist membership is by reference, so a video sits in both at no
            cost beyond one playlistItems.insert each. """
        ids = []
        station_playlist = self._resolve_station_playlist(station, kind, playlist_keys)
        if station_playlist:
            ids.append(station_playlist)
        if self.common_playlist_id and self.common_playlist_id not in ids:
            ids.append(self.common_playlist_id)
        return ids

    def _playlist_title_for(self, kind):
        """ The title template for this upload kind, falling back to the shared
            one. A per-kind key exists because the natural period differs: a
            bolide belongs to a month, a track stack to a shower and a year. """
        if kind:
            template = self.cfg.get("{}_playlist_title".format(kind))
            if template:
                return template
        return self.playlist_title

    def _resolve_station_playlist(self, station, kind=None, playlist_keys=None):
        """ Return the playlist id for this station and period, or None. A fixed
            "playlist_id" in the config wins; otherwise the playlist named by the
            formatted title is looked up, created if missing, and its id cached
            in the config so later runs cost one quota unit. """
        if self.playlist_id:
            return self.playlist_id
        if not station:
            return None

        template = self._playlist_title_for(kind)
        fields = dict(playlist_keys or {}, station=station)
        try:
            title = template.format(**fields)
        except (KeyError, IndexError) as e:
            # A template naming a key this caller does not supply is a config
            # typo, and one must not cost the night's upload its playlist.
            log.error("playlist title %r wants %s, which %s uploads do not supply; "
                      "falling back to %r", template, e, kind or "these",
                      DEFAULT_PLAYLIST_TITLE)
            title = DEFAULT_PLAYLIST_TITLE.format(station=station)

        cache = self.cfg.setdefault("playlists", {})
        if title in cache:
            return cache[title]

        try:
            playlist_id = self._find_playlist(title) or self._create_playlist(title, station)
        except Exception as e:
            log.error("could not resolve playlist %r: %r", title, e)
            return None

        cache[title] = playlist_id
        _save_config(self.cfg)
        return playlist_id

    def _find_playlist(self, title):
        """ Return the id of this channel's playlist with that exact title.

            Every title seen on the way is cached, not just the one asked for.
            Splitting by period means the channel accumulates hundreds of
            playlists, and paging all of them to answer one miss and then doing
            it again for the next miss is the slow part of a cold start. One
            scan now resolves every later lookup for free; the cost is identical.
        """
        cache = self.cfg.setdefault("playlists", {})
        found = None

        request = self.youtube.playlists().list(part="snippet", mine=True, maxResults=50)
        while request is not None:
            response = request.execute()
            for item in response.get("items", []):
                cache.setdefault(item["snippet"]["title"], item["id"])
                if item["snippet"]["title"] == title:
                    found = item["id"]
            request = self.youtube.playlists().list_next(request, response)

        _save_config(self.cfg)
        return found

    def _create_playlist(self, title, station):
        response = self.youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": "Captured by Israeli Meteor Network "
                                   "station {}.".format(station),
                },
                "status": {"privacyStatus": self.playlist_privacy},
            },
        ).execute()
        log.info("created playlist %r (%s)", title, response["id"])
        return response["id"]

    def _add_to_playlist(self, playlist_id, video_id):
        """ Add a video to a playlist and return the playlist ITEM id, which is
            what a later removal needs -- it identifies the entry, not the
            video. None if the add failed. """
        try:
            response = self.youtube.playlistItems().insert(
                part="snippet",
                body={"snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }},
            ).execute()
            return response["id"]
        except Exception as e:
            log.error("could not add %s to playlist %s: %r", video_id, playlist_id, e)
            return None

    def retire_video(self, video_id, playlist_item_ids=()):
        """ Remove a video from its playlists and delete it.

            Best effort throughout: something already gone -- deleted by hand,
            or left behind by a half-failed earlier run -- must not abort the
            caller. Losing track of one orphan beats wedging a daily rebuild.
            Returns True only if the video itself is now gone.
        """
        for item_id in playlist_item_ids:
            try:
                self.youtube.playlistItems().delete(id=item_id).execute()
            except Exception as e:
                log.warning("could not remove playlist item %s: %r", item_id, e)

        try:
            self.youtube.videos().delete(id=video_id).execute()
            log.info("retired video %s", video_id)
            return True
        except Exception as e:
            log.error("could not delete video %s: %r", video_id, e)
            return False


def get_uploader():
    """ Build an Uploader from the on-disk config, or return None if the
        credentials are not present or the Google libraries are unavailable.
        Callers treat None as "skip uploading". """
    cfg = _load_config()
    if cfg is None:
        log.info("no YouTube config at %s", _CONFIG_PATH)
        return None

    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not cfg.get(k)]
    if missing:
        log.error("YouTube config %s missing keys: %s", _CONFIG_PATH, ", ".join(missing))
        return None

    try:
        return Uploader(cfg)
    except ImportError as e:
        log.error("Google API libraries not installed (%r); see requirements-bolides.txt", e)
        return None


def authorize():
    """ One-time interactive OAuth flow: read client_secret.json, obtain a
        refresh token, and write it into youtube.json. Run on a machine with a
        browser, then copy youtube.json to the station. """
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.isfile(_CLIENT_SECRET_PATH):
        raise SystemExit("Place your OAuth client secret at {}".format(_CLIENT_SECRET_PATH))

    flow = InstalledAppFlow.from_client_secrets_file(_CLIENT_SECRET_PATH, SCOPES)
    # There is no headless fallback: Google blocked the out-of-band
    # (copy-the-code) flow in 2022 and google-auth-oauthlib dropped
    # run_console() in 1.0.0. Run this where a browser is available and copy
    # the resulting youtube.json to the station.
    #
    # prompt="consent" is deliberate. Google issues a refresh token only on the
    # first consent for an app/account pair, and otherwise skips the chooser
    # and reuses the remembered grant -- which would both strand us without a
    # refresh token and silently re-authorize the wrong channel. Pick the IMN
    # brand channel on the "Choose a channel" screen.
    try:
        creds = flow.run_local_server(port=0, prompt="consent")
    except Warning as e:
        # oauthlib raises a bare Warning when Google returns fewer scopes than
        # were asked for -- i.e. a consent checkbox was left unticked. Uploading
        # needs youtube.upload; the playlists need the broader youtube scope.
        raise SystemExit(
            "{}\n\nTick *both* permission checkboxes on the consent screen "
            "(the second one covers playlist management) and retry.".format(e))

    if not creds.refresh_token:
        raise SystemExit(
            "Google returned no refresh token; {} left unchanged. Revoke this "
            "app at https://myaccount.google.com/permissions and retry."
            .format(_CONFIG_PATH))

    existing = _load_config() or {}
    existing.update({
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    })
    existing.setdefault("privacy", "unlisted")

    # Playlist ids belong to whichever channel was authorized, so a re-auth that
    # picked a different one must not carry the cache over. Ask which channel
    # this is (1 unit, and only here in the one-time flow) rather than dropping
    # the cache unconditionally: re-consenting to the SAME channel is the common
    # case, and with playlists split by period there are hundreds of ids in
    # there that would otherwise cost a full cold scan to rediscover.
    channel_id = None
    try:
        from googleapiclient.discovery import build
        channels = build("youtube", "v3", credentials=creds, cache_discovery=False) \
            .channels().list(part="id", mine=True).execute()
        items = channels.get("items", [])
        if items:
            channel_id = items[0]["id"]
    except Exception as e:
        log.warning("could not identify the authorized channel (%r)", e)

    if channel_id is None or existing.get("channel_id") != channel_id:
        existing.pop("playlists", None)
    if channel_id:
        existing["channel_id"] = channel_id

    if not os.path.isdir(_CONFIG_DIR):
        os.makedirs(_CONFIG_DIR)
    with open(_CONFIG_PATH, "w") as f:
        json.dump(existing, f, indent=2)
    os.chmod(_CONFIG_PATH, 0o600)
    print("Wrote {} (add a 'playlist_id' to publish into a playlist).".format(_CONFIG_PATH))


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "authorize":
        authorize()
    else:
        raise SystemExit("usage: python youtube.py authorize")
