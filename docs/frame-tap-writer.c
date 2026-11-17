// A small writer of decoded frames for SmoovPlay's frame tap (docs/frame-tap.md), to start an app's own from.
// MIT licence, like SmoovPlay. Windows only.
//
// It sends a moving test picture as a 30 fps game on a 60 Hz output would: each picture twice, the second marked
// as a repeat. Build and run (MSYS2 gcc or MSVC), then start SmoovPlay with `run.py --source tap --display`:
//
//   gcc -O2 -Wall -o frame-tap-writer.exe docs/frame-tap-writer.c
//   frame-tap-writer.exe [frames, default 600] [shared-memory name, default Local\smoovplay-tap]
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TAP_TOTAL (4096u + 64u * 1024u * 1024u)  // the mapping's size, whatever the frames
#define TAP_MAGIC 0x50544643u                  // 'CFTP'
#define TAP_CODEC_NV12 16u
#define TAP_CODEC_BGRA 17u
#define TAP_FLAG_REPEAT 2u
#define TAP_FLAG_NEW 4u

typedef struct
{
	HANDLE map, event;
	uint8_t *base;
	uint32_t slot_count, slot_size;
} Tap;

#define H32(t, off) ((volatile uint32_t *)((t)->base + (off)))
#define H64(t, off) ((volatile uint64_t *)((t)->base + (off)))

// Open (or create) the tap. Either side may start first.
static int tap_open(Tap *t, const char *name)
{
	char event_name[256];
	snprintf(event_name, sizeof(event_name), "%s-frame", name);
	t->map = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, TAP_TOTAL, name);
	if(!t->map)
		return 0;
	t->base = (uint8_t *)MapViewOfFile(t->map, FILE_MAP_ALL_ACCESS, 0, 0, TAP_TOTAL);
	t->event = CreateEventA(NULL, FALSE, FALSE, event_name);
	return t->base != NULL;
}

// Start a stream of decoded frames: slots of whole MiB that hold one frame and its 64-byte header, as many as fit
// in 64 MiB (at most 32, at least 4).
static int tap_begin(Tap *t, uint32_t codec, uint32_t w, uint32_t h, uint32_t fps)
{
	uint64_t frame = codec == TAP_CODEC_BGRA ? (uint64_t)w * h * 4 : (uint64_t)w * h * 3 / 2;
	uint64_t mib = 1024 * 1024;
	t->slot_size = (uint32_t)((64 + frame + mib - 1) / mib * mib);
	t->slot_count = (uint32_t)((64 * mib) / t->slot_size);
	if(t->slot_count > 32)
		t->slot_count = 32;
	if(t->slot_count < 4)
		return 0;
	LARGE_INTEGER f;
	QueryPerformanceFrequency(&f);
	InterlockedExchange((volatile LONG *)H32(t, 0), 0);  // the header is being written
	*H32(t, 4) = 2;                                       // version 2: decoded frames
	*H32(t, 8) = t->slot_count;
	*H32(t, 12) = t->slot_size;
	*H32(t, 16) = codec;
	*H32(t, 20) = w;
	*H32(t, 24) = h;
	*H32(t, 28) = fps;
	*H64(t, 48) = (uint64_t)f.QuadPart;
	*H32(t, 64) = 0;  // features: none (a frame every refresh, no sound ring)
	*H32(t, 92) = 0;  // colour (NV12): auto
	InterlockedExchange64((volatile LONG64 *)H64(t, 32), 0);
	InterlockedIncrement((volatile LONG *)H32(t, 44));     // a new session
	InterlockedExchange((volatile LONG *)H32(t, 0), (LONG)TAP_MAGIC);  // last: the header is complete
	return 1;
}

// One frame, rows packed tightly. flags: TAP_FLAG_NEW, TAP_FLAG_REPEAT or 0 (not known). Never waits.
static void tap_push(Tap *t, const uint8_t *data, uint32_t size, uint32_t flags)
{
	LARGE_INTEGER now;
	QueryPerformanceCounter(&now);
	uint64_t seq = *H64(t, 32);
	uint8_t *slot = t->base + 4096 + (size_t)(seq % t->slot_count) * t->slot_size;
	if(size > t->slot_size - 64)
	{
		size = t->slot_size - 64;
		flags |= 1;  // truncated
	}
	*(volatile uint64_t *)(slot + 0) = UINT64_MAX;  // being rewritten: a reader copying it drops it
	MemoryBarrier();
	*(volatile uint64_t *)(slot + 8) = (uint64_t)now.QuadPart;
	*(volatile uint32_t *)(slot + 16) = size;
	*(volatile int32_t *)(slot + 20) = 0;  // frames lost before this one
	*(volatile uint32_t *)(slot + 24) = 0;  // recovered
	*(volatile uint32_t *)(slot + 28) = flags;
	memcpy(slot + 64, data, size);
	MemoryBarrier();
	*(volatile uint64_t *)(slot + 0) = seq;
	InterlockedExchange64((volatile LONG64 *)H64(t, 32), (LONG64)(seq + 1));
	SetEvent(t->event);
}

static void tap_end(Tap *t)
{
	InterlockedExchange((volatile LONG *)H32(t, 0), 0);
	SetEvent(t->event);
}

int main(int argc, char **argv)
{
	int frames = argc > 1 ? atoi(argv[1]) : 600;
	const char *name = argc > 2 ? argv[2] : "Local\\smoovplay-tap";
	const uint32_t w = 1920, h = 1080;
	Tap tap = {0};
	if(!tap_open(&tap, name) || !tap_begin(&tap, TAP_CODEC_BGRA, w, h, 60))
	{
		fprintf(stderr, "couldn't open the frame tap %s\n", name);
		return 1;
	}
	uint8_t *pic = (uint8_t *)malloc((size_t)w * h * 4);
	LARGE_INTEGER f, t0, now;
	QueryPerformanceFrequency(&f);
	QueryPerformanceCounter(&t0);
	for(int i = 0; i < frames; i++)
	{
		int picture = i / 2;  // a 30 fps game: a new picture every other refresh
		if(i % 2 == 0)
			for(uint32_t y = 0; y < h; y++)
				for(uint32_t x = 0; x < w; x++)
				{
					uint8_t *p = pic + ((size_t)y * w + x) * 4;
					p[0] = (uint8_t)(x + picture * 8);  // B: bands moving right
					p[1] = (uint8_t)(y / 4);           // G
					p[2] = (uint8_t)picture;           // R: the picture's number, for checking
					p[3] = 255;
				}
		tap_push(&tap, pic, w * h * 4, i % 2 == 0 ? TAP_FLAG_NEW : TAP_FLAG_REPEAT);
		// 60 refreshes a second
		LONGLONG due = t0.QuadPart + (LONGLONG)(i + 1) * f.QuadPart / 60;
		for(QueryPerformanceCounter(&now); now.QuadPart < due; QueryPerformanceCounter(&now))
			Sleep(due - now.QuadPart > f.QuadPart / 500 ? 1 : 0);  // over 2 ms to go: sleep, else yield
	}
	tap_end(&tap);
	free(pic);
	printf("sent %d frames\n", frames);
	return 0;
}
