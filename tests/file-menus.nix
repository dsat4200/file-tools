{ pkgs, fileTools }:

pkgs.stdenv.mkDerivation {
  pname = "dolphin-file-menus-check";
  version = "1.0";
  src = ./file-menus;
  nativeBuildInputs = [ pkgs.cmake pkgs.kdePackages.extra-cmake-modules ];
  buildInputs = [ pkgs.kdePackages.qtbase pkgs.kdePackages.kio ];
  dontWrapQtApps = true;
  doCheck = true;
  checkPhase = ''
    export HOME="$TMPDIR/home" XDG_RUNTIME_DIR="$TMPDIR/runtime"
    export XDG_CONFIG_HOME="$HOME/config" XDG_CACHE_HOME="$HOME/cache"
    export XDG_DATA_HOME="$HOME/data" XDG_CONFIG_DIRS="$HOME/empty-config"
    export XDG_DATA_DIRS="${fileTools}/share:${pkgs.shared-mime-info}/share"
    export QT_QPA_PLATFORM=offscreen
    export QT_PLUGIN_PATH="${pkgs.kdePackages.qtbase}/lib/qt-6/plugins"
    export LC_ALL=C.UTF-8
    mkdir -p "$HOME" "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
    ./dolphin-file-menus
  '';
  installPhase = ''
    touch "$out"
  '';
}
