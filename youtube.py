#!/usr/bin/python

""" YouTube upload for IMN bolide videos (YouTube Data API v3).

    Credentials live OUTSIDE this repo and are never committed. Default
    location: ~/.config/IMN/youtube.json  (override with $IMN_YOUTUBE_CONFIG):

        {
          "client_id":     "....apps.googleusercontent.com",
          "client_secret": "....",
          "refresh_token": "....",
          "playlist_id":   "PL....",      # optional; add uploads to this playlist
          "privacy":       "unlisted"     # optional; unlisted (default) | public | private
        }

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

_CONFIG_DIR = os.path.expanduser("~/.config/IMN")
_CONFIG_PATH = os.environ.get("IMN_YOUTUBE_CONFIG", os.path.join(_CONFIG_DIR, "youtube.json"))
_CLIENT_SECRET_PATH = os.path.join(_CONFIG_DIR, "client_secret.json")


def _load_config():
    """ Return the youtube.json dict, or None if it is absent. """
    if not os.path.isfile(_CONFIG_PATH):
        return None
    with open(_CONFIG_PATH) as f:
        return json.load(f)


class Uploader(object):
    """ Thin wrapper over the YouTube Data API v3 for uploading a video and
        (optionally) adding it to a playlist. """

    def __init__(self, cfg):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        self.playlist_id = cfg.get("playlist_id")
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

    def upload_video(self, path, title, description, tags=None):
        """ Upload one video file, add it to the playlist if configured, and
            return its YouTube video id. """
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

        if self.playlist_id:
            self._add_to_playlist(video_id)

        return video_id

    def _add_to_playlist(self, video_id):
        try:
            self.youtube.playlistItems().insert(
                part="snippet",
                body={"snippet": {
                    "playlistId": self.playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }},
            ).execute()
        except Exception as e:
            log.error("could not add %s to playlist %s: %r", video_id, self.playlist_id, e)


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
    try:
        creds = flow.run_local_server(port=0)
    except Exception:
        # Headless fallback (no local browser/redirect available).
        creds = flow.run_console()

    existing = _load_config() or {}
    existing.update({
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    })
    existing.setdefault("privacy", "unlisted")

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
