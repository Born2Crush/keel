"""Low delay (DXGI) window presentation, end to end. Plays a clip in a Low delay window and, while it plays, resizes
the window, turns VSync on and off, and goes fullscreen for a few seconds and back (through --control, as the
launcher does). The presenter saves what Direct3D is about to show (FRAMEGEN_PRESENT_SNAP, framegen/dxgi_present.py),
so out\\dxgi_smoke\\*.png show whether the video is upright and fills the window at each step. Opens a window.

    .venv\\Scripts\\python scripts\\dxgi_smoke.py [--clip testdata\\skate_1080p60.mp4] [--no-fullscreen]
    .venv\\Scripts\\python scripts\\dxgi_smoke.py --matrix    # the fullscreen-stall candidates, a table at the end

Checks, printed at the end: the window used DXGI (not the OpenGL fallback), every step left snapshots at the size
asked for, and each snapshot looks like the clip the right way up (it matches a frame of the clip better upright
than flipped).
"""
from __future__ import annotations

import argparse
import ctypes
import re
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "dxgi_smoke"
user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.AdjustWindowRectEx.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]


class ProcessEntry(ctypes.Structure):  # PROCESSENTRY32W
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def family(pid: int) -> set[int]:
    """pid and its descendants (a venv's python.exe starts the real interpreter as a child)."""
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    parents: dict[int, int] = {}
    e = ProcessEntry()
    e.dwSize = ctypes.sizeof(e)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        parents[e.th32ProcessID] = e.th32ParentProcessID
        ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    kernel32.CloseHandle(snap)
    found, grew = {pid}, True
    while grew:
        more = {c for c, p in parents.items() if p in found} - found
        found |= more
        grew = bool(more)
    return found


def video_window(pids: set[int]) -> int | None:
    """Our own visible top-level window: matched by process, nothing else is looked at."""
    hits: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            hits.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return hits[0] if hits else None


def set_client_size(hwnd: int, w: int, h: int) -> None:
    rect = wintypes.RECT(0, 0, w, h)
    style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
    user32.AdjustWindowRectEx(ctypes.byref(rect), style, False, 0)
    user32.SetWindowPos(hwnd, 0, 0, 0, rect.right - rect.left, rect.bottom - rect.top, 0x0002 | 0x0004 | 0x0010)


