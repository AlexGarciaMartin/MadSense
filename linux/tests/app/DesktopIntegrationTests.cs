using MadSense;
using NUnit.Framework;
using Tmds.DBus;

namespace MadSense.Linux.App.Tests;

public sealed class DesktopIntegrationTests
{
    sealed class Subscription : IDisposable
    {
        public bool Disposed { get; private set; }
        public void Dispose() => Disposed = true;
    }
    sealed class Bus : ISessionBus
    {
        public ObjectPath ObjectPath => new("/org/freedesktop/DBus");
        public bool Owner = true;
        public Action<(string Name, string OldOwner, string NewOwner)>? Changed;
        public Action<Exception>? Error;
        public Task<IDisposable>? DelayedSubscription;
        public Task<bool> NameHasOwnerAsync(string name) => Task.FromResult(Owner);
        public Task<IDisposable> WatchNameOwnerChangedAsync(Action<(string Name, string OldOwner, string NewOwner)> changed,
            Action<Exception>? onError = null)
        { Changed = changed; Error = onError; return DelayedSubscription ?? Task.FromResult<IDisposable>(new Subscription()); }
    }
    sealed class Watcher : IStatusNotifierWatcher
    {
        public ObjectPath ObjectPath => new("/StatusNotifierWatcher");
        public bool Host;
        public Action? Registered, Unregistered;
        public Task<bool>? DelayedProperty;
        public async Task<T> GetAsync<T>(string property)
        {
            Assert.That(property, Is.EqualTo("IsStatusNotifierHostRegistered"));
            bool value = DelayedProperty == null ? Host : await DelayedProperty;
            return (T)(object)value;
        }
        public Task<IDisposable> WatchStatusNotifierHostRegisteredAsync(Action changed)
        { Registered = changed; return Task.FromResult<IDisposable>(new Subscription()); }
        public Task<IDisposable> WatchStatusNotifierHostUnregisteredAsync(Action changed)
        { Unregistered = changed; return Task.FromResult<IDisposable>(new Subscription()); }
    }
    static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (!condition()) await Task.Delay(10, timeout.Token);
    }
    [Test]
    public async Task WatcherWithoutARegisteredHostDoesNotPermitHidingTheWindow()
    {
        var bus = new Bus(); var watcher = new Watcher();
        using var tray = new TrayAvailability();
        await tray.Start(bus, () => watcher);
        Assert.That(tray.Available, Is.False);
        watcher.Host = true; watcher.Registered!();
        await Until(() => tray.Available);
        watcher.Host = false; watcher.Unregistered!();
        Assert.That(tray.Available, Is.False);
        watcher.Host = true; watcher.Registered!();
        await Until(() => tray.Available);
        bus.Error!(new IOException("Session bus disconnected"));
        Assert.That(tray.Available, Is.False);
    }
    [Test]
    public async Task OldPropertyReplyCannotReviveAnUnregisteredHost()
    {
        var pending = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var bus = new Bus(); var watcher = new Watcher { DelayedProperty = pending.Task };
        using var tray = new TrayAvailability();
        var changes = new List<bool>(); tray.Changed += changes.Add;
        var starting = tray.Start(bus, () => watcher);
        await Until(() => watcher.Unregistered != null);
        watcher.Unregistered!();
        watcher.DelayedProperty = null; watcher.Host = false;
        pending.SetResult(true);
        await starting;
        await Task.Delay(50);
        Assert.That(tray.Available, Is.False);
        Assert.That(changes, Does.Not.Contain(true));
    }
    [Test]
    public async Task ASubscriptionCompletingAfterDisposeIsImmediatelyReleased()
    {
        var pending = new TaskCompletionSource<IDisposable>(TaskCreationOptions.RunContinuationsAsynchronously);
        var bus = new Bus { DelayedSubscription = pending.Task };
        var watcher = new Watcher { Host = true };
        var tray = new TrayAvailability();
        var starting = tray.Start(bus, () => watcher);
        tray.Dispose();
        var subscription = new Subscription(); pending.SetResult(subscription);
        await starting;
        Assert.That(subscription.Disposed, Is.True);
        Assert.That(tray.Available, Is.False);
    }
}
