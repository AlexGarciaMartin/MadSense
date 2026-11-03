using System.Collections.Concurrent;

// Test observation must not run JSON serialization or file I/O on a production
// input callback. Capacity exhaustion and writer failure invalidate evidence.
sealed class DeferredEvidence<T> : IDisposable
{
    readonly BlockingCollection<T> pending;
    readonly Thread writer;
    long submitted, written, rejected;
    int completed;
    Exception? error;
    public long Rejected => Interlocked.Read(ref rejected);
    public long Unwritten => Interlocked.Read(ref submitted) - Interlocked.Read(ref written);
    public Exception? Error => Volatile.Read(ref error);

    public DeferredEvidence(int capacity, Action<T> observe)
    {
        pending = new(capacity);
        writer = new Thread(() =>
        {
            try
            {
                foreach (T item in pending.GetConsumingEnumerable())
                {
                    observe(item);
                    Interlocked.Increment(ref written);
                }
            }
            catch (Exception failure) { Volatile.Write(ref error, failure); }
        }) { IsBackground = true, Name = "madsense-core-evidence" };
        writer.Start();
    }

    public bool TryWrite(T item)
    {
        if (Volatile.Read(ref completed) == 0)
        {
            Interlocked.Increment(ref submitted);
            try { if (pending.TryAdd(item)) return true; }
            catch (InvalidOperationException) { }
            Interlocked.Decrement(ref submitted);
        }
        Interlocked.Increment(ref rejected);
        return false;
    }

    public void Complete()
    {
        if (Interlocked.Exchange(ref completed, 1) == 0) pending.CompleteAdding();
        writer.Join();
    }

    public void Dispose() { Complete(); pending.Dispose(); }
}
