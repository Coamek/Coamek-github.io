#!/usr/bin/env python3
"""Package this buildless portfolio without publishing source photos or tooling."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import unquote, urlsplit


class AssetReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.required: set[str] = set()
        self.optional: set[str] = set()

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        for key, value in attributes:
            if not value:
                continue
            if key == "srcset":
                self.required.update(part.strip().split()[0] for part in value.split(",") if part.strip())
            elif key in {"src", "href", "poster", "content"}:
                self.required.add(value)
            elif key == "data-thumbnail-src":
                # These exports intentionally fall back to existing collage previews.
                self.optional.add(value)


def local_asset(reference: str) -> Path | None:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path).removeprefix("./").removeprefix("/")
    if not path.startswith("assets/"):
        return None
    parts = PurePosixPath(path).parts
    if ".." in parts:
        raise ValueError("An asset reference escapes the assets directory")
    return Path(*parts)


def checked_path(source: Path, relative: Path) -> Path:
    path = source / relative
    if any(parent.is_symlink() for parent in [path, *path.parents] if parent != source.parent):
        raise ValueError(f"Symlink assets are not supported: {relative}")
    if not path.resolve().is_relative_to(source):
        raise ValueError("An asset reference escapes the source directory")
    return path


def package_site(source: Path, output: Path) -> tuple[int, int]:
    source = source.resolve()
    output = output.resolve()
    if output == source or source.is_relative_to(output):
        raise ValueError("The output must be separate from the source checkout")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    if not (source / "index.html").is_file():
        raise ValueError("The portfolio source has no index.html")

    files = {
        path.relative_to(source)
        for pattern in ("*.html", "*.css", "*.js", "favicon*", "social-preview*", "CNAME")
        for path in source.glob(pattern)
        if path.is_file()
    }
    required: set[str] = set()
    optional: set[str] = set()
    dynamic: set[Path] = set()
    for relative in list(files):
        path = source / relative
        if path.suffix not in {".html", ".css", ".js"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            parser = AssetReferences()
            parser.feed(text)
            required.update(parser.required)
            optional.update(parser.optional)
        if path.suffix in {".css", ".html"}:
            required.update(match[1].strip() for match in re.findall(r"url\(\s*(['\"]?)(.*?)\1\s*\)", text))
        if path.suffix == ".js":
            required.update(match[1] for match in re.findall(r"(['\"])(assets/[^'\"\n]+)\1", text))
            for reference in re.findall(r"`(assets/[^`]+)`", text):
                if "${" in reference:
                    prefix = reference.split("${", 1)[0]
                    directory = local_asset(prefix.rsplit("/", 1)[0] + "/")
                    if directory:
                        dynamic.add(directory)
                else:
                    required.add(reference)

    missing = []
    for reference in required | optional:
        relative = local_asset(reference)
        if relative is None:
            continue
        path = checked_path(source, relative)
        if path.is_file():
            files.add(relative)
        elif reference in required:
            missing.append(str(relative))
    for relative in dynamic:
        directory = checked_path(source, relative)
        if not directory.is_dir():
            missing.append(str(relative))
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                checked_path(source, path.relative_to(source))
                files.add(path.relative_to(source))
    if missing:
        raise ValueError("Required site assets are missing: " + ", ".join(sorted(set(missing))))

    output.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for relative in sorted(files):
        origin = checked_path(source, relative)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        total_bytes += origin.stat().st_size
    (output / ".nojekyll").touch()
    return len(files) + 1, total_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        count, size = package_site(arguments.source, arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Pages packaging failed: {error}\n")
    print(f"Packaged {count} public files ({size / 1024 / 1024:.1f} MiB).")


if __name__ == "__main__":
    main()
