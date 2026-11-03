using MadSense.Mac;

namespace MadSense.Mac.Tests;

public class BridgeRetryPolicyTests
{
    [Test]
    public void VirtualHidFailureStopsReacquisitionUntilExplicitResume()
    {
        var retry = new BridgeRetryPolicy();
        Assert.That(retry.CanAttempt(100), Is.True);
        retry.RecordFailure(new VirtualHidUnavailableException(unchecked((int)0xe00002bc), "Signature rejected."), 100);

        Assert.Multiple(() =>
        {
            Assert.That(retry.RequiresExplicitRetry, Is.True);
            Assert.That(retry.CanAttempt(10100), Is.False);
            Assert.That(retry.CanAttempt(long.MaxValue), Is.False);
        });
        // Discovery delays and subsequent transient failures must not silently
        // clear a blocked state while the service continues emitting status.
        retry.Delay(200, 1000);
        retry.RecordFailure(new IOException("Transient physical failure."), 200);
        Assert.That(retry.CanAttempt(long.MaxValue), Is.False);

        retry.Resume();
        Assert.That(retry.RequiresExplicitRetry, Is.False);
        Assert.That(retry.CanAttempt(201), Is.True);
        // A user-requested retry that still fails blocks again after that attempt.
        retry.RecordFailure(new VirtualHidUnavailableException(1, "Still unavailable."), 201);
        Assert.That(retry.CanAttempt(long.MaxValue), Is.False);
    }

    [Test]
    public void PhysicalConnectionFailuresKeepAutomaticRetry()
    {
        var retry = new BridgeRetryPolicy();
        retry.RecordFailure(new IOException("Bluetooth disconnected."), 100);
        Assert.That(retry.RequiresExplicitRetry, Is.False);
        Assert.That(retry.CanAttempt(10099), Is.False);
        Assert.That(retry.CanAttempt(10100), Is.True);
        retry.Delay(10100, 1000);
        Assert.That(retry.CanAttempt(11099), Is.False);
        Assert.That(retry.CanAttempt(11100), Is.True);
    }

    [Test]
    public void FailurePreservesTheActualNativeErrorCode()
    {
        const int code = unchecked((int)0xe00002d8);
        var error = new VirtualHidUnavailableException(code, "Publication failed.");
        Assert.That(error.NativeErrorCode, Is.EqualTo(code));
        Assert.That(error.Message, Does.Contain("0xe00002d8").And.Contain("Publication failed."));
    }

    [Test]
    public void NativeLibraryFailureIsTerminalWithoutInventingAnIOKitCode()
    {
        var cause = new DllNotFoundException("madsense_hid");
        var error = new VirtualHidUnavailableException(cause);
        var retry = new BridgeRetryPolicy();
        retry.RecordFailure(error, 100);
        Assert.That(error.NativeErrorCode, Is.Null);
        Assert.That(error.InnerException, Is.SameAs(cause));
        Assert.That(error.Message, Does.Contain("madsense_hid"));
        Assert.That(retry.CanAttempt(long.MaxValue), Is.False);
    }

    [TestCase(0u)]
    [TestCase(1u)]
    [TestCase(3u)]
    [TestCase(uint.MaxValue)]
    public void IncompatibleNativeAbiBlocksBeforeCreation(uint version)
    {
        var error = Assert.Throws<VirtualHidUnavailableException>(() => MacVirtualHid.ValidateAbi(version));
        var retry = new BridgeRetryPolicy();
        retry.RecordFailure(error!, 100);
        Assert.That(error!.NativeErrorCode, Is.Null);
        Assert.That(error.Message, Does.Contain($"ABI {version}").And.Contain("ABI 2 is required"));
        Assert.That(retry.CanAttempt(long.MaxValue), Is.False);
    }

    [Test]
    public void CurrentNativeAbiAllowsCreation() => Assert.DoesNotThrow(() => MacVirtualHid.ValidateAbi(2));
}
