using System.Diagnostics;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;

namespace MadSense;

internal sealed record UpdatePackage(string Version, string Channel, string Filename, long Bytes, string Sha256, string DownloadUrl);

internal static class IntegratedUpdate
{
    internal const long MaximumPackageBytes = 256L * 1024 * 1024;
    internal const string PublicKey = """
        -----BEGIN PUBLIC KEY-----
        MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA2F2KuFIAkNqGDQzM374r
        qGsUqabhpyKyCAEqZeQwjh55NahCF32CgYoBsoEaODiKk7sGFn+fnT+AMc/ZEOnM
        z7aNS6Fmx6vhHBkGNO9xCZSsE55xnh2uHJsJ34CiEBwwEmAqochftlLeeDZK6c9T
        mHKa9zWjXBpmLEBYeziudjTSJIgbvW7ePb/KC4AJ7Gea4Ntmmm/rTGMuCccnP7QU
        BG6qsh36zUm5NDfjHfp9aOe53VvAFjN4xYUzGFEww7S9ClTiqJp5BG3yD0lqI6K8
        2KvSnieIbzxrWCl2ERweG9YWtWjbzlbJPdGF2e0Htss9Lw35F4SveEwvqXRpwit7
        pg+UmlxephNFKnQIxRbhEqDK6hgqZDndXzcN4QzpXMn+WuyfNl+k92ZZ9zZd+wBU
        xwNeZy9BJu/CJJ7sUFKlh565E7q3wa7eGNUSnQ3GBC2s2IjsD+F/y3NHCzpM+mGG
        PdU+PUJ8amzM5gwWJ6lWgT5b/GOyLvDlfpG1wKusLo2tAgMBAAE=
        -----END PUBLIC KEY-----
        """;
    internal static string CacheRoot => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MadSense", "Updates");
    static readonly HttpClient http = new(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = Timeout.InfiniteTimeSpan };
    static readonly JsonSerializerOptions json = new() { PropertyNameCaseInsensitive = false };

