#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
// These calls run on a normal worker, never the audio realtime thread. Returns
// zero on success. Error text is UTF-8, nul-terminated if capacity is nonzero.
int ms_audio_open(void** handle, char* error, uint32_t error_capacity);
int ms_audio_read(void* handle, float* interleaved, uint32_t capacity_frames,
                  uint32_t* frames, uint64_t* dropped_frames, uint32_t* active_clients);
void ms_audio_close(void* handle);
#ifdef __cplusplus
}
#endif
