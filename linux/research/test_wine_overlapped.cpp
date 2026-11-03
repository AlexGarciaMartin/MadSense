// Portable scripted API regressions. No device, Wine or Windows API execution.
#include "wine_overlapped.hpp"
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

struct Reply { bool ok; std::uint32_t error, bytes; };
struct Fake {
    bool immediate = false;
    std::uint32_t write_error = 997, last_error = 997;
    std::vector<std::uint32_t> waits{0};
    std::vector<Reply> results{{true, 0, 48}};
    bool cancel_ok = true;
    std::uint32_t cancel_error = 0;
    unsigned waits_used = 0, results_used = 0, cancellations = 0;
    std::string calls;
    bool submit() { calls += 's'; last_error = write_error; return immediate; }
    std::uint32_t error() { calls += 'e'; return last_error; }
    std::uint32_t wait(std::uint32_t ms) {
        if (ms != 3000) throw std::runtime_error("unbounded wait");
        calls += 'w'; last_error = 6; return waits.at(waits_used++);
    }
    bool result(std::uint32_t &bytes) {
        calls += 'r'; auto reply = results.at(results_used++);
        bytes = reply.bytes; if (!reply.ok) last_error = reply.error;
        return reply.ok;
    }
    bool cancel() { calls += 'c'; ++cancellations; last_error = cancel_error; return cancel_ok; }
};
static void expect(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }
int main()
{
    unsigned tests = 0;
    { Fake f; f.immediate = true; auto r = owned_io::write(f, 48);
      expect(r.passed() && f.calls == "sr" && r.write_error == 0 && r.completion_error == 0,
             "immediate completion must use authoritative result, not stale error"); ++tests; }
    { Fake f; auto r = owned_io::write(f, 48);
      expect(r.passed() && f.calls == "sewr" && r.write_error == 997 && r.completion_error == 0,
             "pending completion"); ++tests; }
    { Fake f; f.immediate = true; f.results = {{true, 0, 0}}; auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && r.succeeded && r.transferred == 0 && f.calls == "sr",
             "immediate success with zero completion bytes must fail"); ++tests; }
    { Fake f; f.waits = {0, 0}; f.results = {{false, 996, 0}, {false, 995, 0}};
      auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && r.cancel_attempted && f.results_used == 2,
             "signaled event is not a substitute for terminal completion"); ++tests; }
    for (auto count : {0u, 47u, 49u}) { Fake f; f.results = {{true, 0, count}};
      auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && r.succeeded && r.transferred == count && r.completion_error == 0,
             "short/oversized completed count must remain failure without residual error"); ++tests; }
    { Fake f; f.write_error = 5; auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && !r.accepted && f.calls == "se", "submission failure"); ++tests; }
    { Fake f; f.waits = {258, 0}; f.results = {{false, 996, 0}, {false, 995, 0}};
      auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && !r.succeeded && r.completion_error == 995
             && f.calls == "sewrecwre", "timeout must cancel then drain before ownership release"); ++tests; }
    { Fake f; f.waits = {258, 0}; f.results = {{false, 996, 0}, {true, 0, 48}};
      f.cancel_ok = false; f.cancel_error = 1168; auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && r.succeeded && r.cancel_error == 1168 && f.results_used == 2,
             "cancel NOT_FOUND race must still drain and retain timeout failure"); ++tests; }
    { Fake f; f.waits = {258, 258}; f.results = {{false, 996, 0}, {false, 996, 0}};
      auto r = owned_io::write(f, 48);
      expect(!r.passed() && !r.completed && f.waits_used == 2 && f.cancellations == 1,
             "undrained IO must prohibit event/buffer release"); ++tests; }
    { Fake f; f.waits = {0xffffffffu, 0}; f.results = {{false, 996, 0}, {true, 0, 48}};
      auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && r.wait_error == 6 && r.cancel_attempted,
             "wait failure still drains and cannot pass"); ++tests; }
    { Fake f; f.results = {{false, 5, 0}}; auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && r.completion_error == 5 && !r.cancel_attempted,
             "terminal completion failure"); ++tests; }
    { Fake f; f.waits = {258}; auto r = owned_io::write(f, 48);
      expect(!r.passed() && r.completed && r.succeeded && !r.cancel_attempted,
             "completion racing initial timeout releases safely but stays failed"); ++tests; }
    std::cout << tests << " overlapped completion policy regressions passed\n";
}
