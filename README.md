# File Tools

A standalone Linux/Qt program for PDF page extraction, batch renaming and image
resizing. Dolphin discovers the included standard KDE service menus; no Dolphin
source patches, custom file-manager build or manual menu installation is needed.
It is also available as **File Tools** in the application launcher.

## Actions

- **Extract Pages to Images** renders whole PDF pages with **PyMuPDF/MuPDF**, not
  Poppler. `Example.pdf` creates adjacent `Example/page-001.png`, etc. A compact
  dialog offers 150, 200 and 300 DPI (300 remains the default), followed by real
  page progress and cancellation. Lossless PNG compression is tuned for speed.
  Files are published without a second full copy on normal Linux filesystems;
  unsupported filesystems use an exclusive-copy fallback. Existing destinations
  are never overwritten. Failure/cancellation removes this job's partial output.
- **PowerRename…** opens KDE's maintained KRename with selected files/folders,
  including regex, numbering, preview and undo. A small packaging adjustment makes
  selected folders rename themselves rather than expanding their contents.
- **Resize Pictures…** opens a compact dialog with size presets and custom
  pixel/percentage sizes. More options expose Fit/Fill/Stretch, image quality,
  orientation and metadata controls. Originals are kept; collisions get numbered
  suffixes. Animated/multipage images are skipped rather than flattened.

PDF rendering runs in an isolated child process, keeping MuPDF away from Qt
threads and allowing immediate cancellation of a slow page. Only the child
started by the current job can be terminated. Older already-running jobs from
other programs are not interrupted. Image batches stop after the current image.

## Nix

```sh
nix build .#file-tools
nix run .#file-tools             # standalone launcher
nix run .#file-tools -- pdf -- '/path/to/document.pdf'
nix flake check                 # backend/Qt tests + real KDE menu discovery
```

Install `packages.x86_64-linux.file-tools` through Home Manager or a NixOS user
profile. That installs its executable, app entry and `share/kio/servicemenus`
files with fully resolved dependencies. The consuming NixOS configuration owns
Sway's compact floating-window rules; normal Dolphin windows stay tiled. Other
desktops can use their normal dialog/window rules.

Source is **AGPL-3.0-or-later**, matching the open-source PyMuPDF/MuPDF dependency.
KRename remains a separate upstream program under its own license. No accounts,
personal PDFs, example user documents, machine paths or secrets belong here.
