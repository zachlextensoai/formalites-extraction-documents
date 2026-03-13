{ pkgs }: {
  deps = [
    pkgs.python311
    (pkgs.tesseract.override { enableLanguages = [ "eng" "fra" ]; })
    pkgs.poppler_utils
    pkgs.ghostscript
    pkgs.nodejs_20
  ];
}
