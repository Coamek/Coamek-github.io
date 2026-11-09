#!/usr/bin/env python3
"""Import the owner's Instagram photos into the existing static portfolio.

Requires Pillow for new image downloads. Authentication stays in the
INSTAGRAM_ACCESS_TOKEN environment variable and is never written to the site.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


CATEGORIES = ("basketball", "hockey", "media-day", "personal")
MARKER_KEYS = ("basketball", "hockey", "media-day", "personal-vertical", "personal-wide")
ASSET_DIRECTORY = "assets/photography/web/instagram"
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_JSON_BYTES = 10 * 1024 * 1024
MEDIA_FIELDS = "id,caption,media_type,media_url,permalink,timestamp,alt_text"
CHILD_FIELDS = "id,media_type,media_url,alt_text"
UNKNOWN_CATEGORY_REASON = "No clear category; add a #portfolio_ category tag or an ID override."


class SyncError(Exception):
    """A safe, user-readable import failure."""


class APIError(SyncError):
    def __init__(self, message: str, unsupported_caption: bool = False):
        super().__init__(message)
        self.unsupported_caption = unsupported_caption


def numeric_id(value: object) -> str:
    result = str(value)
    if not re.fullmatch(r"[0-9]{1,40}", result):
        raise SyncError("Instagram returned an invalid media or account ID.")
    return result


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SyncError(f"Missing required file: {path.name}") from None
    except (UnicodeError, json.JSONDecodeError):
        raise SyncError(f"Invalid JSON in {path.name}.") from None


def load_config(path: Path) -> dict:
    config = read_json(path)
    if not isinstance(config, dict):
        raise SyncError("Instagram config must be a JSON object.")
    username = config.get("username", "")
    version = config.get("api_version", "v27.0")
    overrides = config.get("category_overrides", {})
    login_method, account_id = login_settings(config.get("login_method", "instagram"),
                                              config.get("instagram_account_id"))
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", username):
        raise SyncError("Set a valid Instagram username in instagram-config.json.")
    if not isinstance(version, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+", version):
        raise SyncError("Instagram api_version must look like v27.0.")
    if not isinstance(overrides, dict):
        raise SyncError("category_overrides must map media IDs to categories or skip.")
    for media_id, category in overrides.items():
        numeric_id(media_id)
        if category not in (*CATEGORIES, "skip"):
            raise SyncError("Every category override must be basketball, hockey, media-day, personal, or skip.")
    return {"username": username, "api_version": version, "category_overrides": overrides,
            "login_method": login_method, "instagram_account_id": account_id}


def login_settings(login_method: object, account_id: object) -> tuple[str, str | None]:
    if login_method not in ("instagram", "unified"):
        raise SyncError("Instagram login_method must be instagram or unified.")
    if login_method == "unified" or account_id is not None:
        if (isinstance(account_id, bool) or not isinstance(account_id, (str, int))
                or not re.fullmatch(r"[0-9]{1,40}", str(account_id))):
            raise SyncError("Unified Login requires a numeric instagram_account_id in instagram-config.json.")
        account_id = str(account_id)
    return login_method, account_id


def timestamp_key(value: object) -> datetime:
    if not isinstance(value, str):
        raise SyncError("A photo is missing its Instagram timestamp.")
    try:
        normalized = re.sub(r"([+-][0-9]{2})([0-9]{2})$", r"\1:\2", value.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise SyncError("A photo has an invalid Instagram timestamp.") from None


def clean_permalink(value: object) -> str:
    if not isinstance(value, str):
        raise SyncError("An Instagram photo is missing its post link.")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc not in ("instagram.com", "www.instagram.com")
            or not re.fullmatch(r"/(?:[A-Za-z0-9_.]+/)?(?:p|reel|tv)/[A-Za-z0-9_-]+/?", parsed.path)):
        raise SyncError("An Instagram photo has an invalid post link.")
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/") + "/", "", ""))


def load_manifest(path: Path) -> dict:
    manifest = read_json(path) if path.exists() else {"version": 1, "photos": [], "pending": []}
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or not isinstance(manifest.get("photos"), list)
            or not isinstance(manifest.get("pending"), list)):
        raise SyncError("instagram-photos.json must contain version 1, photos, and pending.")
    seen = set()
    for photo in manifest["photos"]:
        if not isinstance(photo, dict):
            raise SyncError("The Instagram manifest contains an invalid photo.")
        media_id = numeric_id(photo.get("id"))
        numeric_id(photo.get("post_id"))
        if media_id in seen:
            raise SyncError("The Instagram manifest contains duplicate photo IDs.")
        seen.add(media_id)
        if photo.get("category") not in CATEGORIES:
            raise SyncError("The Instagram manifest contains an invalid photo category.")
        if photo.get("src") != f"{ASSET_DIRECTORY}/{media_id}.jpg":
            raise SyncError("The Instagram manifest contains an invalid image path.")
        for dimension in ("width", "height"):
            if type(photo.get(dimension)) is not int or not 1 <= photo[dimension] <= 1800:
                raise SyncError("The Instagram manifest contains invalid image dimensions.")
        if not isinstance(photo.get("alt"), str):
            raise SyncError("The Instagram manifest contains invalid image alt text.")
        clean_permalink(photo.get("permalink"))
        timestamp_key(photo.get("timestamp"))
    for pending in manifest["pending"]:
        if not isinstance(pending, dict):
            raise SyncError("The Instagram manifest contains an invalid pending photo.")
        media_id = numeric_id(pending.get("id"))
        if "post_id" in pending:
            numeric_id(pending["post_id"])
        if media_id in seen:
            raise SyncError("The Instagram manifest contains duplicate media IDs.")
        seen.add(media_id)
        clean_permalink(pending.get("permalink"))
        if not all(isinstance(pending.get(key), str) for key in ("caption", "reason")):
            raise SyncError("The Instagram manifest contains an invalid pending photo.")
    return manifest


def classify_caption(caption: str) -> tuple[str | None, str]:
    """Prefer explicit portfolio tags, then unambiguous caption keywords."""
    text = caption.casefold()
    tags = set(re.findall(r"(?<!\w)#portfolio_(basketball|hockey|media_day|personal)(?!\w)", text))
    if len(tags) == 1:
        return tags.pop().replace("_", "-"), ""
    if tags:
        return None, "Conflicting #portfolio_ category tags; choose one tag or add an ID override."
    if re.search(r"\bmedia[\s_-]*day\b", text):
        return "media-day", ""
    basketball = bool(re.search(r"\b(?:basketball|hoops|bball|mbb|wbb)\b", text))
    hockey = bool(re.search(r"\b(?:hockey|whky|mhky|puck)\b", text))
    if basketball and hockey:
        return None, "Both basketball and hockey appear; add a #portfolio_ category tag or an ID override."
    if basketball:
        return "basketball", ""
    if hockey:
        return "hockey", ""
    if re.search(r"\b(?:personal|landscapes?|nature|travel|travels)\b", text):
        return "personal", ""
    return None, UNKNOWN_CATEGORY_REASON


def photo_alt(alt_text: object, caption: str, category: str, username: str) -> str:
    text = alt_text if isinstance(alt_text, str) and alt_text.strip() else caption
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = f"{category.replace('-', ' ').capitalize()} photograph by @{username}"
    return text if len(text) <= 240 else text[:237].rstrip() + "..."


def validate_markers(html: str) -> None:
    spans = []
    for key in MARKER_KEYS:
        start = f"<!-- instagram:{key}:start -->"
        end = f"<!-- instagram:{key}:end -->"
        if html.count(start) != 1 or html.count(end) != 1:
            raise SyncError(f"photography.html needs exactly one Instagram marker pair for {key}.")
        first, last = html.index(start), html.index(end)
        if first >= last:
            raise SyncError(f"The Instagram marker pair for {key} is out of order.")
        spans.append((first, last + len(end)))
    spans.sort()
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        raise SyncError("Instagram marker blocks must not overlap or nest.")


def sorted_photos(photos: list[dict]) -> list[dict]:
    return sorted(photos, key=lambda photo: (timestamp_key(photo["timestamp"]), int(photo["id"])), reverse=True)


def render_html(html: str, photos: list[dict]) -> str:
    validate_markers(html)
    groups: dict[str, list[dict]] = {key: [] for key in MARKER_KEYS}
    for photo in sorted_photos(photos):
        key = photo["category"]
        if key == "personal":
            key += "-wide" if photo["width"] > photo["height"] else "-vertical"
        groups[key].append(photo)
    newline = "\r\n" if "\r\n" in html else "\n"
    for key in MARKER_KEYS:
        start = f"<!-- instagram:{key}:start -->"
        end = f"<!-- instagram:{key}:end -->"
        start_pos = html.index(start) + len(start)
        end_pos = html.index(end)
        line_start = html.rfind("\n", 0, html.index(start)) + 1
        prefix = html[line_start:html.index(start)]
        indent = prefix if not prefix.strip() else ""
        figures = []
        for photo in groups[key]:
            attr = lambda value: escape(str(value), quote=True)
            classes = "instagram-photo"
            if photo["category"] != "personal" and photo["width"] > photo["height"]:
                classes += " instagram-photo-horizontal"
            figures.append(
                f'<figure class="{classes}" data-instagram-id="{attr(photo["id"])}">'
                f'<img src="{attr(photo["src"])}" alt="{attr(photo["alt"])}" '
                f'width="{photo["width"]}" height="{photo["height"]}" loading="lazy" decoding="async"></figure>'
            )
        figures = [indent + figure for figure in figures]
        content = newline + (newline.join(figures) + newline if figures else "") + indent
        html = html[:start_pos] + content + html[end_pos:]
    return html


def read_bounded(response, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise SyncError("Instagram returned a response larger than the import limit.")
    return data


def api_error(payload: object, status: int | None = None, context: str = "") -> APIError:
    detail = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = str(detail.get("message", "")).casefold() if isinstance(detail, dict) else ""
    unsupported = (isinstance(detail, dict) and detail.get("code") == 100 and "caption" in message
                   and any(word in message for word in ("nonexisting field", "non-existing field", "unknown field", "unsupported field", "does not exist")))
    suffix = f" (HTTP {status})" if status is not None else ""
    selected = f" using {context}" if context else ""
    return APIError(f"Instagram API request failed{suffix}{selected}; check the selected login method's token, account access, and app permissions.", unsupported)


def normalize_token(token: str) -> str:
    token = token.strip()
    if not token:
        raise SyncError("Set INSTAGRAM_ACCESS_TOKEN to sync Instagram, or use --render-only for existing local photos.")
    if re.search(r"[^\x21-\x7e]", token):
        raise SyncError("INSTAGRAM_ACCESS_TOKEN contains invalid whitespace or control characters.")
    return token


class GraphRedirectHandler(HTTPRedirectHandler):
    """Keep bearer-authenticated API redirects on the selected Graph host."""

    def __init__(self, host: str):
        super().__init__()
        self.host = host

    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        if parsed.scheme != "https" or parsed.netloc != self.host:
            raise SyncError("Instagram returned an unexpected API redirect host.")
        return super().redirect_request(request, response, code, message, headers, new_url)


class InstagramAPI:
    def __init__(self, token: str, version: str = "v27.0", opener=None,
                 login_method: str = "instagram", account_id: str | None = None):
        self.token = normalize_token(token)
        self.login_method, self.account_id = login_settings(login_method, account_id)
        self.host = "graph.facebook.com" if self.login_method == "unified" else "graph.instagram.com"
        self.base_url = f"https://{self.host}/{version}/"
        self.context = (f"Unified Login on {self.host} for configured Instagram account {self.account_id}"
                        if self.login_method == "unified" else f"Instagram Login on {self.host}")
        self.opener = opener or build_opener(GraphRedirectHandler(self.host)).open

    def request_json(self, url: str) -> dict:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != self.host:
            raise SyncError("Instagram returned an unexpected API request host.")
        request = Request(url, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        try:
            with self.opener(request, timeout=30) as response:
                data = read_bounded(response, MAX_JSON_BYTES)
        except HTTPError as error:
            try:
                payload = json.loads(error.read(MAX_JSON_BYTES))
            except (ValueError, UnicodeError, OSError):
                payload = {}
            raise api_error(payload, error.code, self.context) from None
        except (URLError, TimeoutError, OSError):
            raise SyncError("Could not reach Instagram; no portfolio files were changed.") from None
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeError):
            raise SyncError("Instagram returned invalid JSON; no portfolio files were changed.") from None
        if not isinstance(payload, dict):
            raise SyncError("Instagram returned an unexpected API response.")
        if "error" in payload:
            raise api_error(payload, context=self.context)
        return payload

    def url(self, endpoint: str, **params) -> str:
        return self.base_url + endpoint + "?" + urlencode(params)

    def verify_account(self, expected_username: str) -> str:
        unified = self.login_method == "unified"
        endpoint = self.account_id if unified else "me"
        fields = "id,username" if unified else "user_id,username"
        payload = self.request_json(self.url(endpoint, fields=fields))
        identity = payload.get("data", payload)
        if isinstance(identity, list):
            identity = identity[0] if len(identity) == 1 else {}
        if not isinstance(identity, dict) or str(identity.get("username", "")).casefold() != expected_username.casefold():
            raise SyncError("The Instagram token belongs to a different account; the portfolio was not changed.")
        if unified:
            if str(identity.get("id", "")) != self.account_id:
                raise SyncError("Unified Login returned a different Instagram account ID; the portfolio was not changed.")
            return self.account_id
        return numeric_id(identity.get("user_id", identity.get("id")))

    def safe_next_url(self, value: object) -> str | None:
        if not value:
            return None
        if not isinstance(value, str):
            raise SyncError("Instagram returned an invalid pagination link.")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.netloc != self.host:
            raise SyncError("Instagram returned an unexpected pagination host.")
        params = [(key, val) for key, val in parse_qsl(parsed.query) if key.casefold() != "access_token"]
        return urlunsplit(("https", self.host, parsed.path, urlencode(params), ""))

    def collection(self, endpoint: str, fields: str) -> list[dict]:
        url = self.url(endpoint, fields=fields, limit=100)
        results = []
        visited = set()
        while url:
            if url in visited:
                raise SyncError("Instagram pagination repeated a page; the portfolio was not changed.")
            visited.add(url)
            payload = self.request_json(url)
            items = payload.get("data")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise SyncError("Instagram returned an invalid media collection.")
            results.extend(items)
            paging = payload.get("paging", {})
            if not isinstance(paging, dict):
                raise SyncError("Instagram returned invalid pagination data.")
            url = self.safe_next_url(paging.get("next"))
        return results

    def media(self, user_id: str) -> list[dict]:
        try:
            return self.collection(f"{numeric_id(user_id)}/media", MEDIA_FIELDS)
        except APIError as error:
            if not error.unsupported_caption:
                raise
            return self.collection(f"{numeric_id(user_id)}/media", MEDIA_FIELDS.replace(",caption", ""))

    def children(self, post_id: str) -> list[dict]:
        return self.collection(f"{numeric_id(post_id)}/children", CHILD_FIELDS)


def download_image(url: object, *, opener=None, sleeper=None) -> bytes:
    if not isinstance(url, str):
        raise SyncError("An Instagram photo is missing its download URL.")
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or not any(host == domain or host.endswith("." + domain) for domain in ("cdninstagram.com", "fbcdn.net"))):
        raise SyncError("Instagram returned an unexpected image download host.")
    request = Request(url, headers={"User-Agent": "PortfolioInstagramImporter/1.0"})
    open_image = opener or urlopen
    wait = sleeper or time.sleep
    for attempt in range(3):
        retry = True
        try:
            with open_image(request, timeout=60) as response:
                return read_bounded(response, MAX_IMAGE_BYTES)
        except HTTPError as error:
            cause = f"HTTP {error.code}"
            retry = error.code in (408, 429, 500, 502, 503, 504)
        except (URLError, TimeoutError, OSError) as error:
            underlying = error.reason if isinstance(error, URLError) else error
            cause = "network timeout" if isinstance(underlying, TimeoutError) else "network error"
        if not retry or attempt == 2:
            attempts = attempt + 1
            count = "attempt" if attempts == 1 else "attempts"
            raise SyncError(f"An Instagram image could not be downloaded ({cause} after {attempts} {count}); the portfolio was not changed.") from None
        wait(attempt + 1)


def optimize_image(data: bytes, output: Path) -> tuple[int, int]:
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise SyncError("Install Pillow before importing images: python3 -m pip install Pillow") from None
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > 60_000_000:
                raise SyncError("An Instagram image exceeds the supported pixel limit.")
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = image.convert("RGB")
            image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            image.save(output, "JPEG", quality=85, optimize=True, progressive=True, exif=b"")
            return image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise SyncError("An Instagram image could not be processed; the portfolio was not changed.") from None


def stage_sync(root: Path, manifest: dict, config: dict, api: InstagramAPI,
               staging: Path, downloader=download_image, optimizer=optimize_image) -> tuple[dict, dict[Path, Path]]:
    user_id = api.verify_account(config["username"])
    posts = api.media(user_id)
    photos = {photo["id"]: dict(photo) for photo in manifest["photos"]}
    pending = {photo["id"]: dict(photo) for photo in manifest["pending"]}
    overrides = config["category_overrides"]
    staged_files: dict[Path, Path] = {}
    processed = set()

    for post in posts:
        post_id = numeric_id(post.get("id"))
        if post_id in processed:
            continue
        processed.add(post_id)
        permalink = clean_permalink(post.get("permalink"))
        if overrides.get(post_id) == "skip":
            photos = {key: photo for key, photo in photos.items() if photo["post_id"] != post_id and key != post_id}
            pending = {key: photo for key, photo in pending.items()
                       if key != post_id and photo.get("post_id") != post_id and photo["permalink"] != permalink}
            continue
        media_type = post.get("media_type")
        if media_type == "CAROUSEL_ALBUM":
            items = api.children(post_id)
        elif media_type == "IMAGE":
            items = [post]
        else:
            continue
        caption = post.get("caption") or post.get("alt_text") or ""
        if not isinstance(caption, str):
            raise SyncError("Instagram returned a non-text photo caption.")
        for item in items:
            if item.get("media_type") != "IMAGE":
                continue
            media_id = numeric_id(item.get("id"))
            if media_id in processed and media_id != post_id:
                continue
            processed.add(media_id)
            category = overrides.get(media_id, overrides.get(post_id))
            if category == "skip":
                photos.pop(media_id, None)
                pending.pop(media_id, None)
                continue
            reason = ""
            item_alt = item.get("alt_text") if isinstance(item.get("alt_text"), str) else ""
            if category is None:
                category, reason = classify_caption(caption or item_alt)
                if category is None and reason == UNKNOWN_CATEGORY_REASON and item_alt:
                    category, reason = classify_caption(item_alt)
            if category is None:
                photos.pop(media_id, None)
                pending[media_id] = {"id": media_id, "post_id": post_id, "permalink": permalink,
                                     "caption": (caption or item_alt)[:2200], "reason": reason}
                continue
            old = photos.get(media_id)
            src = f"{ASSET_DIRECTORY}/{media_id}.jpg"
            destination = root / src
            if old and destination.is_file():
                width, height = old["width"], old["height"]
            else:
                staged = staging / f"{media_id}.jpg"
                try:
                    downloaded = downloader(item.get("media_url"))
                except SyncError as error:
                    raise SyncError(f"Instagram media {media_id}: {error}") from None
                width, height = optimizer(downloaded, staged)
                staged_files[destination] = staged
            photos[media_id] = {
                "id": media_id, "post_id": post_id, "category": category, "src": src,
                "width": width, "height": height,
                "alt": photo_alt(item.get("alt_text"), caption, category, config["username"]),
                "permalink": permalink, "timestamp": post.get("timestamp"),
            }
            timestamp_key(photos[media_id]["timestamp"])
            pending.pop(media_id, None)
    result = {"version": 1, "photos": sorted_photos(list(photos.values())),
              "pending": sorted(pending.values(), key=lambda photo: int(photo["id"]), reverse=True)}
    return result, staged_files


def atomic_write(path: Path, data: bytes) -> None:
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=".instagram-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def commit_sync(staged_files: dict[Path, Path], html_path: Path, html: str,
                manifest_path: Path | None = None, manifest: dict | None = None) -> None:
    updates = [(path, staged.read_bytes()) for path, staged in staged_files.items()]
    if manifest_path is not None:
        updates.append((manifest_path, (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")))
    updates.append((html_path, html.encode("utf-8")))
    originals = {path: path.read_bytes() if path.exists() else None for path, _ in updates}
    written = []
    try:
        for path, data in updates:
            atomic_write(path, data)
            written.append(path)
    except OSError:
        for path in reversed(written):
            previous = originals[path]
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, previous)
        raise SyncError("Could not save the Instagram update; previous portfolio files were restored.") from None


def run(root: Path, render_only: bool = False, token: str | None = None, api=None,
        downloader=download_image, optimizer=optimize_image) -> dict:
    html_path = root / "photography.html"
    try:
        html = html_path.read_bytes().decode("utf-8")
    except (FileNotFoundError, UnicodeError):
        raise SyncError("A readable photography.html is required in the repository root.") from None
    validate_markers(html)
    manifest_path = root / "data/instagram-photos.json"
    manifest = load_manifest(manifest_path)
    if render_only:
        for photo in manifest["photos"]:
            if not (root / photo["src"]).is_file():
                raise SyncError("A manifest image is missing locally; run an authenticated sync to restore it.")
        commit_sync({}, html_path, render_html(html, manifest["photos"]))
        return manifest
    access_token = token if token is not None else os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    access_token = normalize_token(access_token)
    config = load_config(root / "data/instagram-config.json")
    api = api or InstagramAPI(access_token, config["api_version"], login_method=config["login_method"],
                             account_id=config["instagram_account_id"])
    with tempfile.TemporaryDirectory(prefix="portfolio-instagram-") as staging:
        updated, staged = stage_sync(root, manifest, config, api, Path(staging), downloader, optimizer)
        output = render_html(html, updated["photos"])
        commit_sync(staged, html_path, output, manifest_path, updated)
    return updated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-only", action="store_true", help="render downloaded photos without contacting Instagram")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="portfolio repository root (defaults to this script's repository)")
    args = parser.parse_args(argv)
    try:
        result = run(args.root.resolve(), args.render_only)
    except (SyncError, OSError) as error:
        message = f"Instagram sync: {error}"
        print(message, file=sys.stderr)
        if isinstance(error, SyncError) and os.environ.get("GITHUB_ACTIONS") == "true":
            annotation = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error::{annotation}", file=sys.stderr)
        return 1
    print(f"Instagram gallery: {len(result['photos'])} imported photos; {len(result['pending'])} need a category.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
