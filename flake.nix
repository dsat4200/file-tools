{
  description = "Standalone PDF extraction, batch rename and picture resizing with Dolphin service menus";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { self, nixpkgs }: let
    system = "x86_64-linux";
    pkgs = nixpkgs.legacyPackages.${system};
    file-tools = pkgs.callPackage ./package.nix { };
  in {
    packages.${system} = { default = file-tools; inherit file-tools; };
    checks.${system} = {
      tools = file-tools;
      menus = pkgs.callPackage ./tests/file-menus.nix { fileTools = file-tools; };
    };
  };
}
