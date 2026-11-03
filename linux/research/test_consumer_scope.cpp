// Portable scope-policy regression; never opens a marker, device or Wine.
#include "consumer_scope.hpp"
#include <iostream>
#include <string>

static void expect(bool value, const char *message)
{
    if (!value) throw std::runtime_error(message);
}
int main()
{
    unsigned tests = 0;
    expect(std::wstring_view(owned_scope::marker_path(false, {})) == L"Z:\\etc\\madsense-ci-owner",
           "Distribution Wine must retain its original marker"); ++tests;
    expect(std::wstring_view(owned_scope::marker_path(true, L"pressure-vessel"))
               == L"Z:\\run\\host\\etc\\madsense-ci-owner",
           "GE must use the original marker through the fixed read-only host mount"); ++tests;
    for (auto value : {L"", L"PRESSURE-VESSEL", L"pressure-vessel ", L"pressure-vessel/../etc",
                       L"default", L"Z:\\fake\\marker", L"/run/host/etc/madsense-ci-owner"}) {
        bool rejected = false;
        try { (void)owned_scope::marker_path(true, value); }
        catch (const std::runtime_error &) { rejected = true; }
        expect(rejected, "Unknown layout must not fall back to another marker"); ++tests;
    }
    std::wstring embedded = L"pressure-vessel"; embedded.push_back(L'\0'); embedded += L"ignored";
    bool rejected = false;
    try { (void)owned_scope::marker_path(true, embedded); }
    catch (const std::runtime_error &) { rejected = true; }
    expect(rejected, "Embedded null must not select a valid layout"); ++tests;
    std::cout << "{\"scope\":\"owned-marker-layout-selection\",\"passed\":" << tests
              << ",\"deviceAccess\":false,\"wineExecuted\":false}\n";
}
