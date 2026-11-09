# Instagram photo sync

The importer checks **@oamekphotography every six hours**, at minute 17 UTC, and adds categorized still photos to the portfolio. Each run saves optimized JPEGs in this repository and publishes the static site through GitHub Pages. Visitors do not need Instagram or a login to see the imported photos.

The files alone do not establish an Instagram connection. Complete the setup below and verify a successful first run before treating the sync as active.

## Connect Instagram and enable publishing

1. Use the Creator or Business account for **@oamekphotography**. In the [Meta App Dashboard](https://developers.facebook.com/apps/), create or select an app with **Instagram API with Instagram Login**, add this Instagram account, and authorize the read permission **`instagram_business_basic`**. The [official setup guide](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/get-started) describes account roles and token generation. A linked Facebook Page is not required for this API.
2. Generate a **long-lived Instagram User access token** for this account. Dashboard-generated tokens are already long-lived; a token from a custom login flow may need to be exchanged. Follow Meta's [token instructions](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login#long-lived-access-tokens). Do not grant publishing, messaging, or comment permissions for this importer.
3. In [this repository's Actions secrets](https://github.com/Coamek/Coamek-github.io/settings/secrets/actions), create a repository secret named **`INSTAGRAM_ACCESS_TOKEN`** containing that token. Enter the token only in GitHub's secret field; keep it out of source files, issue comments, and chat.
4. Merge or push the sync files to **`main`**. In [Settings → Pages](https://github.com/Coamek/Coamek-github.io/settings/pages), select **GitHub Actions** as the build and deployment source. Keep the existing custom domain, `connoroamek.me`.
5. Open [Actions](https://github.com/Coamek/Coamek-github.io/actions), select **Sync Instagram and publish portfolio**, and choose **Run workflow** on `main`. Confirm both the `sync` and `deploy` jobs succeed, then check the live Photography page and `data/instagram-photos.json`.

The workflow needs permission to commit generated photos to `main` and deploy to the `github-pages` environment. Repository rules must allow those actions. It uses GitHub's built-in workflow token; no GitHub personal access token is needed. New site-source pushes also publish through this workflow using already saved photos; they do not contact Instagram or require its secret. Scheduled runs with no changes do not redeploy.

Long-lived Instagram tokens expire after about **60 days**. Renew the token and replace the repository secret before it expires. This setup does not store a refreshed token in the public repository or ask for permission to rewrite repository secrets. Meta's [refresh endpoint](https://developers.facebook.com/documentation/instagram-platform/reference/refresh_access_token.md) returns a new access token; refreshing it locally does not update the GitHub secret. A missing, expired, or revoked credential makes a scheduled/manual sync fail visibly while the published gallery stays available. Enable GitHub Actions failure notifications for the repository. GitHub may also pause scheduled workflows in public repositories after 60 days without repository activity; re-enable the workflow if that happens.

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

- Single-image posts and every still image in a carousel can be imported. Videos, Reels, Stories, and video slides are skipped.
- Existing hand-curated gallery photos stay in place. Synced images appear in separate generated portions of their matching sections, newest first. Landscape Personal photos are placed in the wide gallery.
- Instagram media IDs prevent the same imported image from being added on every run. The initial import cannot identify every matching photo already hand-added to the portfolio; use `skip` overrides for historical posts that repeat those photos.
- Images are downloaded, resized to a maximum edge of 1800 pixels, and saved locally with dimensions and lazy loading. Signed Instagram CDN links and credentials are not stored in the manifest or served to visitors.
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
