"""Join a PS5 stream that is already running, instead of telling the player to restart chiaki-ng.

The build runs this against the sources it packages. A second run changes nothing.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sub(path: pathlib.Path, old: str, new: str, label: str, already: str) -> None:
    """Replace `old` with `new` once. `already` is text only the patched file has; "" means `old` is gone."""
    file = path.read_text(encoding="utf-8")
    if (already and already in file) or (not already and old not in file):
        print(f"already {label}")
        return
    if old not in file:
        raise SystemExit(f"missing anchor for {label} in {path}")
    path.write_text(file.replace(old, new, 1), encoding="utf-8")
    print(f"patched {label}")


def main() -> int:
    tap = ROOT / "framegen" / "chiaki_tap.py"
    run = ROOT / "run.py"
    sub(tap,
        "                 tap_name: str = NAME, test=None, app: bool = False) -> None:",
        "                 tap_name: str = NAME, test=None, app: bool = False, on_missing_header=None) -> None:",
        "reader argument", "on_missing_header=None")
    sub(tap,
        '        self._header_file = Path(tempfile.gettempdir()) / (tap_name.split("\\\\")[-1] + "-header.bin")\n',
        '        self._tap_name = tap_name\n'
        '        self._header_file = Path(tempfile.gettempdir()) / (tap_name.split("\\\\")[-1] + "-header.bin")\n'
        "        self._on_missing_header = on_missing_header\n"
        "        self._rescued = False\n",
        "reader state", "self._tap_name = tap_name")
    sub(tap,
        """                late = passed = seq > 0
                if late:  # joining a running stream: an earlier run.py may have saved its header
                    params = self._load_header(session, h.codec)
                    if params:
                        print(f"{self.tag} joined a running stream, using its saved codec header")
                    else:
                        self._ask_idr()
                self._no_header = late and not params
""",
        """                late = passed = seq > 0
                if late:  # joining a running stream: this connection, or an earlier one, may have saved its header
                    params = self._load_header(session, h.codec)
                    if params:
                        print(f"{self.tag} joined a running stream, using its saved codec header")
                    elif self._on_missing_header is not None and not self._rescued:
                        self._rescued = True
                        threading.Thread(target=self._on_missing_header, daemon=True).start()
                    else:
                        self._ask_idr()
                self._no_header = False
""",
        "join without the banner", "self._rescued = True")
    sub(tap,
        """            if late and not params and not told and time.perf_counter() - t_join > 3:
                print(f"{self.tag} this stream was already running, so its codec header went past before the "
                      "pipeline started. " + ("Restart the stream in the app" if self.app else
                                              "In chiaki-ng press Ctrl+O, click the x to end the stream, then connect "
                                              "again") + ": the pipeline picks up the new stream from its start.")
                told = True
""",
        "",
        "drop the restart note", "")
    sub(tap,
        """    def _save_header(self, session: int, codec: int, params: bytes) -> None:
        try:
            self._header_file.write_bytes(struct.pack("<II", session, codec) + params)
        except OSError:
            pass

    def _load_header(self, session: int, codec: int) -> bytes:
        \"\"\"The codec header an earlier run.py saved for this same stream, if any.\"\"\"
        try:
            b = self._header_file.read_bytes()
        except OSError:
            return b""
        return b[8:] if len(b) > 8 and struct.unpack_from("<II", b) == (session, codec) else b""
""",
        """    def _save_header(self, session: int, codec: int, params: bytes) -> None:
        from .session_owner import remember_header
        remember_header(self._tap_name, session, codec, params)

    def _load_header(self, session: int, codec: int) -> bytes:
        \"\"\"The codec header saved for this stream, or for an earlier connection of the same codec.\"\"\"
        from .session_owner import header_params, saved_headers
        exact = b""
        for blob in saved_headers(self._tap_name):
            if len(blob) <= 8 or struct.unpack_from("<II", blob)[1] != codec:
                continue
            if struct.unpack_from("<II", blob)[0] == session:
                return blob[8:]
            exact = exact or header_params(blob, codec)
        return exact
""",
        "keep the header across connections", "remember_header")
    sub(tap,
        """        if self._no_header:
            return ("This stream started before Keel, so its start is missing.\\n"
                    + ("Restart the stream in the app" if self.app else
                       "In chiaki-ng press Ctrl+O, click the x to end the stream, then connect again"))
        return ""
""",
        '        return ""\n',
        "no restart hint", "")
    sub(run,
        """        src = ChiakiSource(a.chiaki_decoder, wait_s=600, log_path=a.chiaki_log or frame_log(a.results),
                           tap_name=tap_name, test=test)
""",
        """        def catch_stream() -> None:
            from framegen.session_owner import catch_running_stream
            catch_running_stream()

        src = ChiakiSource(a.chiaki_decoder, wait_s=600, log_path=a.chiaki_log or frame_log(a.results),
                           tap_name=tap_name, test=test,
                           on_missing_header=catch_stream if os.environ.get("KEEL_OWN_SESSION") == "1" and not a.chiaki_tap else None)
""",
        "catch a headerless stream once", "catch_running_stream")
    return 0


if __name__ == "__main__":
    sys.exit(main())
