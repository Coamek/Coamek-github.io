# Instagram photo sync

The importer checks **@oamekphotography every six hours**, at minute 17 UTC, and adds categorized still photos to the portfolio. Each run saves optimized JPEGs in this repository and publishes the static site through GitHub Pages. Visitors do not need Instagram or a login to see the imported photos.

The files alone do not establish an Instagram connection. Complete the setup below and verify a successful first run before treating the sync as active.

## Connect Instagram and enable publishing

1. Use the Creator or Business account for **@oamekphotography**. In the [Meta App Dashboard](https://developers.facebook.com/apps/), select the app's **Instagram API with Unified Login** setup and the business portfolio containing this Instagram account. This setup requires a business portfolio; a Facebook Page and Page permissions are not required. See Meta's [Unified Login setup guide](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started).
2. Under **Generate access tokens**, select **@oamekphotography** and generate its token. The token type for Unified Login is **Business Integration System User Access Token**, also shown as **System-user access token (SUAT)**. The importer needs only **`instagram_basic`**, which reads the account's profile and media. Use a configuration limited to that permission; publishing, messaging, ads, and comment-management permissions are not needed. See [Unified Login permissions](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/permissions).
3. In [this repository's Actions secrets](https://github.com/Coamek/Coamek-github.io/settings/secrets/actions), create or update the existing repository secret named **`INSTAGRAM_ACCESS_TOKEN`** with that token. The secret name stays the same for Unified Login. Enter the token only in GitHub's secret field; keep it out of source files, issue comments, and chat.
4. Merge or push the sync files to **`main`**. In [Settings → Pages](https://github.com/Coamek/Coamek-github.io/settings/pages), select **GitHub Actions** as the build and deployment source. Keep the existing custom domain, `connoroamek.me`.
5. Open [Actions](https://github.com/Coamek/Coamek-github.io/actions), select **Sync Instagram and publish portfolio**, and choose **Run workflow** on `main`. Confirm both the `sync` and `deploy` jobs succeed, then check the live Photography page and `data/instagram-photos.json`.

The current account configuration in `data/instagram-config.json` uses **`"login_method": "unified"`** and Instagram account ID **`17841477718206518`**. Requests go to `graph.facebook.com/v27.0/17841477718206518`, including its `/media` edge. Unified Login's `/me` identifies the system user; the importer uses the configured Instagram account ID to read the profile and photos. To connect a different account later, obtain its ID through [assigned Instagram accounts](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started#instagram-account-ids) and update the account ID and username together.

The workflow needs permission to commit generated photos to `main` and deploy to the `github-pages` environment. Repository rules must allow those actions. It uses GitHub's built-in workflow token; no GitHub personal access token is needed. New site-source pushes also publish through this workflow using already saved photos; they do not contact Instagram or require its secret. Scheduled runs with no changes do not redeploy.

## Token lifetime and connection errors

Unified Login token expiration depends on the configuration selected in Meta's dashboard. Check the **configuration preview's expiration**, then replace the repository secret before that token expires. Reconnect the account and update the secret if the token is revoked, the app is removed from its business portfolio, or the Instagram account moves to another portfolio. The importer does not manage business-portfolio tokens or rewrite GitHub secrets. See Meta's [authentication and token lifetime documentation](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/authentication).

For an HTTP 401 or token-authentication error, check that the secret contains the token generated for this app and account, that `login_method` is `unified`, and that `instagram_account_id` is `17841477718206518`. Unified Login tokens use **`graph.facebook.com`**. A token generated for the separate Instagram Login setup uses **`graph.instagram.com`** and a different permission family; mixing these setups can produce authentication errors. Keep the existing secret name and match its token to the configured login method.

The importer also retains support for an existing Instagram Login integration using `"login_method": "instagram"`, an Instagram User token, and `instagram_business_basic`. Its long-lived tokens have a 60-day lifetime and follow that setup's own [token renewal instructions](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login#long-lived-access-tokens). This legacy option is separate from the current Unified Login configuration.

A missing, expired, or revoked credential makes a scheduled/manual sync fail visibly while the published gallery stays available. Enable GitHub Actions failure notifications for the repository. GitHub may also pause scheduled workflows in public repositories after 60 days without repository activity; re-enable the workflow if that happens.

## How photos are categorized

The importer uses the caption and hashtags. For a reliable category, add exactly one of these hashtags to the Instagram caption:

| Hashtag | Portfolio category |
| --- | --- |
| `#portfolio_basketball` | Basketball Photos |
| `#portfolio_hockey` | Hockey Photos |
| `#portfolio_media_day` | Media Day Photos |
| `#portfolio_personal` | Personal Photos |

Without a portfolio hashtag, clear basketball, hockey, Media Day, or personal/travel/nature keywords can supply the category. Media Day takes precedence over sport keywords. Meta may omit a caption; the importer can then use the image's alternative text. If neither supplies a clear category, the image needs an ID override. Unknown captions, conflicting portfolio hashtags, and mixed sport captions are recorded in **`data/instagram-photos.json` → `pending`** for review and are not added to a guessed category. The script does not use image recognition.

To resolve a post or set an exception, edit **`data/instagram-config.json` → `category_overrides`**. Use an Instagram media ID from the saved manifest and one of `basketball`, `hockey`, `media-day`, `personal`, or `skip`:

```json
{
  "category_overrides": {
    "REPLACE_WITH_POST_ID": "basketball",
    "REPLACE_WITH_CHILD_IMAGE_ID": "media-day",
    "REPLACE_WITH_ANOTHER_POST_ID": "skip"
  }
}
```

Preserve the config's other fields. A child image override can put one slide of a carousel into a different category. A post-level `skip` excludes its whole carousel. Commit the config change and run the workflow again. Do not put access tokens in this file.

## Import behavior

- The "Find a game" index beside the Photography title is rebuilt from the rendered game headings on every sync, including offline renders. It includes imported and curated games, preserves combined hockey-weekend dates, and uses normal anchor links that also work without JavaScript. Its list scrolls within the introduction's height on desktop so additional games do not create blank space beneath the introductory text. On smaller screens it appears beneath the introduction with a compact scrollable list.
- Landscape photographs share rows with other photos and are limited to the width of two portrait columns on larger screens. The same layout applies to future imports; full-resolution image files and their proportions are preserved.
- Photos within each post follow the carousel sequence returned by Instagram, saved as zero-based `carousel_position` values. Media IDs are identifiers and are never used to guess slide order. Existing posts acquire these positions on the next authenticated sync; until then, their saved sequence stays stable. Newer posts appear first, and a split slide keeps its top and bottom photographs together at that slide's position. Favorite-photo selections do not override the Instagram sequence.
- Basketball and hockey photos are grouped by game, with an opponent heading and the game date. The caption format `vs. Denver - 11/2/26` (or `at Denver - 11-2-2026`) supplies the event details for future imports. The posting date is never displayed as the game date. Multiple posts with the same game title and date share a group. Hockey games against the same opponent on Thursday through Sunday of the same week share one weekend group showing both dates, including weekends crossing a month or year boundary. Different weekends remain separate. Groups appear newest game first, and each keeps its own balanced photo rows.
- `game_overrides` in `data/instagram-config.json` maps a post ID to `{ "title": "Wisconsin vs. Denver", "date": "2026-11-02" }` when a caption needs correction or lacks a game date. The current labels were checked against Wisconsin's official [2026–27 men's basketball schedule](https://uwbadgers.com/sports/mens-basketball/schedule/2026-27) and [2026–27 women's hockey schedule](https://uwbadgers.com/sports/womens-ice-hockey/schedule/2026-27). Curated UW–Parkside photos are from October 10, 2026; the curated Boston University weekend set combines October 1–2, 2026.
- Single-image posts and every still image in a carousel can be imported. Videos, Reels, Stories, and video slides are skipped.
- Existing hand-curated gallery photos stay in place. Synced images appear in separate generated portions of their matching sections, newest first. Landscape Personal photos are placed in the wide gallery.
- Instagram media IDs prevent the same imported image from being added on every run. The initial import cannot identify every matching photo already hand-added to the portfolio; use `skip` overrides for historical posts that repeat those photos.
- Regular gallery previews are resized to a maximum edge of 1800 pixels. When a stacked pair is detected, the importer first saves a metadata-free PNG of the full-resolution image supplied by Instagram, before any resizing or JPEG recompression. The two landscape photographs are cropped from that source and saved as separate lossless PNGs at their full available dimensions. There is no upscaling. The cached `-source.png` stays in the repository; only the individual photographs are packaged for the public site. Signed Instagram CDN links and credentials are never saved in the manifest.
- Stacked-pair detection runs automatically on new downloads and on previously saved images, including during `--render-only`. It looks for a strong, abrupt horizontal scene change near the center of a portrait image, with landscape proportions on both sides. It recognized all four confirmed stacked slides in the current collection, including the two Denver slides, without splitting the remaining imported photos. This is conservative pixel analysis, not semantic image recognition; unclear or off-center compositions can need an override.
- `image_splits` remains available for exceptions: its `at` fraction gives the dividing line measured from the top, and optional `alt_top` and `alt_bottom` describe the two photographs. Set an image ID to `false` to keep an ordinary portrait whole. Saved split positions persist across later imports, and missing crop files are restored automatically. Existing composites first split offline use their saved preview pixels; the next authenticated sync downloads their full-resolution Instagram source once and upgrades the crops while preserving the existing preview. Cached full-resolution sources are then reused on later syncs.
- Posts absent from a later API response do not automatically delete saved portfolio photos. The sync is additive. Review the saved manifest when removing old work.
- The workflow explicitly deploys Pages after saving photos because commits made with `GITHUB_TOKEN` do not trigger GitHub's usual branch-based Pages build. The packaging step includes only public site files and referenced assets; it leaves original design/photo source files, scripts, setup docs, and import metadata out of the deployed artifact.

## Local checks

Use Python 3.11 or newer in a virtual environment:

```sh
python3 -m venv /tmp/portfolio-instagram-venv
/tmp/portfolio-instagram-venv/bin/python -m pip install -r scripts/requirements-instagram.txt
/tmp/portfolio-instagram-venv/bin/python -m unittest discover -s scripts -p 'test_*.py'
/tmp/portfolio-instagram-venv/bin/python scripts/sync_instagram.py --render-only
/tmp/portfolio-instagram-venv/bin/python scripts/package_pages.py --output /tmp/portfolio-public-site
```

`--render-only` regenerates the saved Instagram portions of the gallery without network access or a token. Packaging requires an empty output directory. The separate **Check Instagram importer** workflow runs offline tests for pull requests without Instagram credentials or write permissions.