    public static void CleanCache()
    {
        try
        {
            if (!Directory.Exists(CacheRoot) || (File.GetAttributes(CacheRoot) & FileAttributes.ReparsePoint) != 0) return;
            foreach (var directory in Directory.EnumerateDirectories(CacheRoot))
            {
                if (!Guid.TryParseExact(Path.GetFileName(directory), "N", out _) ||
                    (File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0 ||
                    !File.Exists(Path.Combine(directory, "finished")) ||
                    File.GetLastWriteTimeUtc(Path.Combine(directory, "finished")) > DateTime.UtcNow.AddMinutes(-30) ||
                    Directory.EnumerateDirectories(directory).Any()) continue;
                var files = Directory.GetFiles(directory);
                if (files.Any(f => (File.GetAttributes(f) & FileAttributes.ReparsePoint) != 0)) continue;
                try { foreach (var file in files) File.Delete(file); Directory.Delete(directory); }
                catch (IOException) { /* A helper may still have a runtime file open. */ }
                catch (UnauthorizedAccessException) { }
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    internal static UpdatePackage VerifyDescriptor(byte[] envelope, Version current, bool preview, string publicKey = PublicKey, bool requireNewer = true)
    {
        if (envelope.Length > 32768) throw new InvalidDataException("Update descriptor is too large.");
        using var doc = JsonDocument.Parse(envelope);
        var root = doc.RootElement;
        RequireFields(root, "payload", "signature");
        var payload = Convert.FromBase64String(root.GetProperty("payload").GetString()!);
        var signature = Convert.FromBase64String(root.GetProperty("signature").GetString()!);
        using var rsa = RSA.Create();
        rsa.ImportFromPem(publicKey);
        if (!rsa.VerifyData(payload, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pss))
            throw new CryptographicException("Invalid update signature.");
        using var signed = JsonDocument.Parse(payload);
        RequireFields(signed.RootElement, "Version", "Channel", "Filename", "Bytes", "Sha256", "DownloadUrl");
        var p = JsonSerializer.Deserialize<UpdatePackage>(payload, json) ?? throw new InvalidDataException("Empty update.");
        if (!Version.TryParse(p.Version, out var version) || version.Build < 0 ||
            p.Version != version.ToString() ||
            (preview ? p.Channel != "preview" || version.Revision < 1 : p.Channel != "stable" || version.Revision >= 0) ||
            p.Filename != $"MadSense-{p.Version}-Setup.exe" ||
            p.Bytes <= 0 || p.Bytes > MaximumPackageBytes ||
            p.Sha256 is null || p.Sha256.Length != 64 || !p.Sha256.All(Uri.IsHexDigit))
            throw new InvalidDataException("Invalid update metadata or version.");
        var tag = preview ? $"update-preview-{p.Version}" : $"v{p.Version}";
        if (p.DownloadUrl != $"https://github.com/AlexGarciaMartin/MadSense/releases/download/{tag}/{p.Filename}")
            throw new InvalidDataException("Invalid update location.");
        if (requireNewer && version <= current) throw new InvalidDataException("Update must be newer than the installed version.");
        return p;
    }

    static void RequireFields(JsonElement element, params string[] fields)
    {
        if (element.ValueKind != JsonValueKind.Object) throw new InvalidDataException("Invalid object.");
        var names = element.EnumerateObject().Select(p => p.Name).ToArray();
        if (names.Length != fields.Length || names.Distinct().Count() != fields.Length || fields.Except(names).Any())
            throw new InvalidDataException("Unexpected or duplicate update fields.");
    }

    internal static bool AllowedDownload(Uri uri) => uri.Scheme == "https" && uri.IsDefaultPort &&
        uri.UserInfo == "" && uri.Fragment == "" &&
        (uri.Host == "github.com" || uri.Host == "release-assets.githubusercontent.com");

    internal static async Task Fetch(Uri uri, Stream output, long maximum, IProgress<int>? progress, CancellationToken token, HttpClient? client = null)
    {
        for (int redirects = 0; redirects <= 5; redirects++)
        {
            if (!AllowedDownload(uri)) throw new InvalidDataException("Untrusted update redirect.");
            using var request = new HttpRequestMessage(HttpMethod.Get, uri);
            request.Headers.UserAgent.ParseAdd("MadSense-IntegratedUpdate");
            request.Headers.CacheControl = new() { NoCache = true };
            using var response = await (client ?? http).SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token).ConfigureAwait(false);
            if ((int)response.StatusCode is 301 or 302 or 303 or 307 or 308)
            {
                uri = new Uri(uri, response.Headers.Location ?? throw new InvalidDataException("Missing redirect."));
                continue;
            }
            response.EnsureSuccessStatusCode();
            if (response.Content.Headers.ContentLength > maximum) throw new InvalidDataException("Update exceeds size limit.");
            await using var input = await response.Content.ReadAsStreamAsync(token).ConfigureAwait(false);
            await CopyBounded(input, output, maximum, progress, token).ConfigureAwait(false);
            return;
        }
        throw new InvalidDataException("Too many update redirects.");
    }

    internal static async Task CopyBounded(Stream input, Stream output, long maximum, IProgress<int>? progress, CancellationToken token)
    {
        byte[] buffer = new byte[65536];
        long total = 0;
        int previous = -1;
        int read;
        while ((read = await input.ReadAsync(buffer, token).ConfigureAwait(false)) != 0)
        {
            total += read;
            if (total > maximum) throw new InvalidDataException("Update exceeds size limit.");
            await output.WriteAsync(buffer.AsMemory(0, read), token).ConfigureAwait(false);
            int percent = (int)(total * 100 / maximum);
            if (percent != previous) { progress?.Report(percent); previous = percent; }
        }
    }

    internal static void VerifyPackage(Stream stream, UpdatePackage package)
    {
        if (stream.Length != package.Bytes) throw new InvalidDataException("Update size mismatch.");
        stream.Position = 0;
        if (!Convert.ToHexString(SHA256.HashData(stream)).Equals(package.Sha256, StringComparison.OrdinalIgnoreCase))
            throw new CryptographicException("Update hash mismatch.");
        stream.Position = 0;
    }

    public static async Task<string> StageAsync(AppUpdate? offer, string? previewDescriptor, IProgress<int> progress, CancellationToken token)
    {
        // Development/portable copies must not silently create or replace an installed app.
        if (!File.Exists(Path.Combine(AppContext.BaseDirectory, "uninstall", "unins000.exe")))
            throw new InvalidOperationException(L.Text("Ui.UpdateInstalledOnly"));
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(TimeSpan.FromMinutes(10));
        var ct = timeout.Token;
        byte[] descriptor;
        bool preview = typeof(Program).Assembly.GetName().Version!.Revision > 0;
        if (previewDescriptor != null)
        {
            if (new FileInfo(previewDescriptor!).Length > 32768) throw new InvalidDataException("Descriptor too large.");
            descriptor = await File.ReadAllBytesAsync(previewDescriptor!, ct);
        }
        else
        {
            using var memory = new MemoryStream();
            await Fetch(new Uri(offer!.DownloadUri.AbsoluteUri + ".update.json"), memory, 32768, null, ct);
            descriptor = memory.ToArray();
        }
        var package = VerifyDescriptor(descriptor, typeof(Program).Assembly.GetName().Version!, preview);
        if (previewDescriptor == null && package.Version != offer!.Version) throw new InvalidDataException("Offered version changed.");
        var directory = Path.Combine(CacheRoot, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            await File.WriteAllBytesAsync(Path.Combine(directory, "update.json"), descriptor, ct);
            await using (var destination = new FileStream(Path.Combine(directory, "package.exe"), FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                if (previewDescriptor != null)
                {
                    await using var input = File.OpenRead(Path.Combine(Path.GetDirectoryName(previewDescriptor!)!, package.Filename));
                    await CopyBounded(input, destination, package.Bytes, progress, ct);
                }
                else await Fetch(new Uri(package.DownloadUrl), destination, package.Bytes, progress, ct);
            }
            using (var input = File.OpenRead(Path.Combine(directory, "package.exe"))) VerifyPackage(input, package);
            // A separate copy of the existing app acts as updater. It runs before mutex/UI/HID.
            // Only runtime binaries/configuration are copied; never preferences or controller journals.
            foreach (var source in Directory.EnumerateFiles(AppContext.BaseDirectory))
            {
                var name = Path.GetFileName(source);
                if (Path.GetExtension(name) is ".dll" or ".exe" || name is "MadSense.runtimeconfig.json" or "MadSense.deps.json")
                    File.Copy(source, Path.Combine(directory, name));
            }
            ct.ThrowIfCancellationRequested();
            return directory;
        }
        catch { File.WriteAllText(Path.Combine(directory, "finished"), "staging failed"); throw; }
    }

    public static async Task LaunchAsync(string directory, CancellationToken token)
    {
        using var parent = Process.GetCurrentProcess();
        var start = new ProcessStartInfo(Path.Combine(directory, "MadSense.exe")) { UseShellExecute = false, WorkingDirectory = directory };
        foreach (var arg in new[] { "--apply-update", Environment.ProcessId.ToString(), parent.StartTime.ToUniversalTime().Ticks.ToString(),
            Environment.ProcessPath!, L.Preference }) start.ArgumentList.Add(arg);
        using var helper = Process.Start(start) ?? throw new IOException("Updater did not start.");
        for (int i = 0; i < 100; i++)
        {
            if (File.Exists(Path.Combine(directory, "ready"))) return;
            if (helper.HasExited) throw new IOException("Updater validation failed.");
            await Task.Delay(100, token);
        }
        throw new TimeoutException("Updater did not become ready.");
    }

    // Invoked at the very start of Program.Main. No controller is opened by this process.
    public static int Apply(string[] args)
    {
        string directory = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        try
        {
            if (args.Length != 5 || !int.TryParse(args[1], out var pid) || !long.TryParse(args[2], out var ticks))
                throw new InvalidDataException("Invalid updater arguments.");
            L.Use(args[4]);
            if (!Guid.TryParseExact(Path.GetFileName(directory), "N", out _) ||
                !InstallerMaintenance.SamePath(Path.GetDirectoryName(directory)!, CacheRoot))
                throw new InvalidDataException("Invalid updater workspace.");
            using var parent = Process.GetProcessById(pid);
            var target = Path.GetFullPath(args[3]);
            if (parent.StartTime.ToUniversalTime().Ticks != ticks ||
                parent.SessionId != Process.GetCurrentProcess().SessionId ||
                !InstallerMaintenance.SamePath(parent.MainModule!.FileName!, target) ||
                Path.GetFileName(target) != "MadSense.exe" ||
                !File.Exists(Path.Combine(Path.GetDirectoryName(target)!, "uninstall", "unins000.exe")))
                throw new InvalidDataException("Installation identity mismatch.");
            var current = Version.Parse(FileVersionInfo.GetVersionInfo(target).FileVersion!);
            var descriptor = File.ReadAllBytes(Path.Combine(directory, "update.json"));
            // Four-component versions are reserved for explicit local rehearsals.
            var package = VerifyDescriptor(descriptor, current, preview: current.Revision > 0);
            using var lockedPackage = new FileStream(Path.Combine(directory, "package.exe"), FileMode.Open, FileAccess.Read, FileShare.Read);
            VerifyPackage(lockedPackage, package);
            File.WriteAllText(Path.Combine(directory, "ready"), "ready");
            if (!parent.WaitForExit(30000)) throw new TimeoutException("MadSense has not released the controller.");
            var setup = new ProcessStartInfo(Path.Combine(directory, "package.exe")) { UseShellExecute = false, WorkingDirectory = directory };
            foreach (var arg in new[] { "/VERYSILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART", "/RESTARTEXITCODE=3010",
                "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/NOLAUNCH=1",
                "/DIR=" + Path.GetDirectoryName(target), "/LOG=" + Path.Combine(directory, "setup.log"),
                "/UPDATESTATUS=" + Path.Combine(directory, "install-status.txt") }) setup.ArgumentList.Add(arg);
            using var installer = Process.Start(setup) ?? throw new IOException("Installer did not start.");
            installer.WaitForExit();
            File.WriteAllText(Path.Combine(directory, "result.json"), JsonSerializer.Serialize(new { installer.ExitCode, package.Version }));
            var status = File.Exists(Path.Combine(directory, "install-status.txt"))
                ? File.ReadAllText(Path.Combine(directory, "install-status.txt")).Trim() : "";
            if (installer.ExitCode is not (0 or 3010)) throw new IOException($"Installer exit code: {installer.ExitCode}");
            if (status is not ("ready" or "reboot")) throw new IOException("Installer did not confirm successful driver registration.");
            if (installer.ExitCode == 3010 || status == "reboot")
                MessageBox.Show(L.Text("Ui.UpdateRestartRequired"), "MadSense", MessageBoxButtons.OK, MessageBoxIcon.Information);
            else
            {
                var installed = Version.Parse(FileVersionInfo.GetVersionInfo(target).FileVersion!);
                var expected = Version.Parse(package.Version);
                if (installed.Major != expected.Major || installed.Minor != expected.Minor || installed.Build != expected.Build ||
                    Math.Max(0, installed.Revision) != Math.Max(0, expected.Revision)) throw new IOException("Installed version mismatch.");
                Process.Start(new ProcessStartInfo(target) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(target) });
            }
            lockedPackage.Dispose();
            File.Delete(Path.Combine(directory, "package.exe"));
            File.WriteAllText(Path.Combine(directory, "finished"), "completed");
            return 0;
        }
        catch (Exception error)
        {
            try { File.WriteAllText(Path.Combine(directory, "failure.log"), error.ToString()); } catch { }
            try { File.WriteAllText(Path.Combine(directory, "finished"), "failed"); } catch { }
            MessageBox.Show(L.Text("Ui.UpdateFailed") + "\n" + error.Message, "MadSense", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
