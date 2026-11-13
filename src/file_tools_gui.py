"""Small Qt dialogs; all actual file operations live in dolphin_tools."""

import sys
import os
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFileDialog,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QSpinBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from file_tools import PRESETS, CancelledError, KRENAME, ResizeOptions, extract_pdf, resize_image


class ExtractDialog(QDialog):
    def __init__(self, count):
        super().__init__()
        self.setWindowTitle("Extract Pages to Images")
        self.setFixedWidth(460)
        layout = QVBoxLayout(self)
        label = QLabel(f"Extract {count} PDF(s) into adjacent, same-named folders.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.quality = QComboBox()
        for name, dpi in (("Fast — 150 DPI", 150), ("Balanced — 200 DPI", 200),
                          ("High quality — 300 DPI", 300)):
            self.quality.addItem(name, dpi)
        self.quality.setCurrentIndex(2)
        form = QFormLayout()
        form.addRow("Resolution:", self.quality)
        layout.addLayout(form)
        note = QLabel("MuPDF • lossless PNG • originals kept\n"
                      "Lower DPI is faster. Existing folders are never replaced.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Extract")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ResizeDialog(QDialog):
    def __init__(self, count):
        super().__init__()
        self.setWindowTitle("Resize Pictures")
        self.setFixedWidth(480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Resize {count} picture(s). Originals are always kept."))
        form = QFormLayout()
        layout.addLayout(form)
        self.preset = QComboBox()
        for name, (width, height) in PRESETS.items():
            self.preset.addItem(f"{name} ({width} × {height})", name)
        self.preset.addItem("Custom", "Custom")
        form.addRow("Size:", self.preset)
        self.custom = QWidget()
        custom_form = QFormLayout(self.custom)
        layout.addWidget(self.custom)
        self.units = QComboBox()
        self.units.addItems(["Pixels", "Percent"])
        custom_form.addRow("Units:", self.units)
        self.width = QSpinBox()
        self.height = QSpinBox()
        for spin in (self.width, self.height):
            spin.setRange(1, 50000)
        custom_form.addRow("Width:", self.width)
        custom_form.addRow("Height:", self.height)
        self.advanced = QWidget()
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_form = QFormLayout()
        advanced_layout.addLayout(advanced_form)
        self.mode = QComboBox()
        self.mode.addItems(["Fit", "Fill", "Stretch"])
        self.mode.setToolTip("Fit: keep proportions. Fill: crop to size. Stretch: exact dimensions.")
        advanced_form.addRow("Mode:", self.mode)
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setValue(90)
        advanced_form.addRow("JPEG / WebP / AVIF quality:", self.quality)
        self.shrink = QCheckBox("Only make pictures smaller")
        self.shrink.setChecked(True)
        self.orientation = QCheckBox("Match picture orientation")
        self.orientation.setChecked(True)
        self.strip = QCheckBox("Remove metadata from resized copies")
        layout.addWidget(self.shrink)
        for widget in (self.orientation, self.strip):
            advanced_layout.addWidget(widget)
        more = QPushButton("More options ▸")
        more.setCheckable(True)
        layout.addWidget(more)
        layout.addWidget(self.advanced)
        self.advanced.hide()

        def advanced_visible(visible):
            self.advanced.setVisible(visible)
            more.setText("Fewer options ▾" if visible else "More options ▸")
            self.adjustSize()

        more.toggled.connect(advanced_visible)
        note = QLabel("Copies are saved alongside each original in the same format.\n"
                      "Existing files are never overwritten. Animated / multipage images are skipped.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Resize")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.preset.currentIndexChanged.connect(self.choose_preset)
        self.units.currentIndexChanged.connect(self.choose_units)
        self.choose_preset()
        self.preset.setCurrentIndex(1)

    def choose_preset(self):
        name = self.preset.currentData()
        custom = name == "Custom"
        self.custom.setVisible(custom)
        for widget in (self.width, self.height, self.units):
            widget.setEnabled(custom)
        if not custom:
            self.units.setCurrentIndex(0)
            self.width.setValue(PRESETS[name][0])
            self.height.setValue(PRESETS[name][1])

    def choose_units(self):
        percent = self.units.currentIndex() == 1
        self.mode.setEnabled(not percent)
        self.orientation.setEnabled(not percent)
        if percent:
            self.width.setValue(50)
            self.height.setValue(50)

    def options(self):
        return ResizeOptions(
            width=self.width.value(),
            height=self.height.value(),
            mode=self.mode.currentText(),
            percent=self.units.currentIndex() == 1,
            shrink_only=self.shrink.isChecked(),
            match_orientation=self.orientation.isChecked(),
            label=self.preset.currentData(),
            quality=self.quality.value(),
            strip_metadata=self.strip.isChecked(),
        )


class Worker(QThread):
    current = Signal(int, str)
    detail = Signal(str)
    results = Signal(list, list)

    def __init__(self, paths, operation, pdf_dpi=None):
        super().__init__()
        self.paths, self.operation = paths, operation
        self.pdf_dpi = pdf_dpi
        self.cancelled = threading.Event()
        self.process_lock = threading.Lock()
        self.process = None

    def watch_process(self, process):
        with self.process_lock:
            self.process = process
            if process is not None and self.cancelled.is_set() and process.poll() is None:
                process.terminate()

    def cancel(self):
        self.cancelled.set()
        with self.process_lock:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass

    def run(self):
        successes, failures = [], []
        for index, path in enumerate(self.paths):
            if self.cancelled.is_set():
                break
            self.current.emit(index, path.name)
            try:
                if self.pdf_dpi is not None:
                    result = extract_pdf(path, dpi=self.pdf_dpi, progress=self.detail.emit,
                                         on_process=self.watch_process,
                                         cancelled=self.cancelled.is_set)
                else:
                    result = self.operation(path)
                successes.append(str(result))
            except CancelledError:
                break
            except Exception as error:
                failures.append(f"{path}: {error}")
        self.results.emit(successes, failures)


class ProgressDialog(QProgressDialog):
    cancel_requested = Signal()

    def reject(self):
        # Let the worker stop/clean its own output before destroying the dialog.
        self.cancel_requested.emit()
        self.setLabelText("Cancelling…")


def process(paths, operation, title, pdf_dpi=None):
    progress = ProgressDialog("Starting…", "Cancel", 0, 0)
    progress.setWindowTitle(title)
    progress.setFixedWidth(460)
    label = QLabel("Starting…")
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    progress.setLabel(label)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    worker = Worker(paths, operation, pdf_dpi=pdf_dpi)
    progress.canceled.connect(worker.cancel)
    progress.cancel_requested.connect(worker.cancel)
    worker.detail.connect(progress.setLabelText)
    result = ([], [])

    def finish(successes, failures):
        nonlocal result
        result = successes, failures
        progress.accept()

    worker.current.connect(lambda index, name: progress.setLabelText(
        f"Processing {index + 1} of {len(paths)}: {name}"
    ))
    worker.results.connect(finish)
    # Start after the dialog's event loop is running, even for very fast failures.
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, worker.start)
    progress.exec()
    worker.wait()
    successes, failures = result
    box = QMessageBox()
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Warning if failures else QMessageBox.Information)
    box.setTextFormat(Qt.PlainText)
    box.setText(("Cancelled. " if worker.cancelled.is_set() else "") +
                f"Created {len(successes)} output(s)." +
                (f" {len(failures)} file(s) could not be processed." if failures else ""))
    box.setInformativeText("Original files were kept. See details for output paths and any errors.")
    box.setDetailedText("\n".join(successes + failures))
    box.exec()
    return 130 if worker.cancelled.is_set() else (1 if failures else 0)


