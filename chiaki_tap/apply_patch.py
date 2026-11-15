"""Add the frame tap to a chiaki-ng source checkout (tested against v1.10.1).

    python chiaki_tap/apply_patch.py <chiaki-ng source folder>

Copies frametap.c/.h into gui/ and makes nine small edits to gui/src/streamsession.cpp and one to
gui/CMakeLists.txt. Each edit is an exact-text replacement that must match once, so a chiaki-ng change that moves
the code fails loudly here instead of building something subtly wrong. Running it twice is a no-op. A source
patched by an older version (without the game's sound for instant replay) has to be restored first:
git checkout -- gui/src/streamsession.cpp

With the tap built in, chiaki-ng behaves exactly as before unless CHIAKI_FRAMETAP=1 is set. With it set, besides
copying frames out, it presses buttons when SmoovPlay's delay test asks, holds the sound back by SmoovPlay's
picture delay when SmoovPlay asks (both through the shared memory; see frametap.h), and copies the game's sound as
it arrives into a second ring, for SmoovPlay's instant replay.
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = "frametap_push_audio"  # this version's edits are in
OLD_MARK = "frametap"  # an older version's are

EDITS = [
    ("#include <streamsession.h>\n",
     "#include <streamsession.h>\n#include <frametap.h>\n"),

    ("static void FfmpegFrameCb(ChiakiFfmpegDecoder *decoder, void *user);\n",
     "static void FfmpegFrameCb(ChiakiFfmpegDecoder *decoder, void *user);\n"
     "static bool FrameTapVideoSampleCb(uint8_t *buf, size_t buf_size, int32_t frames_lost, bool frame_recovered, void *user);\n"
     "static void FrameTapQueueAudioCb(StreamSession *session, const QByteArray &buf);\n"
     "static void FrameTapAudioFormatCb(unsigned int rate, unsigned int sample_size);\n"),

    ("\t\tchiaki_session_set_video_sample_cb(&session, chiaki_ffmpeg_decoder_video_sample_cb, ffmpeg_decoder);\n",
     "\t\tif(frametap_begin(chiaki_connect_info.video_profile.codec, chiaki_connect_info.video_profile.width,\n"
     "\t\t\t\tchiaki_connect_info.video_profile.height, chiaki_connect_info.video_profile.max_fps))\n"
     "\t\t{\n"
     "\t\t\tCHIAKI_LOGI(GetChiakiLog(), \"Frame tap on: copying every video frame to shared memory \" FRAMETAP_NAME);\n"
     "\t\t\tchiaki_session_set_video_sample_cb(&session, FrameTapVideoSampleCb, this);\n"
     "\t\t}\n"
     "\t\telse\n"
     "\t\t\tchiaki_session_set_video_sample_cb(&session, chiaki_ffmpeg_decoder_video_sample_cb, ffmpeg_decoder);\n"),

    ("\t\tstatic void TriggerFfmpegFrameAvailable(StreamSession *session)",
     "\t\tstatic bool FrameTapVideoSample(StreamSession *session, uint8_t *buf, size_t buf_size, int32_t frames_lost, bool frame_recovered)\n"
     "\t\t{\n"
     "\t\t\t// Copy first (never blocks), then decode as usual. A reader that joined mid-stream or fell behind\n"
     "\t\t\t// asks for a keyframe; RequestIDR runs on the GUI thread, like chiaki-ng's own calls to it.\n"
     "\t\t\tframetap_push(buf, buf_size, frames_lost, frame_recovered);\n"
     "\t\t\tif(frametap_take_idr_request())\n"
     "\t\t\t\tQMetaObject::invokeMethod(session, [session]() { session->RequestIDR(); }, Qt::QueuedConnection);\n"
     "\t\t\tuint32_t hold_ms = 0;\n"
     "\t\t\tif(uint32_t buttons = frametap_take_press_request(&hold_ms))\n"
     "\t\t\t\tQMetaObject::invokeMethod(session, [session, buttons, hold_ms]() { FrameTapPress(session, buttons, hold_ms); }, Qt::QueuedConnection);\n"
     "\t\t\treturn chiaki_ffmpeg_decoder_video_sample_cb(buf, buf_size, frames_lost, frame_recovered, session->ffmpeg_decoder);\n"
     "\t\t}\n"
     "\t\t// SmoovPlay's delay test: press the buttons, send the state at once, note the time, let go after hold_ms.\n"
     "\t\tstatic void FrameTapPress(StreamSession *session, uint32_t buttons, uint32_t hold_ms)\n"
     "\t\t{\n"
     "\t\t\tsession->keyboard_state.buttons |= buttons;\n"
     "\t\t\tsession->SendFeedbackState();\n"
     "\t\t\tframetap_press_sent();\n"
     "\t\t\tQTimer::singleShot((int)hold_ms, session, [session, buttons]() {\n"
     "\t\t\t\tsession->keyboard_state.buttons &= ~buttons;\n"
     "\t\t\t\tsession->SendFeedbackState();\n"
     "\t\t\t});\n"
     "\t\t}\n"
     "\t\t// Sound held back by SmoovPlay's picture delay (frametap_audio_delay_ms): a first-in first-out buffer of\n"
     "\t\t// exactly that much sound. Each frame in sends one frame's worth out, so the device is fed as steadily as\n"
     "\t\t// before; while the delay grows that's silence, and when it shrinks the oldest sound is skipped.\n"
     "\t\tstatic QByteArray &TapAudioFifo() { static QByteArray fifo; return fifo; }\n"
     "\t\tstatic size_t &TapAudioBytesPerMs() { static size_t n = 192; return n; }  // 48 kHz stereo 16-bit\n"
     "\t\tstatic size_t &TapAudioSampleSize() { static size_t n = 4; return n; }\n"
     "\t\tstatic void FrameTapAudioFormat(unsigned int rate, unsigned int sample_size)\n"
     "\t\t{\n"
     "\t\t\tTapAudioFifo().clear();\n"
     "\t\t\tTapAudioSampleSize() = sample_size ? sample_size : 4;\n"
     "\t\t\tTapAudioBytesPerMs() = (size_t)rate * TapAudioSampleSize() / 1000;\n"
     "\t\t\tframetap_audio_format(rate, (uint32_t)(TapAudioSampleSize() / sizeof(int16_t)));\n"
     "\t\t}\n"
     "\t\tstatic void FrameTapQueueAudio(StreamSession *session, const QByteArray &buf)\n"
     "\t\t{\n"
     "\t\t\tQByteArray &fifo = TapAudioFifo();\n"
     "\t\t\tsize_t ss = TapAudioSampleSize();\n"
     "\t\t\tsize_t want = (size_t)frametap_audio_delay_ms() * TapAudioBytesPerMs() / ss * ss;\n"
     "\t\t\tif(!want && fifo.isEmpty())\n"
     "\t\t\t{\n"
     "\t\t\t\tsession->QueueAudioOutData(buf);\n"
     "\t\t\t\treturn;\n"
     "\t\t\t}\n"
     "\t\t\tfifo.append(buf);\n"
     "\t\t\tsize_t n = (size_t)buf.size();\n"
     "\t\t\tif((size_t)fifo.size() > want + 2 * n)  // the delay shrank: skip the oldest sound\n"
     "\t\t\t\tfifo.remove(0, (int)((size_t)fifo.size() - want - n));\n"
     "\t\t\tif((size_t)fifo.size() >= want + n)\n"
     "\t\t\t{\n"
     "\t\t\t\tsession->QueueAudioOutData(fifo.left((int)n));\n"
     "\t\t\t\tfifo.remove(0, (int)n);\n"
     "\t\t\t}\n"
     "\t\t\telse  // the delay is growing: silence while the buffer fills\n"
     "\t\t\t\tsession->QueueAudioOutData(QByteArray((int)n, 0));\n"
     "\t\t}\n"
     "\t\tstatic void TriggerFfmpegFrameAvailable(StreamSession *session)"),

    ("\tStreamSessionPrivate::TriggerFfmpegFrameAvailable(session);\n}\n",
     "\tStreamSessionPrivate::TriggerFfmpegFrameAvailable(session);\n}\n\n"
     "static bool FrameTapVideoSampleCb(uint8_t *buf, size_t buf_size, int32_t frames_lost, bool frame_recovered, void *user)\n"
     "{\n"
     "\tauto session = reinterpret_cast<StreamSession *>(user);\n"
     "\treturn StreamSessionPrivate::FrameTapVideoSample(session, buf, buf_size, frames_lost, frame_recovered);\n"
     "}\n\n"
     "static void FrameTapQueueAudioCb(StreamSession *session, const QByteArray &buf)\n"
     "{\n"
     "\tStreamSessionPrivate::FrameTapQueueAudio(session, buf);\n"
     "}\n\n"
     "static void FrameTapAudioFormatCb(unsigned int rate, unsigned int sample_size)\n"
     "{\n"
     "\tStreamSessionPrivate::FrameTapAudioFormat(rate, sample_size);\n"
     "}\n"),

    ("\tchiaki_session_fini(&session);\n\tchiaki_opus_decoder_fini(&opus_decoder);\n",
     "\tchiaki_session_fini(&session);\n\tframetap_end();\n\tchiaki_opus_decoder_fini(&opus_decoder);\n"),

    # Sound: SmoovPlay can ask for it to be held back to meet its picture (FrameTapQueueAudio).
    ("\taudio_out_sample_size = sizeof(int16_t) * channels;\n",
     "\taudio_out_sample_size = sizeof(int16_t) * channels;\n\tFrameTapAudioFormatCb(rate, audio_out_sample_size);\n"),

    ("queue_audio:\n\tQueueAudioOutData(buf);\n",
     "queue_audio:\n\tFrameTapQueueAudioCb(this, buf);\n"),

    # Instant replay: the game's sound as it arrives, before chiaki-ng's volume (so a quiet chiaki-ng still records).
    ("void StreamSession::PushAudioFrame(int16_t *og_buf, size_t samples_count)\n{\n",
     "void StreamSession::PushAudioFrame(int16_t *og_buf, size_t samples_count)\n{\n"
     "\tif(audio_out_sample_size)\n"
     "\t\tframetap_push_audio((const uint8_t *)og_buf, samples_count * audio_out_sample_size);\n"),
]

CMAKE_EDIT = ("\tsrc/streamsession.cpp\n", "\tsrc/streamsession.cpp\n\tinclude/frametap.h\n\tsrc/frametap.c\n")


def replace_once(text: str, old: str, new: str, where: str) -> str:
    n = text.count(old)
    if n != 1:
        sys.exit(f"{where}: expected the anchor exactly once, found {n}:\n{old}")
    return text.replace(old, new)


def patch_file(path: Path, edits: list[tuple[str, str]], done_marker: str) -> None:
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    if done_marker in text:
        print(f"{path.name}: already patched")
        return
    if path.suffix == ".cpp" and OLD_MARK in text:
        sys.exit(f"{path.name} has an older frame tap: restore it first (git checkout -- gui/src/{path.name})")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    for old, new in edits:
        text = replace_once(text, old, new, path.name)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", "\r\n") if crlf else text)
    print(f"{path.name}: patched")


def main() -> int:
    root = Path(sys.argv[1])
    gui = root / "gui"
    shutil.copy(HERE / "frametap.h", gui / "include" / "frametap.h")
    shutil.copy(HERE / "frametap.c", gui / "src" / "frametap.c")
    patch_file(gui / "src" / "streamsession.cpp", EDITS, MARK)
    patch_file(gui / "CMakeLists.txt", [CMAKE_EDIT], "src/frametap.c")
    return 0


if __name__ == "__main__":
    sys.exit(main())
