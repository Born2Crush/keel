# Changelog

## Unreleased

**Play**
- **A minimal stats overlay**, now the default: a small line in the corner with the frames on screen, the game's own
  frame rate (amber when it isn't 30 fps) and the delay, and a dot: green, your PC keeps up; amber, it's tight; red,
  it's falling behind. Settings → Display → Stats overlay style has Detailed for everything as before; **S** goes
  Off, Minimal, Detailed.
- **Keys work in the video window straight away.** It no longer needs a click before M, F or Esc do anything, and
  chiaki-ng stays minimised when it shows its window again just after the stream starts. The video window shows its
  keys when it opens (M menu, F fullscreen, Esc quit), and again when you move the mouse over it.
- **The in-video menu stays still** while you go up and down it: the space for each setting's description is
  the same size whichever one is picked, so the rows no longer jump.
- **Test results say quality in plain terms**: the share of each frame that's visibly off from the real one (lower is
  better), instead of dB. It's only given when there's enough to go on: at 90 fps, or with a game that isn't at a
  steady 60, a few seconds of fair frames.
- **Instant replay at 120 fps keeps 60 fps clips** (every other frame). Encoding all 120 a second fell behind,
  dropping frames from clips, and took graphics card time from play.

**For developers** (Experimental)
- **The frame tap is documented for any app** ([docs/frame-tap.md](docs/frame-tap.md)): the shared memory
  chiaki-ng sends PS5 Remote Play's frames through. An app can now send decoded frames too (NV12 or BGRA), and
  say which are repeats, or send pictures only when they change. Pick **Frame tap** on the Play page (or
  `run.py --source tap`). `scripts/frametap_send.py` and `docs/frame-tap-writer.c` are writers to start from.
  Tested with those, not yet with another app.
- **A slimmer chiaki-ng patch**: the tap's code now lives in its own files (`chiaki_tap/frametap_session.cpp`), and
  chiaki-ng's own files only get one-line calls, so a new chiaki-ng version is quicker to patch.

## 0.2.0

**Experimental** (please send feedback)
- **HDR** (Settings → Picture → HDR, off by default). For a PS5 streaming in HDR, pick chiaki-ng's H.265 HDR codec
  and turn this on. **On, shown in SDR** gives the stream its real colours on any monitor, where before it looked
  washed out, for about 0.7 ms of graphics card time a frame. **On, HDR output** keeps the picture HDR10 all the way
  to a monitor with Windows' HDR on; Looks and RTX VSR are off with it, and instant replay clips are saved in SDR.
  Tried with a stand-in for a PS5's HDR stream, not a real one yet; HDR output was checked by reading back the
  10-bit picture it sends, not on an HDR screen.

**Play**
- An HDR stream with HDR off now says how to fix the washed-out colours.

**Launcher**
- The PS5 guide explains the HDR codec instead of saying to turn HDR off. HDR has the Experimental badge and a
  Send feedback button on the Settings page.

**Fixes**
- **Low delay** presentation could hand Windows a black buffer instead of the new frame on some refreshes (found by
  reading back what it sent). Each frame is now copied into place, for one quick graphics card copy.

## 0.1.0 (early release)

The first public version. Windows 10 or 11, with an NVIDIA RTX graphics card recommended.

**Play**
- Plays your PS5 on the PC over Remote Play, through chiaki-ng (the open-source Remote Play app, with SmoovPlay's
  frame tap built in). Nothing is installed on the console.
- A 30 fps game becomes 60, 90 or 120 fps: RIFE 4.22 lite draws the in-between frames (TensorRT or PyTorch on the
  graphics card), and repeats and scene cuts are found so they aren't blended.
- AMD FSR 1 scales the 1080p stream to your monitor (RTX VSR too, when running from source).
- **Sound in sync:** chiaki-ng holds the game's sound back to meet the picture.
- **Delay test:** press L while playing; SmoovPlay presses a button through chiaki-ng and times how long the PS5
  takes to show it.
- **Test mode:** try it with a 60 fps game, played as a 30 fps one, with every AI frame scored against the real one.
- When no picture arrives, the video window says why, for example that chiaki-ng isn't streaming yet.
- **Instant replay:** keep the last 30 seconds to 5 minutes and press R (or Ctrl+Alt+R anywhere) to save it as an
  MP4 at the frame rate you see, with the game's own sound from PS5 Remote Play or a capture card. B saves a before
  & after video: the game's own frames next to SmoovPlay's, split screen or side by side. Recorded on the graphics
  card's video encoder; off until you pick a length.
- **Looks:** stack effects like the nodes of a color grading app (color, color wheels, split tone, LUTs, bloom,
  halation, blur, clarity, chromatic aberration, sharpening, film grain, vignette, scanlines, letterbox, color-blind
  assist) and see the result live on the game while you edit. 14 looks to start from. K turns the look on and off
  and J goes to the next one. Share a look as a short code that anyone can paste. Clips stay clean unless you turn
  on Looks in clips.
- Optional NVIDIA Video Frame Generation (VFG) as the interpolation model on RTX 40 and 50 series cards: set up from
  the launcher, downloaded from NVIDIA under its licence, not part of SmoovPlay.

**Launcher**
- One installer: checks the PC, downloads the AI runtime from official sources, and walks you through pairing the
  PS5. Installs for your Windows user only, without admin rights.
- Settings with quick picks, and the rough picture and speed impact of each choice.
- **Updates itself:** when a new version is out, one click downloads it, checks its signature and installs it,
  keeping your settings, looks and clips.
- A Looks page to make, change and share looks, drawn as you edit on sample screenshots from GTA VI and five other
  games, and shown live on the video while you play.
- Opens where it was last left.

**Experimental** (built and tested with stand-ins, not yet on real hardware: please send feedback)
- **AMD Radeon and Intel Arc cards:** RIFE through DirectML. The same frames as the NVIDIA engine; on the
  development PC's NVIDIA card it took about 1.7 times as long as TensorRT. Not yet tried on an AMD or Intel card.
- **Even out 40-60 fps:** games whose frame rate wanders between 40 and 60 have each frame timed and evenly spaced
  frames drawn from them, instead of some staying on screen twice as long. Tried with real footage played as 43
  and 47 fps games, not yet with a real one.
- **Capture cards:** any console with HDMI, through a capture card, with its sound held back to meet the picture.
  MJPEG cards are decoded on the graphics card. On a PS5, turn off HDCP while you use one.
- **App window:** the window of the app you stream with (the Xbox app, Sony's PS Remote Play app, Moonlight, a
  browser playing a cloud game), captured on the graphics card and shown smoothly over it. The repeat check learns
  the stream's look and keeps to a 30 fps game's rhythm.
- **Low delay** window presentation (a DXGI flip-model swap chain).

**Known limits**
- The installer isn't code-signed yet: Windows shows "Windows protected your PC" (More info, then Run anyway). The
  README explains why, and how to check the file is the real one.
- Tested on one PC so far, with an RTX 4070 Ti SUPER. Other RTX 30, 40 and 50 series cards should work; RTX 20
  cards and laptops aren't tested yet.
- If the picture stays black, or the PS5 says the content can't be shown, turn off HDCP on the PS5 (Settings →
  System → HDMI) and turn it back on when you're done.
- With an app window, the app's sound comes a little ahead of the smooth picture.
- See the README's Known limits for the rest.
