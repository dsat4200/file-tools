"""Standalone file operations, independent of Dolphin's source/build."""

import argparse
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import sys

from PIL import Image, ImageOps


KRENAME = "@krename@"
PRESETS = {
    "Small": (854, 480),
    "Medium": (1366, 768),
    "Large": (1920, 1080),
    "Phone": (320, 568),
}
FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF", "GIF", "AVIF"}


def local_paths(paths):
    # Do not resolve symlinks: outputs belong beside the selected directory entry.
    return list(dict.fromkeys(Path(os.path.abspath(path)) for path in paths))


class CancelledError(Exception):
    pass


def render_pdf(path, work, dpi, progress=None, on_process=None, cancelled=None):
    # MuPDF is isolated from Qt/Python threads, and can be terminated immediately
    # on cancellation even when a single complex page is still rendering.
    command = [sys.executable, "-u", str(Path(__file__).with_name("pdf_renderer.py")),
               str(dpi), str(path), str(work)]
    errors = ""
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace", bufsize=1) as process:
        try:
            if on_process:
                on_process(process)
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    errors = (errors + line)[-2000:]
                    continue
                if isinstance(event, dict) and event.get("event") == "page" and progress:
                    progress(f"Page {event['page']} of {event['total']} — {path.name}")
            status = process.wait()
            if cancelled and cancelled():
                raise CancelledError("Extraction cancelled")
            if status:
                raise ValueError(errors.strip() or "MuPDF conversion failed")
        finally:
            if on_process:
                on_process(None)


def extract_pdf(path, dpi=300, progress=None, on_process=None, cancelled=None):
    if dpi not in {150, 200, 300}:
        raise ValueError("Choose 150, 200 or 300 DPI")
    path = local_paths([path])[0]
    if not path.is_file():
        raise ValueError("Not a readable local file")
    name = path.stem if path.suffix.lower() == ".pdf" else path.name + " (pages)"
    destination = path.with_name(name)
    # Reserve the exact requested name, including against dangling symlinks.
    destination.mkdir()
    published = []
    try:
        # Failed/encrypted PDFs must not leave a folder full of partial pages.
        with tempfile.TemporaryDirectory(prefix=".render-", dir=destination) as work:
            render_pdf(path, work, dpi, progress, on_process, cancelled)
            pages = []
            for page in Path(work).iterdir():
                match = re.fullmatch(r"page-(\d+)\.png", page.name)
                if match:
                    pages.append((int(match[1]), page))
            pages.sort()
            if not pages:
                raise ValueError("The PDF did not contain any renderable pages")
            for number, page in pages:
                if cancelled and cancelled():
                    raise CancelledError("Extraction cancelled")
                target = destination / f"page-{number:03d}.png"
                try:
                    # Atomic, no-overwrite publication without rereading or
                    # copying every PNG. Both entries are on the same filesystem.
                    os.link(page, target)
                    published.append(target)
                except OSError as error:
                    if error.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV}:
                        raise
                    # FAT/exFAT and some network shares have no hard links.
                    with target.open("xb") as output:
                        published.append(target)
                        with page.open("rb") as source:
                            shutil.copyfileobj(source, output)
                page.unlink()
        return destination
    except Exception:
        for page in published:
            page.unlink(missing_ok=True)
        try:
            destination.rmdir()
        except OSError:
            pass  # Never remove anything another process added to this directory.
        raise


@dataclass(frozen=True)
class ResizeOptions:
    width: int
    height: int
    mode: str = "Fit"
    percent: bool = False
    shrink_only: bool = True
    match_orientation: bool = True
    label: str = "Custom"
    quality: int = 90
    strip_metadata: bool = False

    def __post_init__(self):
        if not (1 <= self.width <= 50000 and 1 <= self.height <= 50000):
            raise ValueError("Width and height must be between 1 and 50,000")
        if self.mode not in {"Fit", "Fill", "Stretch"}:
            raise ValueError("Unknown resize mode")
        if not 1 <= self.quality <= 100:
            raise ValueError("Quality must be between 1 and 100")
        if self.label not in {*PRESETS, "Custom"}:
            raise ValueError("Unknown size preset")


