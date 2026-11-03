# Integrated update preview (not the public update channel)

Branch: feature/in-app-updates. The website and stable release manifest remain on 0.4.7.
Version 0.4.8.1 is the bootstrap preview; 0.4.8.2 is its rehearsal target. Both install
over the current user's MadSense installation, preserve preferences and use real
driver checks. They are not the separate InstallerTestBuild product.

## User rehearsal

Exit MadSense 0.4.7 through the tray, outside a game. Install 0.4.8.1 manually once:
0.4.7 cannot gain an updater without first receiving the new code. Open MadSense,
check the version and controller/settings. Extract the supplied test bundle and
leave its installer and signed .update.json together.

Use the tray menu "Probar actualización…" and select the .update.json. MadSense
validates its signature and offers ACTUALIZAR 0.4.8.2 in the existing update UI.
Click that offer and confirm. The package is copied to the private cache, checked,
then a separate helper validates it again and acknowledges readiness. The original
app releases the controller and exits through its existing QuitAsync path. Only
after that process exits may the helper run the installer silently, without a
forced process kill or Windows reboot. Successful driver registration and the
installed version are checked before relaunch.

Check that the app reopens as 0.4.8.2, settings remain, the controller reconnects,
both modes work, and no new file appears in Downloads. Reusing the same descriptor
must fail as a downgrade/equal version. An edited descriptor or corrupted package
must fail before closing the app. The rehearsal uses a local package; real HTTPS
download and redirects still require end-to-end testing before stable publication.

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

The preview chooser exists only in four-component preview builds. Stable builds
accept only a newer three-component stable descriptor; they cannot consume preview
packages. Existing 0.4.7 clients continue to use the old website manifest.

Build the two previews from the same clean source:

    tools/build-installer.ps1 -PreviewVersion 0.4.8.1 -DotnetPath <dotnet>
    tools/build-installer.ps1 -PreviewVersion 0.4.8.2 -DotnetPath <dotnet>
    tools/sign-update.ps1 -Installer <0.4.8.2-Setup.exe> -Version 0.4.8.2

Before public rollout: complete this physical rehearsal, test HTTPS download,
network failure, cancellation, UAC denial, missing/changed driver, reboot-needed,
registration failure and reinstall recovery in an isolated Windows machine.
Change the app version to a three-component stable version and publish its signed
descriptor before updating the website manifest. Do not announce previews through
the stable website feed.
