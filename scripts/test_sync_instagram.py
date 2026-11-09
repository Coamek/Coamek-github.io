"""Offline checks for Instagram imports, category routing, and failure handling."""

from contextlib import redirect_stderr
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_instagram as sync


LINK = "https://www.instagram.com/p/TESTPOST/"
STAMP = "2026-11-09T12:00:00+0000"


def image(media_id="101", caption="Basketball game", **extra):
    result = {"id": media_id, "media_type": "IMAGE", "caption": caption,
              "media_url": "https://scontent.cdninstagram.com/signed.jpg?secret=temporary",
              "permalink": LINK, "timestamp": STAMP}
    result.update(extra)
    return result


def photo(media_id="101", category="basketball", **extra):
    result = {"id": media_id, "post_id": media_id, "category": category,
              "src": f"{sync.ASSET_DIRECTORY}/{media_id}.jpg", "width": 1200, "height": 1800,
              "alt": "A basketball game", "permalink": LINK, "timestamp": STAMP}
    result.update(extra)
    return result


def page_html(newline="\n"):
    lines = ['<html><body><figure id="curated"><img src="existing.jpg"></figure>']
    for key in sync.MARKER_KEYS:
        lines.extend([f'  <div data-category="{key}">',
                      f"    <!-- instagram:{key}:start -->",
                      f"    <!-- instagram:{key}:end -->", "  </div>"])
    return newline.join(lines + ["</body></html>", ""])


class FakeResponse(BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))


class FakeAPI:
    def __init__(self, posts=None, children=None):
        self.posts = posts or []
        self.child_items = children or {}
        self.children_requested = []

    def verify_account(self, username):
        if username != "oamekphotography":
            raise AssertionError("Wrong configured username")
        return "42"

    def media(self, user_id):
        if user_id != "42":
            raise AssertionError("Wrong Instagram user ID")
        return self.posts

    def children(self, post_id):
        self.children_requested.append(post_id)
        return self.child_items[post_id]


class ClassificationTests(unittest.TestCase):
    def test_existing_categories_and_media_day_precedence(self):
        cases = {"Basketball at the Kohl Center #mbb": "basketball", "A hockey game #whky": "hockey",
                 "Basketball and hockey Media Day": "media-day", "#mediaday portraits": "media-day",
                 "A personal photograph": "personal", "Nature and landscapes": "personal", "Travel in Chicago": "personal"}
        for caption, expected in cases.items():
            with self.subTest(caption=caption):
                self.assertEqual(sync.classify_caption(caption), (expected, ""))

    def test_explicit_tags_override_heuristics(self):
        self.assertEqual(sync.classify_caption("basketball hockey #portfolio_personal"), ("personal", ""))
        self.assertEqual(sync.classify_caption("#PORTFOLIO_MEDIA_DAY hockey"), ("media-day", ""))

    def test_conflicts_unknown_and_partial_tags_wait_for_category(self):
        for caption in ("basketball and hockey", "#portfolio_hockey #portfolio_basketball", "A great night!",
                        "#portfolio_personally", "basketballer", "#portfolio_media_day_extra"):
            with self.subTest(caption=caption):
                category, reason = sync.classify_caption(caption)
                self.assertIsNone(category)
                self.assertTrue(reason)


