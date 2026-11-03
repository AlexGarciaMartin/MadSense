namespace MadSense.Mac;

/// <summary>Reconnects transient physical links without repeatedly seizing a
/// usable controller when the virtual transport cannot be created.</summary>
internal sealed class BridgeRetryPolicy
{
    private long nextAttempt;
    public bool RequiresExplicitRetry { get; private set; }

    public bool CanAttempt(long now) => !RequiresExplicitRetry && now >= nextAttempt;

    public void Delay(long now, int milliseconds) => nextAttempt = now + milliseconds;

    public void RecordFailure(Exception error, long now)
    {
        if (error is VirtualHidUnavailableException) RequiresExplicitRetry = true;
        Delay(now, 10000);
    }

    public void Resume()
    {
        RequiresExplicitRetry = false;
        nextAttempt = 0;
    }
}
