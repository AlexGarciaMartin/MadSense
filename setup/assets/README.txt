MadSense installer artwork

Palette from the current MadSense website:
  Paper  #f0ebdc
  Ink    #181815
  Orange #f95428
  Acid   #dcf131

wizard-large.bmp: 164 x 314, 24-bit BMP.
wizard-large@2x.bmp / @3x.bmp: 328 x 628 / 492 x 942.
wizard-small.bmp: 55 x 55, 24-bit BMP.
wizard-small@2x.bmp / @3x.bmp: 110 x 110 / 165 x 165.
brand-header.bmp / @2x.bmp: optional 600 x 84 / 1200 x 168 top banner.
The PNG previews are the 2x images and are not installer payload requirements.

The main sidebar uses the original mark on ink with subtle dots, followed by the
website phrase ENCIENDE. / JUEGA. / SIN CABLE. in three spaced lines. There are no
bottom bands or decorative strokes. The native
welcome page carries the installation heading and explanation, with a direct
Install action and an optional desktop shortcut; there is no repeated summary page.

The mark is the original M/lightning from src/assets/madsense.svg, redrawn from
the exact same polygon coordinates. installer.ico preserves the mark and adds
native-size PNG images at 16, 20, 24, 32, 40, 48, 64, 128 and 256 pixels.
Anton-Regular.ttf is the same display font used by the website, distributed
under SIL OFL (see OFL-Anton.txt). It is only used to regenerate raster artwork;
the installer need not install the font on the user's computer.

Regenerate from repository root on Windows:
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\generate-installer-art.ps1

The generator uses GDI+ in a private font collection, 3x supersampling and
24-bit RGB BMP output. It does not install fonts or modify Windows settings.

localized/sidebar-<language>.bmp: six 492 x 942 sidebars (3x). Setup loads the
selected language on welcome and finish. Each slogan retains the same three-line
composition, with measured type sizing to preserve the right margin.
