import configparser
import errno
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import file_tools as tools


class FileTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def picture(self, name="photo.png", size=(120, 60), **kwargs):
        path = self.root / name
        Image.new("RGB", size, "red").save(path, **kwargs)
        return path

    def assertSize(self, path, size):
        with Image.open(path) as image:
            self.assertEqual(image.size, size)


class PdfTests(FileTest):
    def pdf(self, name="a document.pdf"):
        path = self.root / name
        pages = [Image.new("RGB", (24, 12), color) for color in ("red", "blue")]
        pages[0].save(path, format="PDF", save_all=True, append_images=pages[1:], resolution=72)
        return path

    def test_real_multipage_pdf_render_and_names(self):
        path = self.pdf("quotes ' $; ü.PDF")
        original = path.read_bytes()
        output = tools.extract_pdf(path)
        self.assertEqual(output, self.root / "quotes ' $; ü")
        self.assertEqual(sorted(p.name for p in output.iterdir()), ["page-001.png", "page-002.png"])
        self.assertSize(output / "page-001.png", (100, 50))
        with Image.open(output / "page-001.png") as red, Image.open(output / "page-002.png") as blue:
            self.assertGreater(red.getpixel((10, 10))[0], 200)
            self.assertGreater(blue.getpixel((10, 10))[2], 200)
        self.assertEqual(path.read_bytes(), original)

    def test_existing_folder_is_never_modified(self):
        path = self.pdf()
        destination = path.with_suffix("")
        destination.mkdir()
        sentinel = destination / "important.txt"
        sentinel.write_text("keep me")
        with self.assertRaises(FileExistsError):
            tools.extract_pdf(path)
        self.assertEqual(sentinel.read_text(), "keep me")
        self.assertEqual(list(destination.iterdir()), [sentinel])

    def test_existing_file_and_dangling_symlink_are_not_overwritten(self):
        for symlink in (False, True):
            path = self.pdf(f"document-{symlink}.pdf")
            destination = path.with_suffix("")
            if symlink:
                destination.symlink_to(self.root / "missing")
            else:
                destination.write_text("existing")
            with self.assertRaises(FileExistsError):
                tools.extract_pdf(path)
            self.assertTrue(os.path.lexists(destination))

    def test_invalid_pdf_removes_partial_output(self):
        path = self.root / "broken.pdf"
        path.write_text("not a PDF")
        with self.assertRaisesRegex(ValueError, "PDF"):
            tools.extract_pdf(path)
        self.assertFalse(path.with_suffix("").exists())

    def test_partial_renderer_failure_is_cleaned(self):
        path = self.pdf()

        def fail(path, work, *args):
            (Path(work) / "page-001.png").write_bytes(b"partial")
            raise ValueError("render failed")

        with patch.object(tools, "render_pdf", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "render failed"):
                tools.extract_pdf(path)
        self.assertFalse(path.with_suffix("").exists())

    def test_password_protected_pdf_reports_error_and_cleans_up(self):
        source = self.pdf()
        encrypted = self.root / "locked.pdf"
        subprocess.run(["qpdf", "--encrypt", "user", "owner", "256", "--",
                        str(source), str(encrypted)], check=True)
        with self.assertRaisesRegex(ValueError, "password"):
            tools.extract_pdf(encrypted)
        self.assertFalse(encrypted.with_suffix("").exists())

    def test_extensionless_pdf_does_not_conflict_with_itself(self):
        path = self.pdf("manual")
        self.assertEqual(tools.extract_pdf(path).name, "manual (pages)")

    def test_pdf_symlink_outputs_beside_selected_entry(self):
        source = self.pdf()
        folder = self.root / "elsewhere"
        folder.mkdir()
        link = folder / "selected.pdf"
        link.symlink_to(source)
        self.assertEqual(tools.extract_pdf(link), folder / "selected")

    def test_mupdf_emits_page_progress(self):
        progress = []
        tools.extract_pdf(self.pdf(), progress=progress.append)
        self.assertEqual(len(progress), 2)
        self.assertIn("Page 1 of 2", progress[0])
        self.assertIn("Page 2 of 2", progress[1])

    def test_native_publication_does_not_copy_png_bytes(self):
        with patch.object(tools.shutil, "copyfileobj", side_effect=AssertionError("extra copy")):
            output = tools.extract_pdf(self.pdf())
        self.assertEqual(len(list(output.iterdir())), 2)

    def test_no_link_filesystem_falls_back_to_exclusive_copy(self):
        with patch.object(tools.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "no links")):
            output = tools.extract_pdf(self.pdf())
        self.assertSize(output / "page-001.png", (100, 50))

    def test_cancellation_stops_only_the_owned_renderer_and_cleans_up(self):
        path = self.pdf()
        state = {"cancelled": False, "process": None}

        def watch(process):
            if process is not None:
                state["process"] = process
                state["cancelled"] = True
                process.terminate()

        with self.assertRaises(tools.CancelledError):
            tools.extract_pdf(path, on_process=watch, cancelled=lambda: state["cancelled"])
        self.assertIsNotNone(state["process"].poll())
        self.assertFalse(path.with_suffix("").exists())

    def test_faster_resolution_choices(self):
        for dpi, size in ((150, (50, 25)), (200, (67, 34))):
            with self.subTest(dpi=dpi):
                path = self.pdf(f"quality-{dpi}.pdf")
                output = tools.extract_pdf(path, dpi=dpi)
                self.assertSize(output / "page-001.png", size)


