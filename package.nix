{ pkgs }:

let
  python = pkgs.python3.withPackages (ps: [ ps.pillow ps.pyside6 ps.pymupdf ]);
  selectedKRename = pkgs.krename.overrideAttrs (old: {
    # Context-menu selections must rename folders, not expand their contents.
    postPatch = (old.postPatch or "") + ''
      substituteInPlace src/krenameimpl.cpp \
        --replace-fail 'this->addFilesOrDirs(list);' \
          'for (const QUrl &url : list) { this->addFileOrDir(url); }'
    '';
  });
in
pkgs.stdenv.mkDerivation {
  pname = "file-tools";
  version = "1.1.0";
  src = ./src;
  nativeBuildInputs = [ pkgs.makeWrapper pkgs.kdePackages.wrapQtAppsHook ];
  buildInputs = [ pkgs.kdePackages.qtbase ];
  dontConfigure = true;
  dontBuild = true;
  dontWrapQtApps = true;
  installPhase = ''
    mkdir -p "$out/bin" "$out/share/file-tools" "$out/share/kio/servicemenus" "$out/share/applications"
    cp *.py "$out/share/file-tools/"
    substituteInPlace "$out/share/file-tools/file_tools.py" \
      --replace-fail '@krename@' '${selectedKRename}/bin/krename'
    makeWrapper ${python}/bin/python3 "$out/bin/file-tools" \
      --add-flags "$out/share/file-tools/file_tools.py" "''${qtWrapperArgs[@]}"
    for menu in ${./menus}/*.desktop; do
      substitute "$menu" "$out/share/kio/servicemenus/file-tools-$(basename "$menu")" \
        --replace-fail '@tools@' "$out/bin/file-tools"
    done
    substitute ${./org.cammi.FileTools.desktop} "$out/share/applications/org.cammi.FileTools.desktop" \
      --replace-fail '@tools@' "$out/bin/file-tools"
  '';
  doInstallCheck = true;
  nativeInstallCheckInputs = [ python pkgs.qpdf ];
  installCheckPhase = ''
    export QT_QPA_PLATFORM=offscreen
    export HOME="$TMPDIR/home" XDG_RUNTIME_DIR="$TMPDIR/runtime"
    mkdir -p "$HOME" "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
    export PYTHONPATH="$out/share/file-tools" PYTHONDONTWRITEBYTECODE=1
    export FILE_TOOLS="$out"
    ${python}/bin/python3 ${./tests/test_file_tools.py}
    "$out/bin/file-tools" --help >/dev/null
  '';
  meta = {
    description = "Standalone MuPDF page extraction, batch renaming and image resizing";
    mainProgram = "file-tools";
    license = pkgs.lib.licenses.agpl3Plus;
    platforms = [ "x86_64-linux" ];
  };
}