def upright_score(snap: np.ndarray, frames: list[np.ndarray]) -> tuple[float, float]:
    """Best match (mean abs difference, lower is closer) of the snapshot against the clip's frames, as is and flipped
    top to bottom. Small and grey, so the overlay and the upscaler barely count."""
    def small(img):
        return cv2.cvtColor(cv2.resize(img, (96, 54), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
    s, f = small(snap), [small(x) for x in frames]
    return min(float(np.abs(s - x).mean()) for x in f), min(float(np.abs(s[::-1] - x).mean()) for x in f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip", default=str(ROOT / "testdata" / "skate_1080p60.mp4"))
    ap.add_argument("--no-fullscreen", action="store_true")
    ap.add_argument("--present", default="dxgi", choices=["dxgi", "gl"], help="gl: the same steps, for comparison")
    ap.add_argument("--vsync-fullscreen", action="store_true", help="go fullscreen with VSync on")
    ap.add_argument("--no-snap", action="store_true", help="don't read the back buffer back (rules it out)")
    ap.add_argument("--diag", action="store_true", help="dump every thread's stack every 2 s to out/dxgi_smoke/stacks.txt")
    # the fullscreen-stall candidates (framegen/dxgi_present.py, framegen/io.py)
    ap.add_argument("--mode", default="copy", choices=["direct", "copy"], help="FRAMEGEN_DXGI_MODE")
    ap.add_argument("--buffers", type=int, default=2, choices=[2, 3], help="FRAMEGEN_DXGI_BUFFERS")
    ap.add_argument("--fs-style", default="monitor", choices=["monitor", "borderless"],
                    help="FRAMEGEN_FULLSCREEN_STYLE")
    ap.add_argument("--matrix", action="store_true",
                    help="run every candidate, fullscreen with VSync off and on, and print a table (about 7 minutes)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if a.matrix:
        return matrix(a)
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    env = dict(__import__("os").environ)
    env.update(FRAMEGEN_DXGI_MODE=a.mode, FRAMEGEN_DXGI_BUFFERS=str(a.buffers), FRAMEGEN_FULLSCREEN_STYLE=a.fs_style)
    if not a.no_snap:
        env["FRAMEGEN_PRESENT_SNAP"] = str(out)
    steps = [(4, "size", (1280, 720)), (8, "size", (960, 540)), (12, "set", "vsync true"), (15, "set", "vsync false")]
    if not a.no_fullscreen:
        steps += [(16, "set", "vsync true")] if a.vsync_fullscreen else []
        steps += [(18, "set", "fullscreen true"), (21, "set", "fullscreen false")]
    end = steps[-1][0] + 4
    run = ["run.py"]
    if a.diag:  # run.py under faulthandler: every thread's stack every 2 s, to see where a stall sits
        run = ["-c", "import faulthandler, runpy, sys; sys.argv = sys.argv[1:]; faulthandler.dump_traceback_later("
               f"2, repeat=True, file=open(r'{out / 'stacks.txt'}', 'w')); runpy.run_path('run.py', run_name='__main__')",
               "run.py"]
    cmd = [sys.executable, *run, "--source", "file", a.clip, "--raw", "--realtime", "--display", "--control",
           "--present", a.present, "--settings", "none", "--max-ticks", str(int((end + 6) * 60))]
    log = open(out / "run.log", "w", encoding="utf-8")
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    lines: list[str] = []
    threading.Thread(target=lambda: [lines.append(ln) or log.write(ln) for ln in p.stdout], daemon=True).start()

    hwnd, t0 = None, time.time()
    while hwnd is None and time.time() - t0 < 90 and p.poll() is None:
        time.sleep(0.25)
        hwnd = video_window(family(p.pid))
    if hwnd is None:
        print("the video window never opened; see", out / "run.log")
        p.kill()
        return 1
    start, marks = time.time(), []
    for at, kind, what in steps:
        time.sleep(max(0.0, start + at - time.time()))
        if p.poll() is not None:
            break
        if kind == "size":
            set_client_size(hwnd, *what)
        else:
            p.stdin.write(f"set {what}\n")
            p.stdin.flush()
        marks.append((time.time() - start, f"{kind} {what}"))
        print(f"{marks[-1][0]:5.1f} s  {kind} {what}")
    time.sleep(max(0.0, start + end - time.time()))
    try:
        p.stdin.close()
        p.wait(timeout=30)
        hung = False
    except (subprocess.TimeoutExpired, OSError):
        hung = True  # didn't close when asked: stuck (a Present that never returns, ...)
        p.kill()
    time.sleep(0.5)
    log.close()

    text = "".join(lines)
    dxgi = "window presentation: DXGI flip model" in text
    if a.present == "gl":
        print("presentation: OpenGL (asked for with --present gl)")
    else:
        print("presentation:", "DXGI flip model" if dxgi else "NOT DXGI: " + "; ".join(
            ln.strip() for ln in lines if "[display]" in ln)[:300])
    for ln in lines:
        if "[display]" in ln or "Traceback" in ln or "Error" in ln or "display/encode" in ln:
            print("  log:", ln.rstrip()[:200])
    cap = cv2.VideoCapture(a.clip)
    frames = []
    while len(frames) < 60 * (end + 8):
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    frames = frames[::3]
    snaps = sorted(out.glob("dxgi_*.png"))
    sizes: dict[str, int] = {}
    flipped = black = 0
    for s in snaps:
        img = cv2.imread(str(s))
        if img.max() == 0:  # a buffer nothing was drawn into (direct mode did this: see framegen/dxgi_present.py)
            black += 1
            print(f"  black: {s.name}")
            continue
        up, down = upright_score(img, frames)
        size = re.search(r"_(\d+x\d+)_", s.name).group(1)
        sizes[size] = sizes.get(size, 0) + 1
        if down < up:
            flipped += 1
            print(f"  looks upside down: {s.name} (upright {up:.1f}, flipped {down:.1f})")
    print(f"snapshots: {len(snaps)} at sizes {sizes}; upright: {len(snaps) - flipped - black}/{len(snaps)}; "
          f"black: {black}")
    stalls = [ln.strip() for ln in lines if " took " in ln and "[display]" in ln]
    print(f"slow presents or mode switches (over 250 ms): {len(stalls)}")
    # frames drawn at each window size, from the closing stats ("... 3440x1440 p50 ... (n=180)"): the biggest size is
    # fullscreen, about 3 s of it, so about 180 at 60 fps if nothing stalled
    drawn = {f"{w}x{h}": int(n) for w, h, n in re.findall(r"(\d+)x(\d+) p50 [^;]*?\(n=(\d+)\)", text)}
    big = max(drawn, key=lambda s: int(s.split("x")[0]) * int(s.split("x")[1])) if drawn else None
    print(f"frames drawn by size: {drawn or 'none (no closing stats)'}; stuck at the end: {'yes' if hung else 'no'}")
    print(f"RESULT {a.present} {a.mode} {a.buffers} {a.fs_style} vsync-fullscreen={'on' if a.vsync_fullscreen else 'off'}"
          f" | stalls {len(stalls)} | worst {max([int(m) for m in re.findall(r'took (\d+) ms', ' '.join(stalls))] or [0])} ms"
          f" | fullscreen frames {drawn.get(big, 0) if big and not a.no_fullscreen else '-'} | stuck {'yes' if hung else 'no'}"
          f" | flipped {flipped}/{len(snaps)} | black {black}/{len(snaps)}")
    if a.present == "gl":  # snapshots are DXGI-only: here only the run itself is checked
        return 0 if p.returncode == 0 and not stalls and not hung else 1
    return 0 if dxgi and (snaps or a.no_snap) and not flipped and not black and not stalls and not hung else 1


def matrix(a) -> int:
    """Every fullscreen-stall candidate, with VSync off and on in fullscreen, one run each (a window opens each time)."""
    combos = [("direct", 2, "monitor"), ("copy", 2, "monitor"), ("direct", 3, "monitor"),
              ("direct", 2, "borderless"), ("copy", 2, "borderless")]
    rows = []
    for mode, buffers, style in combos:
        for vs in (False, True):
            name = f"{mode}-{buffers}-{style}-vsync{'on' if vs else 'off'}"
            cmd = [sys.executable, __file__, "--clip", a.clip, "--mode", mode, "--buffers", str(buffers),
                   "--fs-style", style, "--out", str(OUT / "matrix" / name)] + (["--vsync-fullscreen"] if vs else [])
            print(f"--- {name}", flush=True)
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            row = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), f"RESULT {name} | no result")
            rows.append(("ok  " if r.returncode == 0 else "FAIL") + "  " + row[7:])
            print(rows[-1], flush=True)
    print("\n".join(["", "mode / buffers / fullscreen / VSync in fullscreen | ...", *rows]))
    return 0 if all(r.startswith("ok") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
