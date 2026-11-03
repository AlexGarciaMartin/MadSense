"""Required assertions for the optional second, fresh protocol sessions."""
CORE_CASES = {"core_distinct_l2_r2_and_explicit_off_delivered"}
APP_CASES = {
    "app_distinct_l2_r2_and_explicit_off_delivered",
    "app_headset_input_and_exact_audio_route", "app_speaker_return_input_and_exact_audio_route",
    "app_edge_auto_uses_real_enumerator_settings_and_kernel_persona",
    "app_edge_each_fn_and_paddle_preserved", "app_edge_pause_restores_physical_and_virtual_state",
}


def verify_extension(result, required):
    extension = result.get("protocolExtension", {})
    cases = extension.get("cases", [])
    names = [case.get("name") for case in cases]
    if (extension.get("requested") is not True or extension.get("result") != "passed"
            or any(case.get("passed") is not True for case in cases)
            or len(names) != len(set(names)) or set(names) != required):
        raise RuntimeError("Protocol extension lacks its exact required assertions")


def verify_protocol_results(core, app, token):
    from qemu_core import verify_core_result
    from qemu_app import verify_app_result
    verify_core_result(core, token)
    verify_app_result(app, token)
    verify_extension(core, CORE_CASES)
    verify_extension(app, APP_CASES)
