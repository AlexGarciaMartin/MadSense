namespace MadSense;

// Product copy remains the exact embedded Windows catalogs. Only OS-specific
// instructions change; slogans and feature descriptions are never synthesized.
static class LinuxText
{
    static readonly string[] Codes = ["en", "es", "fr", "de", "it", "pt-BR"];
    static readonly Dictionary<string, string[]> Overrides = new()
    {
        ["Ui.Autostart"] = ["Start at login, in the tray", "Iniciar sesión, en la bandeja", "Démarrer à la connexion, dans la zone de notification", "Bei Anmeldung im Infobereich starten", "Avvia all'accesso, nell'area di notifica", "Iniciar ao entrar, na bandeja"],
        ["Ui.LanguageAuto"] = ["Automatic (Linux)", "Automático (Linux)", "Automatique (Linux)", "Automatisch (Linux)", "Automatico (Linux)", "Automático (Linux)"],
        ["Ui.WaitingInstructions"] = ["Press PS on your controller. It must be paired over Bluetooth in Linux.", "Pulsa PS en el mando. Tiene que estar emparejado por Bluetooth en Linux.", "Appuie sur PS. La manette doit être associée en Bluetooth dans Linux.", "Drücke PS am Controller. Er muss unter Linux über Bluetooth gekoppelt sein.", "Premi PS sul controller. Deve essere associato tramite Bluetooth in Linux.", "Pressione PS no controle. Ele deve estar pareado por Bluetooth no Linux."],
        ["Linux.Setup"] = ["Set up Linux support", "Preparar soporte Linux", "Configurer le support Linux", "Linux-Unterstützung einrichten", "Configura supporto Linux", "Configurar suporte Linux"],
        ["Linux.SetupNeeded"] = ["Linux support is not installed. Set up the USB/IP and controller access helper first.", "Falta el soporte Linux. Prepara primero USB/IP y el componente de acceso al mando.", "Le support Linux manque. Configure d'abord USB/IP et l'accès à la manette.", "Linux-Unterstützung fehlt. Richte USB/IP und den Controller-Zugriff ein.", "Manca il supporto Linux. Configura prima USB/IP e l'accesso al controller.", "Falta o suporte Linux. Configure primeiro USB/IP e o acesso ao controle."],
        ["Linux.Unsupported"] = ["This build requires Linux.", "Esta versión necesita Linux.", "Cette version nécessite Linux.", "Diese Version benötigt Linux.", "Questa versione richiede Linux.", "Esta versão requer Linux."],
        ["Linux.Preview"] = ["Interface preview · no controller access", "Vista previa · sin acceso al mando", "Aperçu · aucun accès à la manette", "Vorschau · kein Controller-Zugriff", "Anteprima · nessun accesso al controller", "Prévia · sem acesso ao controle"],
        ["Linux.Diagnostics"] = ["Diagnostics", "Diagnóstico", "Diagnostic", "Diagnose", "Diagnostica", "Diagnóstico"],
        ["Linux.NoRelease"] = ["No published Linux release is available yet.", "Todavía no hay una versión publicada de Linux.", "Aucune version Linux n'est encore publiée.", "Noch keine veröffentlichte Linux-Version verfügbar.", "Non è ancora disponibile una versione Linux pubblicata.", "Ainda não há uma versão publicada para Linux."],
        ["Linux.DownloadUpdate"] = ["Download Linux {0}", "Descargar Linux {0}", "Télécharger Linux {0}", "Linux {0} herunterladen", "Scarica Linux {0}", "Baixar Linux {0}"],
        ["Linux.Close"] = ["Close", "Cerrar", "Fermer", "Schließen", "Chiudi", "Fechar"]
    };
    public static string Text(string key, params object?[] args)
    {
        if (key == "Bridge.PairingNeeded") return L.Text(key, args).Replace("Windows", "Linux", StringComparison.Ordinal);
        if (!Overrides.TryGetValue(key, out var translations)) return L.Text(key, args);
        var value = translations[Math.Max(0, Array.IndexOf(Codes, L.CurrentLanguage))];
        return args.Length == 0 ? value : string.Format(L.Culture, value, args);
    }
}