class ResizeTests(FileTest):
    def test_fit_preserves_original_and_aspect_ratio(self):
        source = self.picture()
        original = source.read_bytes()
        output = tools.resize_image(source, tools.ResizeOptions(80, 80))
        self.assertSize(output, (80, 40))
        self.assertEqual(output.name, "photo (80x80).png")
        self.assertEqual(source.read_bytes(), original)

    def test_fill_crops_and_stretch_changes_aspect_ratio(self):
        source = self.picture()
        for mode in ("Fill", "Stretch"):
            output = tools.resize_image(source, tools.ResizeOptions(40, 40, mode=mode))
            self.assertSize(output, (40, 40))

    def test_shrink_only_for_all_modes(self):
        source = self.picture(size=(20, 10))
        for mode, expected in (("Fit", (20, 10)), ("Fill", (10, 10)), ("Stretch", (20, 10))):
            output = tools.resize_image(source, tools.ResizeOptions(100, 100, mode=mode))
            self.assertSize(output, expected)

    def test_upscaling_is_explicit(self):
        source = self.picture(size=(20, 10))
        output = tools.resize_image(source, tools.ResizeOptions(100, 100, shrink_only=False))
        self.assertSize(output, (100, 50))

    def test_percentages(self):
        output = tools.resize_image(self.picture(), tools.ResizeOptions(50, 25, percent=True))
        self.assertSize(output, (60, 15))
        self.assertEqual(output.name, "photo (50x25%).png")

    def test_portrait_orientation_matching_can_be_disabled(self):
        source = self.picture(size=(60, 120))
        output = tools.resize_image(source, tools.ResizeOptions(80, 40))
        self.assertSize(output, (40, 80))
        output = tools.resize_image(source, tools.ResizeOptions(80, 40, match_orientation=False))
        self.assertSize(output, (20, 40))

    def test_preset_names_and_existing_copies_are_preserved(self):
        source = self.picture("space ' ü.png")
        options = tools.ResizeOptions(854, 480, label="Small")
        first = tools.resize_image(source, options)
        original = first.read_bytes()
        second = tools.resize_image(source, options)
        self.assertEqual(first.name, "space ' ü (Small).png")
        self.assertEqual(second.name, "space ' ü (Small) (2).png")
        self.assertEqual(first.read_bytes(), original)

    def test_dangling_destination_symlink_is_not_followed(self):
        source = self.picture()
        target = self.root / "photo (20x20).png"
        target.symlink_to(self.root / "missing.png")
        result = tools.resize_image(source, tools.ResizeOptions(20, 20))
        self.assertTrue(target.is_symlink())
        self.assertFalse((self.root / "missing.png").exists())
        self.assertEqual(result.name, "photo (20x20) (2).png")

    def test_exif_rotation_and_metadata_removal(self):
        exif = Image.Exif()
        exif[274] = 6  # 90-degree camera rotation
        exif[315] = "Camera owner"
        source = self.picture("camera.jpg", exif=exif)
        for strip in (False, True):
            result = tools.resize_image(source, tools.ResizeOptions(40, 80, strip_metadata=strip))
            self.assertSize(result, (40, 80))
            with Image.open(result) as image:
                self.assertNotIn(274, image.getexif())
                self.assertEqual(image.getexif().get(315), None if strip else "Camera owner")

    def test_transparent_png_stays_transparent(self):
        source = self.root / "alpha.png"
        Image.new("RGBA", (50, 50), (0, 255, 0, 100)).save(source)
        result = tools.resize_image(source, tools.ResizeOptions(20, 20))
        with Image.open(result) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((5, 5))[3], 100)

    def test_png_metadata_is_removed_even_if_pillow_would_inherit_it(self):
        source = self.root / "private.png"
        exif = Image.Exif()
        exif[315] = "Private artist"
        Image.new("RGB", (40, 40), "blue").save(source, exif=exif, icc_profile=b"profile")
        result = tools.resize_image(source, tools.ResizeOptions(20, 20, strip_metadata=True))
        with Image.open(result) as image:
            self.assertFalse(image.getexif())
            self.assertNotIn("icc_profile", image.info)

    def test_indexed_transparency_survives_metadata_removal(self):
        source = self.root / "indexed.png"
        image = Image.new("P", (40, 40), 0)
        image.putpalette([255, 0, 0] + [0, 0, 0] * 255)
        image.save(source, transparency=0)
        result = tools.resize_image(source, tools.ResizeOptions(20, 20, strip_metadata=True))
        with Image.open(result) as image:
            self.assertEqual(image.convert("RGBA").getpixel((10, 10))[3], 0)

    def test_supported_formats_keep_their_format(self):
        Image.init()
        for extension, expected in (("jpg", "JPEG"), ("webp", "WEBP"), ("bmp", "BMP"),
                                    ("tiff", "TIFF"), ("gif", "GIF"), ("avif", "AVIF")):
            if expected not in Image.SAVE:
                continue
            with self.subTest(format=expected):
                source = self.picture("format." + extension)
                result = tools.resize_image(source, tools.ResizeOptions(20, 20))
                with Image.open(result) as image:
                    self.assertEqual(image.format, expected)

    def test_animations_and_multipage_images_are_not_flattened(self):
        for extension in ("gif", "tiff"):
            source = self.root / ("animated." + extension)
            frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
            frames[0].save(source, save_all=True, append_images=frames[1:])
            with self.assertRaisesRegex(ValueError, "not flattened"):
                tools.resize_image(source, tools.ResizeOptions(10, 10))
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_failed_encode_does_not_leave_a_partial_file(self):
        source = self.picture()
        with patch.object(Image.Image, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                tools.resize_image(source, tools.ResizeOptions(20, 20))
        self.assertEqual(list(self.root.iterdir()), [source])

    def test_bad_dimensions_and_modes_are_rejected(self):
        for kwargs in ({"width": 0}, {"height": -1}, {"width": 50001}, {"mode": "bad"},
                       {"quality": 0}, {"label": "../escape"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                tools.ResizeOptions(**({"width": 20, "height": 20} | kwargs))


class MenuTests(unittest.TestCase):
    def test_rename_arguments_are_not_interpreted_as_options_or_shell(self):
        with patch.object(tools.os, "execv") as execute:
            tools.main(["rename", "--", "-template", "a ' $(bad).txt"])
        executable, args = execute.call_args.args
        self.assertEqual(executable, tools.KRENAME)
        self.assertEqual(args, [tools.KRENAME, "--", str(Path.cwd() / "-template"),
                               str(Path.cwd() / "a ' $(bad).txt")])
        self.assertNotIn("--start", args)

    def test_duplicate_inputs_are_removed_without_resolving_symlinks(self):
        self.assertEqual(len(tools.local_paths(["a", "./a"])), 1)

    def test_installed_menu_paths_and_mime_filters(self):
        root = Path(os.environ["FILE_TOOLS"])
        menus = root / "share/kio/servicemenus"
        for filename, action, mime, command in (
            ("extract-pdf", "ExtractPdf", "application/pdf", "pdf"),
            ("powerrename", "PowerRename", "inode/directory", "rename"),
            ("resize-pictures", "ResizePictures", "image/png", "resize"),
        ):
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(menus / f"file-tools-{filename}.desktop")
            entry = parser["Desktop Entry"]
            self.assertEqual(entry["X-KDE-Protocols"], "file")
            self.assertIn(mime, entry["MimeType"].split(";"))
            self.assertEqual(parser[f"Desktop Action {action}"]["Exec"],
                             f"{root}/bin/file-tools {command} -- %F")


class QtDialogTests(FileTest):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_default_resize_dialog_preserves_originals(self):
        from file_tools_gui import ResizeDialog
        dialog = ResizeDialog(3)
        options = dialog.options()
        self.assertEqual((options.width, options.height, options.label), (1366, 768, "Medium"))
        self.assertTrue(options.shrink_only)
        self.assertTrue(options.match_orientation)
        self.assertFalse(options.strip_metadata)
        self.assertFalse(dialog.width.isEnabled())

    def test_custom_percent_controls_and_return_to_preset(self):
        from file_tools_gui import ResizeDialog
        dialog = ResizeDialog(1)
        dialog.preset.setCurrentIndex(4)
        self.assertTrue(dialog.width.isEnabled())
        dialog.units.setCurrentIndex(1)
        self.assertTrue(dialog.options().percent)
        self.assertEqual(dialog.width.value(), 50)
        self.assertFalse(dialog.mode.isEnabled())
        dialog.preset.setCurrentIndex(2)
        self.assertFalse(dialog.options().percent)
        self.assertTrue(dialog.mode.isEnabled())
        self.assertEqual(dialog.options().width, 1920)

    def test_batch_continues_after_an_individual_error(self):
        from file_tools_gui import Worker
        source = self.picture()
        missing = self.root / "missing.png"
        worker = Worker([missing, source], lambda path: tools.resize_image(path, tools.ResizeOptions(20, 20)))
        received = []
        worker.results.connect(lambda successes, failures: received.append((successes, failures)))
        worker.run()
        successes, failures = received[0]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("missing.png", failures[0])

    def test_threaded_progress_completes_and_reports_errors(self):
        from PySide6.QtWidgets import QMessageBox
        from file_tools_gui import process
        source = self.picture()
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.Ok):
            status = process([self.root / "missing.png", source],
                             lambda path: tools.resize_image(path, tools.ResizeOptions(20, 20)),
                             "Test resize")
        self.assertEqual(status, 1)
        self.assertTrue((self.root / "photo (20x20).png").exists())

    def test_pdf_dialog_is_compact_and_keeps_300_dpi_available(self):
        from file_tools_gui import ExtractDialog
        dialog = ExtractDialog(1)
        self.assertEqual(dialog.width(), 460)
        self.assertEqual(dialog.quality.currentData(), 300)
        self.assertEqual([dialog.quality.itemData(i) for i in range(3)], [150, 200, 300])

    def test_resize_advanced_options_are_collapsed_by_default(self):
        from file_tools_gui import ResizeDialog
        dialog = ResizeDialog(1)
        self.assertTrue(dialog.advanced.isHidden())
        self.assertTrue(dialog.custom.isHidden())
        self.assertLessEqual(dialog.geometry().width(), 480)


if __name__ == "__main__":
    unittest.main(verbosity=2)
