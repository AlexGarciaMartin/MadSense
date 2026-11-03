# Optional Wine USB source patches

This kit prepares three upstream MacGameFix patches for **public Wine 11.17**
on macOS. They associate a physical USB controller's HID device with its real
CoreAudio USB endpoint using a shared `DEVPKEY_Device_ContainerId`. Games that
find the controller's haptic audio endpoint through the audio PropertyStore
can use that association. No macOS virtual HID entitlement is involved in
this source change.

The source preparation command does not build or install Wine, alter CrossOver, create a global
virtual controller, translate Bluetooth reports, or provide a binary. Patch
application and parser tests do not establish working haptics in a game.
There has been no physical controller/game validation of this kit.

The exact public Wine commit is
`36b6a2cf679fb395f668a917b76537190e212d9c` (`wine-11.17`). The three upstream
patches are preserved verbatim from MacGameFix commit
`233d4daa8c5c06d2a2b85dee8d720bdc2dc7f65d`. They apply to the public sources;
CrossOver's proprietary installation and fork are not needed for this check.
See [NOTICE](NOTICE), [COPYING.LIB](COPYING.LIB), and
[manifest.json](manifest.json) for provenance, licenses and hashes.

## Verify the source changes

Use Python 3.12 or newer and Git. From the MadSense checkout, with an existing
`work` directory and a destination that does **not** exist:

```sh
python3 macos/wine-usb/prepare_source.py --output work/wine-usb-check
python3 macos/wine-usb/test_kit.py
```

The first command downloads only the three affected files from the pinned
public Wine commit, checks their SHA256 hashes and the bundled patch hashes,
and applies the patches inside a new directory. This three-file fixture is
**not a buildable Wine tree**. Its `MADSENSE-WINE-USB.json` records the scope,
original hashes and patched hashes. Git applies exact verified input without
fuzzy matching; the upstream CoreAudio patch has an expected line offset of
18 lines against the public tag.

Tests use the pinned network fixture once and verify actual patch application,
modified-download rejection, and preservation of an existing destination.
An isolated synthetic Git commit also exercises full-tree export, excluded
ignored build files, unchanged input sources, and dirty/nested-tree rejection.
With a C compiler, they compile the actual two container functions extracted
from the patched files and test matching HID/audio IDs, different USB ports,
Bluetooth exclusion and malformed UIDs. Ubuntu CI requires this C test. An
optional `MADSENSE_WINE_FIXTURES` directory can supply the three original,
hash-verified files for offline testing; there is no hash-check bypass.

## Prepare full public sources for a manual build

Obtain a clean public Wine checkout at the exact commit above, with LF source
files. Then export and patch a separate, new tree:

```sh
python3 macos/wine-usb/prepare_source.py \
  --source-tree /path/to/clean-public-wine-11.17 \
  --output /path/to/new-wine-usb-sources
```

The command refuses another commit, modified files, untracked files, an
existing destination, a destination inside the input checkout, or any source
hash mismatch. It exports committed sources, excluding `.git` and ignored
build products, and applies the same three patches only in the new tree.
It never patches the input checkout or an installed runner. A failure can
leave the newly created output for inspection; choose another new path when
retrying. Building this tree requires Wine's normal macOS build dependencies
and is not performed by source preparation or the Ubuntu workflow.

## Remote module compilation

The separate `macos-wine-usb-build.yml` workflow exports the full pinned public
Wine checkout and compiles the three affected modules on macOS 15 ARM and
Intel. It uses Apple Clang for native code and the official llvm-mingw
`20261006` universal toolchain, whose archive SHA256 is fixed in
`compile_modules.sh`. ARM builds include aarch64/ARM64EC; Intel builds x86_64.
The disposable runners install only build dependencies through Homebrew.

This is a module compilation check, not a complete Wine distribution. It
does not install or execute Wine, change CrossOver, create a prefix or access
a controller. Artifacts contain source/patch provenance, toolchain versions,
configure/build logs and hashes of compiled modules. They do not distribute
standalone replacement binaries. A successful build still needs runtime
and physical USB/game validation before it can support a compatibility claim.

## Compatibility limits

The three patches affect `winebus.sys/main.c`, `winecoreaudio.drv/coreaudio.c`
and `mmdevapi/devenum.c`. They cover audio PropertyStore discovery. Games
that discover endpoints through SetupAPI need additional upstream changes
(MacGameFix patches 0009/0010), which this kit does not include. The generic
winebus change is not guarded for Linux; this recipe is specifically for a
macOS Wine build. It is not an instruction to mix modules from different
Wine or CrossOver versions.

The association derives from the USB port's location ID, not a controller
serial number. The audio parser relies on the `AppleUSBAudioEngine:` UID
format. It rejects wrong prefixes, zero, nonhexadecimal data and overflowing
location IDs on the target 64-bit platform. Upstream also accepts a leading
`+` on the hex number and an empty interface suffix; the tests explicitly
report these permissive cases. It does not independently verify physical
USB identity or establish that the endpoint UID format remains stable on
every macOS version. The patches are kept unchanged, with that limitation
visible. Physical USB enumeration and a compatible game remain the necessary
next checks before claiming a functional haptic path.