def resized_pixels(image, options):
    width, height = options.width, options.height
    if options.percent:
        width = max(1, round(image.width * width / 100))
        height = max(1, round(image.height * height / 100))
    elif options.match_orientation and (image.width < image.height) != (width < height):
        width, height = height, width

    if options.percent or options.mode == "Stretch":
        if options.shrink_only:
            width, height = min(width, image.width), min(height, image.height)
        return image.resize((width, height), Image.Resampling.LANCZOS)
    if options.mode == "Fill":
        if options.shrink_only:
            scale = min(1, image.width / width, image.height / height)
            width, height = max(1, round(width * scale)), max(1, round(height * scale))
        return ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS)
    scale = min(width / image.width, height / image.height)
    if options.shrink_only:
        scale = min(1, scale)
    size = max(1, round(image.width * scale)), max(1, round(image.height * scale))
    return image.resize(size, Image.Resampling.LANCZOS)


def resize_image(path, options):
    path = local_paths([path])[0]
    with Image.open(path) as source:
        image_format = source.format
        if image_format not in FORMATS:
            raise ValueError(f"Unsupported image format: {image_format}")
        if getattr(source, "n_frames", 1) != 1:
            raise ValueError("Animated/multipage images are skipped, not flattened")
        # Camera orientation must be applied before computing the output size.
        image = ImageOps.exif_transpose(source)
        resized = resized_pixels(image, options)
        metadata = {}
        if not options.strip_metadata:
            for key in ("icc_profile", "dpi"):
                if source.info.get(key) is not None:
                    metadata[key] = source.info[key]
            exif = image.getexif()
            if exif and image_format in {"JPEG", "PNG", "WEBP", "TIFF", "AVIF"}:
                for tag, value in ((256, resized.width), (257, resized.height)):
                    if tag in exif:
                        exif[tag] = value
                metadata["exif"] = exif.tobytes()
        if image_format in {"JPEG", "WEBP", "AVIF"}:
            metadata["quality"] = options.quality
        if image_format in {"PNG", "GIF"} and "transparency" in image.info:
            metadata["transparency"] = image.info["transparency"]
        # Some Pillow encoders fall back to image.info even when no metadata
        # arguments are supplied. Clear it so "Remove metadata" actually does so.
        resized.info.clear()
        if image_format == "JPEG" and resized.mode not in {"L", "RGB", "CMYK"}:
            resized = resized.convert("RGB")

        label = options.label
        if label == "Custom":
            unit = "%" if options.percent else ""
            label = f"{options.width}x{options.height}{unit}"
        suffix = path.suffix or {"JPEG": ".jpg", "TIFF": ".tiff"}.get(
            image_format, "." + image_format.lower()
        )
        for number in range(1, 100001):
            extra = "" if number == 1 else f" ({number})"
            target = path.with_name(f"{path.stem} ({label}){extra}{suffix}")
            try:
                output = target.open("xb")
            except FileExistsError:
                continue
            try:
                with output:
                    resized.save(output, format=image_format, **metadata)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return target
        raise ValueError("Too many existing resized copies")


def main(argv=None):
    parser = argparse.ArgumentParser(description="PDF extraction, batch rename and image resizing")
    parser.add_argument("action", choices=("pdf", "resize", "rename"), nargs="?")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    if not args.action:
        from file_tools_gui import launch
        return launch()
    if not args.files:
        parser.error("Select at least one local file or folder")
    paths = local_paths(args.files)
    if args.action == "rename":
        # KDE handles preview, regex, numbering, conflicts and undo. No --start:
        # selecting a menu entry must never rename anything without confirmation.
        os.execv(KRENAME, [KRENAME, "--", *map(str, paths)])
    else:
        from file_tools_gui import run

        return run(args.action, paths)


if __name__ == "__main__":
    raise SystemExit(main())
