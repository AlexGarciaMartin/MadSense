# Integrated update preview (not the public update channel)

Branch: feature/in-app-updates. The website and stable release manifest remain on 0.4.7.
Version 0.4.8.3 is the Internet bootstrap preview; 0.4.8.4 is its update target. Both install
over the current user's MadSense installation, preserve preferences and use real
driver checks. They are not the separate InstallerTestBuild product.

## User rehearsal

Exit MadSense through the tray, outside a game. Install 0.4.8.3 manually once:
0.4.7 cannot gain an updater without first receiving the new code. Open MadSense,
check the version and controller/settings. No JSON selection or local update
package is required.

The app automatically checks the signed preview feed 15 seconds after startup.
The existing tray "Buscar actualizaciones" command checks the same channel.
Only preview builds consult the fixed update-preview-channel GitHub prerelease.
MadSense validates its signature and offers ACTUALIZAR 0.4.8.4 in the existing UI.
Click that offer and confirm. The package is downloaded to the private cache, checked,
then a separate helper validates it again and acknowledges readiness. The original
app releases the controller and exits through its existing QuitAsync path. Only
after that process exits may the helper run the installer silently, without a
forced process kill or Windows reboot. Successful driver registration and the
installed version are checked before relaunch.

Check that the app reopens as 0.4.8.4, settings remain, the controller reconnects,
both modes work, and no new file appears in Downloads. Reusing the same descriptor
must report that the installation is current. An invalid signature or corrupted
package must fail before closing the app. The GitHub assets are publicly accessible
prereleases, not private authenticated downloads; stable users and the website
are never directed to them. Always publish with prerelease=true and latest=false.

Logs: app updates.log; %LOCALAPPDATA%/MadSense/Updates/<guid>/setup.log,
result.json, install-status.txt and failure.log when applicable. Successful
installers are deleted immediately. Finished flat cache workspaces are cleaned
after 30 minutes on a subsequent app startup; symlinks/subdirectories are skipped.
Do not delete active update workspaces.

If the test fails, keep the logs. Reinstall the official 0.4.7 installer to return
to stable, preserving settings. Inno Setup is not a transactional updater:
installation failures after file replacement are not guaranteed to roll back.
The helper reports the failure and does not auto-launch an unconfirmed payload.
No claim of automatic rollback or full hardware validation is made.

## Publisher

The app verifies an RSA-3072/SHA256-PSS signed descriptor in addition to the
installer's exact length and SHA-256. The public key is embedded. The private key
is a non-exportable Windows CNG key named MadSense.UpdateSigning.v1 belonging to
the build account; it is never shipped or committed. Other machines cannot sign
with it. Loss of that Windows profile/key requires a key migration in a trusted
installer; define long-term signing-key custody before enabling stable updates.

tools/sign-update.ps1 signs an existing exact installer, producing
<installer>.update.json. The signed payload binds version, channel, filename,
size, hash and canonical GitHub repository URL. Stable downloads fetch this
descriptor beside the official EXE and reject missing/invalid signatures.
No unsigned/browser fallback is used. Only HTTPS GitHub and GitHub release-assets
redirects are allowed, with a bounded redirect count, size and timeout.

Preview discovery exists only in four-component preview builds. Stable builds
accept only a newer three-component stable descriptor; they cannot consume preview
packages. Existing 0.4.7 clients continue to use the old website manifest.

Build the two previews from the same clean source:

    tools/build-installer.ps1 -PreviewVersion 0.4.8.3 -DotnetPath <dotnet>
    tools/build-installer.ps1 -PreviewVersion 0.4.8.4 -DotnetPath <dotnet>
    tools/sign-update.ps1 -Installer <0.4.8.4-Setup.exe> -Version 0.4.8.4

Publish the .4 installer and its .update.json on update-preview-0.4.8.4.
Copy the exact signed descriptor as preview.update.json to update-preview-channel.
Publish both as prereleases with latest=false; never change /downloads/release.json.
The bootstrap .3 installer may be included on the channel prerelease.
Run the explicit LivePreviewDiscoveryAndFullDownload test to validate discovery,
both signed descriptors, redirects, full package length/hash, and post-update
Current state through the app's actual HTTP and verification code, without HID.

Before public rollout: complete this physical rehearsal, test HTTPS download,
network failure, cancellation, UAC denial, missing/changed driver, reboot-needed,
registration failure and reinstall recovery in an isolated Windows machine.
Change the app version to a three-component stable version and publish its signed
descriptor before updating the website manifest. Do not announce previews through
the stable website feed.
