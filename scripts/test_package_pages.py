from pathlib import Path
import tempfile
import unittest

from package_pages import package_site


class PackagePagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source"
        self.output = Path(self.temporary.name) / "output"
        self.source.mkdir()

    def write(self, name, value="asset"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def test_packages_required_assets_dynamic_slides_and_optional_exports(self):
        self.write("index.html", '<img src="assets/photo%20one.jpg" srcset="assets/photo-small.jpg 600w, assets/photo%20one.jpg 1200w"><video src="assets/movie.mp4" poster="assets/poster.jpg"></video><img data-thumbnail-src="assets/optional.jpg"><img data-thumbnail-src="assets/missing-optional.jpg">')
        self.write("style.css", '@font-face {src: url("assets/fonts/font.otf")}')
        self.write("script.js", 'image.src = `assets/slides/slide-${number}.png`;')
        self.write("CNAME", "example.com")
        for filename in ["photo one.jpg", "photo-small.jpg", "movie.mp4", "poster.jpg", "optional.jpg", "fonts/font.otf", "slides/slide-01.png", "slides/slide-20.png"]:
            self.write("assets/" + filename)
        for filename in ["assets/original.psd", ".git/config", "data/instagram-photos.json", "docs/instagram-sync.md", "scripts/private.py"]:
            self.write(filename)

        package_site(self.source, self.output)

        self.assertTrue((self.output / "assets/photo one.jpg").is_file())
        self.assertTrue((self.output / "assets/slides/slide-20.png").is_file())
        self.assertTrue((self.output / "assets/movie.mp4").is_file())
        self.assertTrue((self.output / "CNAME").is_file())
        self.assertTrue((self.output / ".nojekyll").is_file())
        self.assertFalse((self.output / "assets/original.psd").exists())
        for directory in [".git", "data", "docs", "scripts"]:
            self.assertFalse((self.output / directory).exists())

    def test_missing_required_asset_fails_before_creating_output(self):
        self.write("index.html", '<img src="assets/missing.jpg" data-thumbnail-src="assets/missing.jpg">')
        with self.assertRaisesRegex(ValueError, "Required site assets"):
            package_site(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_asset_path_cannot_escape_and_output_cannot_overwrite_source(self):
        self.write("index.html", '<img src="assets/../../private.jpg">')
        with self.assertRaisesRegex(ValueError, "escapes"):
            package_site(self.source, self.output)
        with self.assertRaisesRegex(ValueError, "separate"):
            package_site(self.source, self.source)

    def test_source_symlink_is_rejected(self):
        self.write("index.html", '<img src="assets/photo.jpg">')
        self.write("private.jpg")
        (self.source / "assets").mkdir()
        (self.source / "assets/photo.jpg").symlink_to(self.source / "private.jpg")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            package_site(self.source, self.output)


if __name__ == "__main__":
    unittest.main()