class APITests(unittest.TestCase):
    def make_api(self, payloads, **settings):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            payload = payloads.pop(0)
            if isinstance(payload, Exception):
                raise payload
            return FakeResponse(payload)
        return sync.InstagramAPI("SECRET_TOKEN", opener=opener, **settings), calls

    def test_verify_identity_direct_object_and_data_array(self):
        for identity in ({"user_id": "42", "username": "OamekPhotography"},
                         {"data": [{"user_id": "42", "username": "oamekphotography"}]}):
            api, calls = self.make_api([identity])
            self.assertEqual(api.verify_account("oamekphotography"), "42")
            self.assertEqual(parse_qs(urlsplit(calls[0].full_url).query)["fields"], ["user_id,username"])
            self.assertEqual(calls[0].get_header("Authorization"), "Bearer SECRET_TOKEN")
            self.assertNotIn("SECRET_TOKEN", calls[0].full_url)

    def test_wrong_account_fails_without_exposing_identity_or_token(self):
        api, _ = self.make_api([{"user_id": "42", "username": "another_person"}])
        with self.assertRaises(sync.SyncError) as context:
            api.verify_account("oamekphotography")
        self.assertNotIn("another_person", str(context.exception))
        self.assertNotIn("SECRET_TOKEN", str(context.exception))

    def test_paginated_media_and_children_strip_query_tokens(self):
        api, calls = self.make_api([
            {"data": [image("101")], "paging": {"next": "https://graph.instagram.com/v27.0/42/media?after=page2&access_token=URL_TOKEN"}},
            {"data": [image("102")]},
            {"data": [{"id": "201", "media_type": "IMAGE"}], "paging": {"next": "https://graph.instagram.com/v27.0/100/children?after=child2&access_token=URL_TOKEN"}},
            {"data": [{"id": "202", "media_type": "VIDEO"}]},
        ])
        self.assertEqual([item["id"] for item in api.media("42")], ["101", "102"])
        self.assertEqual([item["id"] for item in api.children("100")], ["201", "202"])
        self.assertEqual(parse_qs(urlsplit(calls[2].full_url).query)["fields"], [sync.CHILD_FIELDS])
        for request in calls:
            self.assertNotIn("access_token", request.full_url)
            self.assertNotIn("URL_TOKEN", request.full_url)

    def test_unsupported_caption_field_has_one_narrow_fallback(self):
        error = HTTPError("https://graph.instagram.com", 400, "Bad request", {},
                          BytesIO(json.dumps({"error": {"code": 100, "message": "Tried accessing nonexisting field (caption) on node type (IGMedia)"}}).encode()))
        api, calls = self.make_api([error, {"data": [image("101", alt_text="Basketball action")]}])
        self.assertEqual(api.media("42")[0]["id"], "101")
        self.assertNotIn("caption", parse_qs(urlsplit(calls[1].full_url).query)["fields"][0].split(","))
        error = HTTPError("https://graph.instagram.com", 400, "SECRET_TOKEN", {},
                          BytesIO(json.dumps({"error": {"code": 100, "message": "Invalid account id SECRET_TOKEN"}}).encode()))
        api, calls = self.make_api([error])
        with self.assertRaises(sync.APIError) as context:
            api.media("42")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("SECRET_TOKEN", str(context.exception))

    def test_untrusted_pagination_and_network_errors_are_safe(self):
        api, _ = self.make_api([{"data": [], "paging": {"next": "https://evil.example/?access_token=SECRET_TOKEN"}}])
        with self.assertRaisesRegex(sync.SyncError, "unexpected pagination host"):
            api.media("42")
        api, _ = self.make_api([URLError("https://example/?access_token=SECRET_TOKEN")])
        with self.assertRaises(sync.SyncError) as context:
            api.media("42")
        self.assertNotIn("SECRET_TOKEN", str(context.exception))

    def test_tokens_trim_pasted_newlines_and_reject_internal_controls(self):
        api = sync.InstagramAPI("  SECRET_TOKEN\r\n")
        self.assertEqual(api.token, "SECRET_TOKEN")
        for token in ("SECRET\nTOKEN", "SECRET\x00TOKEN", "SECRET TOKEN"):
            with self.subTest(token_format=repr(token.replace("SECRET", "..."))):
                with self.assertRaises(sync.SyncError) as context:
                    sync.InstagramAPI(token)
                self.assertNotIn("SECRET", str(context.exception))

    def test_unified_login_uses_configured_instagram_account_and_facebook_host(self):
        api, calls = self.make_api([
            {"id": "42", "username": "oamekphotography"},
            {"data": [image("101")], "paging": {"next": "https://graph.facebook.com/v27.0/42/media?after=page2&access_token=URL_TOKEN"}},
            {"data": [image("102")]},
            {"data": [{"id": "201", "media_type": "IMAGE"}], "paging": {"next": "https://graph.facebook.com/v27.0/100/children?after=page2&access_token=URL_TOKEN"}},
            {"data": [{"id": "202", "media_type": "VIDEO"}]},
        ], login_method="unified", account_id="42")
        self.assertEqual(api.verify_account("oamekphotography"), "42")
        self.assertEqual([item["id"] for item in api.media("42")], ["101", "102"])
        self.assertEqual([item["id"] for item in api.children("100")], ["201", "202"])
        self.assertEqual(urlsplit(calls[0].full_url).path, "/v27.0/42")
        self.assertEqual(parse_qs(urlsplit(calls[0].full_url).query)["fields"], ["id,username"])
        self.assertEqual(urlsplit(calls[1].full_url).path, "/v27.0/42/media")
        self.assertEqual(urlsplit(calls[3].full_url).path, "/v27.0/100/children")
        for request in calls:
            self.assertEqual(urlsplit(request.full_url).netloc, "graph.facebook.com")
            self.assertNotIn("/me", urlsplit(request.full_url).path.replace("/media", ""))
            self.assertNotIn("access_token", request.full_url)
            self.assertNotIn("URL_TOKEN", request.full_url)
            self.assertEqual(request.get_header("Authorization"), "Bearer SECRET_TOKEN")

    def test_pagination_never_switches_between_login_hosts(self):
        cases = [("instagram", None, "graph.facebook.com"), ("unified", "42", "graph.instagram.com")]
        for method, account_id, other_host in cases:
            with self.subTest(login_method=method):
                api, calls = self.make_api([{"data": [], "paging": {"next": f"https://{other_host}/v27.0/42/media?access_token=SECRET_TOKEN"}}],
                                           login_method=method, account_id=account_id)
                with self.assertRaisesRegex(sync.SyncError, "unexpected pagination host"):
                    api.media("42")
                self.assertEqual(len(calls), 1)
                with self.assertRaisesRegex(sync.SyncError, "unexpected API request host"):
                    api.request_json(f"https://{other_host}/v27.0/42")
                self.assertEqual(len(calls), 1)

    def test_authenticated_redirects_cannot_switch_graph_hosts(self):
        handler = sync.GraphRedirectHandler("graph.facebook.com")
        request = Request("https://graph.facebook.com/v27.0/42", headers={"Authorization": "Bearer SECRET_TOKEN"})
        with self.assertRaisesRegex(sync.SyncError, "unexpected API redirect host"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://graph.instagram.com/v27.0/42")

    def test_unified_401_identifies_selected_flow_without_raw_api_error(self):
        raw_error = {"error": {"code": 190, "message": "Invalid OAuth access token SECRET_TOKEN"}}
        error = HTTPError("https://graph.facebook.com", 401, "SECRET_TOKEN", {},
                          BytesIO(json.dumps(raw_error).encode()))
        api, calls = self.make_api([error], login_method="unified", account_id="42")
        with self.assertRaises(sync.APIError) as context:
            api.verify_account("oamekphotography")
        message = str(context.exception)
        for text in ("HTTP 401", "Unified Login", "graph.facebook.com", "account 42"):
            self.assertIn(text, message)
        self.assertNotIn("SECRET_TOKEN", message)
        self.assertNotIn("Invalid OAuth", message)
        self.assertEqual(len(calls), 1)

    def test_opener_still_accepts_the_third_positional_argument(self):
        def opener(request, timeout):
            return FakeResponse({"user_id": "42", "username": "oamekphotography"})
        api = sync.InstagramAPI("SECRET_TOKEN", "v27.0", opener)
        self.assertEqual(api.verify_account("oamekphotography"), "42")
        self.assertEqual(api.host, "graph.instagram.com")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "instagram-config.json"

    def config(self, **settings):
        config = {"username": "oamekphotography", "api_version": "v27.0", "category_overrides": {}}
        config.update(settings)
        self.path.write_text(json.dumps(config))
        return sync.load_config(self.path)

    def test_legacy_config_defaults_to_instagram_login(self):
        config = self.config()
        self.assertEqual(config["login_method"], "instagram")
        self.assertIsNone(config["instagram_account_id"])

    def test_unified_login_keeps_configured_numeric_account(self):
        for account_id in ("17841477718206518", 17841477718206518):
            config = self.config(login_method="unified", instagram_account_id=account_id)
            self.assertEqual(config["login_method"], "unified")
            self.assertEqual(config["instagram_account_id"], "17841477718206518")

    def test_invalid_login_method_and_missing_or_invalid_account_are_rejected(self):
        for method in ("facebook", "UNIFIED", "", None):
            with self.subTest(login_method=method):
                with self.assertRaisesRegex(sync.SyncError, "login_method"):
                    self.config(login_method=method)
        for account_id in (None, "", "not-a-number", "42/path", True, 42.0):
            with self.subTest(account_id=account_id):
                with self.assertRaisesRegex(sync.SyncError, "numeric instagram_account_id"):
                    self.config(login_method="unified", instagram_account_id=account_id)


class DownloadTests(unittest.TestCase):
    url = "https://scontent.cdninstagram.com/image.jpg?access_token=SECRET_URL_TOKEN"

    def opener(self, results):
        calls = []
        def open_image(request, timeout):
            calls.append(request)
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return BytesIO(result)
        return open_image, calls

    def http_error(self, status):
        return HTTPError(self.url, status, "SECRET_HTTP_REASON", {}, BytesIO(b"SECRET_HTTP_BODY"))

    def test_transient_download_errors_retry_with_bounded_backoff(self):
        opener, calls = self.opener([URLError("SECRET_URL_TOKEN"), self.http_error(503), b"JPEG"])
        sleeps = []
        self.assertEqual(sync.download_image(self.url, opener=opener, sleeper=sleeps.append), b"JPEG")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [1, 2])
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                opener, calls = self.opener([self.http_error(status), b"JPEG"])
                sleeps = []
                self.assertEqual(sync.download_image(self.url, opener=opener, sleeper=sleeps.append), b"JPEG")
                self.assertEqual(len(calls), 2)
                self.assertEqual(sleeps, [1])

    def test_final_download_error_reports_safe_status_or_network_cause(self):
        cases = [([self.http_error(503) for _ in range(3)], "HTTP 503"),
                 ([URLError(TimeoutError("SECRET_TIMEOUT")) for _ in range(3)], "network timeout"),
                 ([OSError("SECRET_NETWORK") for _ in range(3)], "network error")]
        for results, expected in cases:
            with self.subTest(cause=expected):
                opener, calls = self.opener(results)
                sleeps = []
                with self.assertRaises(sync.SyncError) as context:
                    sync.download_image(self.url, opener=opener, sleeper=sleeps.append)
                message = str(context.exception)
                self.assertIn(expected, message)
                self.assertIn("after 3 attempts", message)
                self.assertNotIn("SECRET", message)
                self.assertNotIn("https://", message)
                self.assertEqual(len(calls), 3)
                self.assertEqual(sleeps, [1, 2])

    def test_nontransient_http_errors_fail_immediately_without_exposing_secrets(self):
        for status in (400, 401, 403, 404, 501):
            with self.subTest(status=status):
                opener, calls = self.opener([self.http_error(status)])
                sleeps = []
                with self.assertRaises(sync.SyncError) as context:
                    sync.download_image(self.url, opener=opener, sleeper=sleeps.append)
                self.assertIn(f"HTTP {status} after 1 attempt", str(context.exception))
                self.assertNotIn("SECRET", str(context.exception))
                self.assertNotIn("https://", str(context.exception))
                self.assertEqual(len(calls), 1)
                self.assertEqual(sleeps, [])

    def test_oversized_image_response_is_not_retried(self):
        opener, calls = self.opener([b"image"])
        sleeps = []
        with patch.object(sync, "read_bounded", side_effect=sync.SyncError("Image exceeds the size limit")):
            with self.assertRaisesRegex(sync.SyncError, "size limit"):
                sync.download_image(self.url, opener=opener, sleeper=sleeps.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.html = page_html()
        (self.root / "photography.html").write_text(self.html, encoding="utf-8")
        self.set_config()
        self.set_manifest()
        self.downloads = []

    def set_config(self, overrides=None, **settings):
        config = {
            "username": "oamekphotography", "api_version": "v27.0", "category_overrides": overrides or {}
        }
        config.update(settings)
        (self.root / "data/instagram-config.json").write_text(json.dumps(config), encoding="utf-8")

    def set_manifest(self, photos=None, pending=None):
        manifest = {"version": 1, "photos": photos or [], "pending": pending or []}
        (self.root / "data/instagram-photos.json").write_text(json.dumps(manifest), encoding="utf-8")
        for item in photos or []:
            path = self.root / item["src"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"existing-jpeg")

    def downloader(self, url):
        self.downloads.append(url)
        return b"downloaded-image"

    def optimizer(self, data, path):
        path.write_bytes(b"optimized:" + data)
        return 1200, 1800

    def run_sync(self, api, **kwargs):
        return sync.run(self.root, token="TOKEN", api=api, downloader=self.downloader,
                        optimizer=kwargs.pop("optimizer", self.optimizer), **kwargs)

    def file_state(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_carousel_imports_every_image_skips_video_and_deduplicates(self):
        parent = image("100", media_type="CAROUSEL_ALBUM")
        child = image("201", alt_text="Player with the ball")
        other = image("202")
        api = FakeAPI([parent, parent], {"100": [child, child, image("203", media_type="VIDEO"), other]})
        result = self.run_sync(api)
        self.assertEqual({item["id"] for item in result["photos"]}, {"201", "202"})
        self.assertTrue(all(item["post_id"] == "100" for item in result["photos"]))
        self.assertEqual(len(self.downloads), 2)
        self.assertEqual(api.children_requested, ["100"])
        self.assertEqual(result["photos"][1]["alt"], "Player with the ball")
        state = self.file_state()
        manifest_mtime = (self.root / "data/instagram-photos.json").stat().st_mtime_ns
        self.run_sync(api)
        self.assertEqual(len(self.downloads), 2)
        self.assertEqual(self.file_state(), state)
        self.assertEqual((self.root / "data/instagram-photos.json").stat().st_mtime_ns, manifest_mtime)
        saved = (self.root / "data/instagram-photos.json").read_text()
        self.assertNotIn("media_url", saved)
        self.assertNotIn("signed.jpg", saved)
        self.assertNotIn("TOKEN", saved)
        self.assertIn('<figure id="curated"><img src="existing.jpg"></figure>', (self.root / "photography.html").read_text())

    def test_child_overrides_split_a_carousel_and_parent_skip_wins(self):
        self.set_config({"100": "basketball", "201": "hockey", "202": "skip"})
        api = FakeAPI([image("100", media_type="CAROUSEL_ALBUM")],
                      {"100": [image("201"), image("202"), image("203")]})
        result = self.run_sync(api)
        self.assertEqual({item["id"]: item["category"] for item in result["photos"]}, {"201": "hockey", "203": "basketball"})
        self.set_config({"100": "skip", "201": "hockey"})
        api.children_requested = []
        result = self.run_sync(api)
        self.assertEqual(result["photos"], [])
        self.assertEqual(api.children_requested, [])
        self.assertTrue((self.root / f"{sync.ASSET_DIRECTORY}/201.jpg").is_file())

    def test_unknown_photos_are_pending_and_never_default_to_personal(self):
        self.set_manifest([photo()])
        result = self.run_sync(FakeAPI([image(caption="An amazing evening")]))
        self.assertEqual(result["photos"], [])
        self.assertEqual(result["pending"][0]["id"], "101")
        self.assertTrue(result["pending"][0]["reason"])
        self.assertEqual(self.downloads, [])
        self.assertTrue((self.root / f"{sync.ASSET_DIRECTORY}/101.jpg").is_file())
        self.set_config({"101": "personal"})
        result = self.run_sync(FakeAPI([image(caption="An amazing evening")]))
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["photos"][0]["category"], "personal")

    def test_missing_caption_uses_child_alt_and_empty_caption_is_safe(self):
        api = FakeAPI([image("100", caption="", media_type="CAROUSEL_ALBUM")],
                      {"100": [image("201", alt_text="Hockey player skating"), image("202", alt_text="An athlete")]})
        result = self.run_sync(api)
        self.assertEqual(result["photos"][0]["category"], "hockey")
        self.assertEqual(result["photos"][0]["alt"], "Hockey player skating")
        self.assertEqual(result["pending"][0]["id"], "202")
        self.assertEqual(result["pending"][0]["post_id"], "100")
        self.assertEqual(result["pending"][0]["caption"], "An athlete")

    def test_child_alt_only_resolves_unknown_captions_not_explicit_conflicts(self):
        result = self.run_sync(FakeAPI([image(caption="What a night!", alt_text="Basketball player dunking")]))
        self.assertEqual(result["photos"][0]["category"], "basketball")
        result = self.run_sync(FakeAPI([image(caption="basketball and hockey", alt_text="Basketball player dunking")]))
        self.assertEqual(result["photos"], [])
        self.assertEqual(len(result["pending"]), 1)

    def test_parent_skip_clears_pending_carousel_children(self):
        self.set_manifest(pending=[{"id": "201", "post_id": "100", "caption": "An athlete", "permalink": LINK,
                                    "reason": sync.UNKNOWN_CATEGORY_REASON}])
        self.set_config({"100": "skip"})
        result = self.run_sync(FakeAPI([image("100", media_type="CAROUSEL_ALBUM")]))
        self.assertEqual(result["pending"], [])

    def test_history_is_retained_when_api_does_not_return_it(self):
        self.set_manifest([photo("90")])
        result = self.run_sync(FakeAPI([image("101")]))
        self.assertEqual({item["id"] for item in result["photos"]}, {"90", "101"})

    def test_caption_category_and_alt_updates_without_redownload(self):
        self.set_manifest([photo()])
        result = self.run_sync(FakeAPI([image(caption="#portfolio_hockey", alt_text="Updated hockey alt")]))
        self.assertEqual(result["photos"][0]["category"], "hockey")
        self.assertEqual(result["photos"][0]["alt"], "Updated hockey alt")
        self.assertEqual(self.downloads, [])

    def test_api_or_download_failure_preserves_all_files(self):
        original = self.file_state()
        api = FakeAPI()
        api.media = lambda _: (_ for _ in ()).throw(sync.SyncError("API failed"))
        with self.assertRaises(sync.SyncError):
            self.run_sync(api)
        self.assertEqual(self.file_state(), original)
        calls = []
        def fail_second(data, path):
            calls.append(path)
            if len(calls) == 2:
                raise sync.SyncError("Image failed")
            return self.optimizer(data, path)
        with self.assertRaises(sync.SyncError):
            self.run_sync(FakeAPI([image("101"), image("102")]), optimizer=fail_second)
        self.assertEqual(self.file_state(), original)
        def failed_download(url):
            raise sync.SyncError("An Instagram image could not be downloaded (HTTP 503 after 3 attempts).")
        with self.assertRaisesRegex(sync.SyncError, "Instagram media 101:.*HTTP 503"):
            sync.run(self.root, token="TOKEN", api=FakeAPI([image("101")]),
                     downloader=failed_download, optimizer=self.optimizer)
        self.assertEqual(self.file_state(), original)

    def test_markers_are_validated_before_network_or_write(self):
        (self.root / "photography.html").write_text(self.html.replace("<!-- instagram:hockey:end -->", ""))
        original = self.file_state()
        api = FakeAPI()
        api.verify_account = lambda _: self.fail("API must not run before marker validation")
        with self.assertRaisesRegex(sync.SyncError, "marker pair"):
            self.run_sync(api)
        self.assertEqual(self.file_state(), original)

    def test_offline_render_preserves_curated_content_and_crlf(self):
        (self.root / "photography.html").write_bytes(page_html("\r\n").encode())
        sync.run(self.root, render_only=True)
        self.assertEqual((self.root / "photography.html").read_bytes(), page_html("\r\n").encode())
        self.set_manifest([photo(alt='Basketball "action" <script>alert(1)</script> & fans')])
        sync.run(self.root, render_only=True)
        rendered = (self.root / "photography.html").read_bytes()
        self.assertIn(b"&quot;action&quot; &lt;script&gt;alert(1)&lt;/script&gt; &amp; fans", rendered)
        self.assertNotIn(b"<script>", rendered)
        self.assertEqual(rendered.count(b"\n"), rendered.count(b"\r\n"))
        state = self.file_state()
        sync.run(self.root, render_only=True)
        self.assertEqual(self.file_state(), state)

    def test_render_routes_orientation_sorts_newest_and_spans_sports_landscapes(self):
        photos = [photo("101", timestamp="2026-10-01T01:00:00Z"), photo("102", width=1800, height=1200),
                  photo("103", category="personal", width=1800, height=1200), photo("104", category="personal")]
        rendered = sync.render_html(self.html, photos)
        basketball = rendered.split("<!-- instagram:basketball:start -->")[1].split("<!-- instagram:basketball:end -->")[0]
        self.assertLess(basketball.index('data-instagram-id="102"'), basketball.index('data-instagram-id="101"'))
        self.assertIn('class="instagram-photo instagram-photo-horizontal"', basketball)
        wide = rendered.split("<!-- instagram:personal-wide:start -->")[1].split("<!-- instagram:personal-wide:end -->")[0]
        vertical = rendered.split("<!-- instagram:personal-vertical:start -->")[1].split("<!-- instagram:personal-vertical:end -->")[0]
        self.assertIn('data-instagram-id="103"', wide)
        self.assertIn('data-instagram-id="104"', vertical)

    def test_no_token_exits_nonzero_with_actionable_message(self):
        stderr = StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
            result = sync.main(["--root", str(self.root)])
        self.assertEqual(result, 1)
        self.assertIn("INSTAGRAM_ACCESS_TOKEN", stderr.getvalue())

    def test_malformed_token_cli_error_never_exposes_credentials(self):
        stderr = StringIO()
        with patch.dict(os.environ, {"INSTAGRAM_ACCESS_TOKEN": "VERY_SECRET\nTOKEN"}), redirect_stderr(stderr):
            result = sync.main(["--root", str(self.root)])
        self.assertEqual(result, 1)
        self.assertNotIn("VERY_SECRET", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertIn("invalid whitespace", stderr.getvalue())

    def test_run_passes_the_configured_login_method_and_account_to_the_api(self):
        self.set_config(login_method="unified", instagram_account_id="42")
        with patch.object(sync, "InstagramAPI", return_value=FakeAPI()) as constructor:
            sync.run(self.root, token="TOKEN")
        constructor.assert_called_once_with("TOKEN", "v27.0", login_method="unified", account_id="42")

    def test_unified_username_or_account_id_mismatch_preserves_all_files(self):
        self.set_config(login_method="unified", instagram_account_id="42")
        original = self.file_state()
        for identity in ({"id": "42", "username": "other_person"}, {"id": "99", "username": "oamekphotography"}):
            calls = []
            def opener(request, timeout):
                calls.append(request)
                return FakeResponse(identity)
            api = sync.InstagramAPI("TOKEN", opener=opener, login_method="unified", account_id="42")
            with self.subTest(identity_case="username" if identity["id"] == "42" else "account ID"):
                with self.assertRaises(sync.SyncError):
                    self.run_sync(api)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.file_state(), original)

    def test_github_actions_emits_escaped_safe_error_annotations(self):
        error = sync.api_error({"error": {"message": "SECRET_TOKEN"}}, 401,
                               "Unified Login on graph.facebook.com for configured Instagram account 42")
        stderr = StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(sync, "run", side_effect=error), redirect_stderr(stderr):
            result = sync.main(["--root", str(self.root)])
        self.assertEqual(result, 1)
        annotation = next(line for line in stderr.getvalue().splitlines() if line.startswith("::error::"))
        self.assertIn("HTTP 401", annotation)
        self.assertIn("Unified Login", annotation)
        self.assertNotIn("SECRET_TOKEN", stderr.getvalue())
        stderr = StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(sync, "run", side_effect=sync.SyncError("10%\r\nRetry")), redirect_stderr(stderr):
            sync.main(["--root", str(self.root)])
        self.assertIn("::error::Instagram sync: 10%25%0D%0ARetry", stderr.getvalue())
        stderr = StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), patch.object(sync, "run", side_effect=error), redirect_stderr(stderr):
            sync.main(["--root", str(self.root)])
        self.assertNotIn("::error::", stderr.getvalue())


class ImageTests(unittest.TestCase):
    def test_real_optimizer_resizes_orients_and_removes_exif(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        image = Image.new("RGB", (2400, 1200), "red")
        exif = Image.Exif()
        exif[274] = 6  # EXIF orientation rotates landscape pixels to portrait.
        exif[270] = "Private camera metadata"
        data = BytesIO()
        image.save(data, "JPEG", exif=exif)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "photo.jpg"
            self.assertEqual(sync.optimize_image(data.getvalue(), output), (900, 1800))
            with Image.open(output) as result:
                self.assertEqual(result.size, (900, 1800))
                self.assertEqual(dict(result.getexif()), {})
                self.assertEqual(result.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
