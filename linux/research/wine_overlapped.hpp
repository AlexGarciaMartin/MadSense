// Completion policy for the owned Windows API observer. No platform I/O here.
#pragma once
#include <cstdint>

namespace owned_io {
constexpr std::uint32_t pending = 997, incomplete = 996;
constexpr std::uint32_t signaled = 0, failed_wait = 0xffffffffu;
constexpr std::uint32_t timeout_ms = 3000;

struct Result {
    std::uint32_t requested = 0, transferred = 0;
    bool immediate = false, accepted = false;
    std::uint32_t write_error = 0;
    bool waited = false;
    std::uint32_t wait_status = 0, wait_error = 0;
    bool completion_checked = false, completed = false, succeeded = false;
    std::uint32_t completion_error = 0;
    bool cancel_attempted = false, cancel_succeeded = false;
    std::uint32_t cancel_error = 0;
    bool drain_waited = false;
    std::uint32_t drain_wait_status = 0, drain_wait_error = 0;
    bool passed() const {
        return accepted && (!waited || wait_status == signaled) && !cancel_attempted
            && completed && succeeded && transferred == requested;
    }
};

// The caller owns the event, OVERLAPPED and buffer until completed is true.
// If false after the bounded drain, it MUST terminate without unwinding them.
// Api methods do not throw and capture no ownership; error() is read only after
// a failed API. Completion bytes come only from result(), including immediate IO.
template<class Api> Result write(Api &api, std::uint32_t requested)
{
    Result r; r.requested = requested;
    r.immediate = api.submit();
    if (!r.immediate) r.write_error = api.error();
    r.accepted = r.immediate || r.write_error == pending;
    if (!r.accepted) { r.completed = true; return r; } // no outstanding operation
    if (!r.immediate) {
        r.waited = true;
        r.wait_status = api.wait(timeout_ms);
        if (r.wait_status == failed_wait) r.wait_error = api.error();
    }
    auto completion = [&] {
        r.completion_checked = true;
        r.succeeded = api.result(r.transferred);
        r.completion_error = r.succeeded ? 0 : api.error();
        r.completed = r.succeeded || (r.completion_error != incomplete && r.completion_error != pending);
    };
    completion();
    if (!r.completed) {
        r.cancel_attempted = true;
        r.cancel_succeeded = api.cancel();
        if (!r.cancel_succeeded) r.cancel_error = api.error();
        // CancelIoEx is a request, not a completion barrier. Even NOT_FOUND may
        // race completion, so inspect the same operation after the bounded wait.
        r.drain_waited = true;
        r.drain_wait_status = api.wait(timeout_ms);
        if (r.drain_wait_status == failed_wait) r.drain_wait_error = api.error();
        completion();
    }
    return r;
}
} // namespace owned_io