def application():
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("file-tools")
    app.setApplicationDisplayName("File Tools")
    app.setDesktopFileName("org.cammi.FileTools")
    return app


def run(action, paths):
    _app = application()
    if action == "resize":
        dialog = ResizeDialog(len(paths))
        if dialog.exec() != QDialog.Accepted:
            return 0
        options = dialog.options()
        return process(paths, lambda path: resize_image(path, options), "Resize Pictures")
    dialog = ExtractDialog(len(paths))
    if dialog.exec() != QDialog.Accepted:
        return 0
    return process(paths, None, "Extract Pages to Images", pdf_dpi=dialog.quality.currentData())


def launch():
    _app = application()
    dialog = QDialog()
    dialog.setWindowTitle("File Tools")
    dialog.setFixedWidth(360)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("Choose a file operation:"))
    selection = []

    def choose(action):
        if action == "rename":
            selection.extend([action, []])
            dialog.accept()
            return
        title, pattern = (("Choose PDFs", "PDF documents (*.pdf *.PDF)") if action == "pdf"
                          else ("Choose pictures", "Pictures (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif *.avif)"))
        files, _ = QFileDialog.getOpenFileNames(dialog, title, "", pattern)
        if files:
            from pathlib import Path
            selection.extend([action, [Path(path) for path in files]])
            dialog.accept()

    for action, title in (("pdf", "Extract PDF pages…"), ("resize", "Resize pictures…"),
                          ("rename", "PowerRename…")):
        button = QPushButton(title)
        button.clicked.connect(lambda checked=False, action=action: choose(action))
        layout.addWidget(button)
    if dialog.exec() != QDialog.Accepted:
        return 0
    action, paths = selection
    if action == "rename":
        os.execv(KRENAME, [KRENAME])
    return run(action, paths)
