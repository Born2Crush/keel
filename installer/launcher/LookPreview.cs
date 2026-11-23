// The Looks page's preview: the picked look drawn on a sample game picture, redrawn as it changes.
//
// The drawing is the pipeline's own (framegen/looks_preview.py): a small Python process, started the first time the
// page opens and kept for as long as the launcher runs. Each request names the look, the picture and the size it's
// shown at, and the answer is a picture file. One request is out at a time: changes made meanwhile are sent together
// once it answers, so dragging a slider redraws as fast as the process keeps up (about a millisecond a picture with
// OpenGL). The process ends when the launcher closes its stdin, or when the launcher ends.
//
// The pictures (framegen/samples) are screenshots from games, credited under the preview; holding the mouse on the
// preview shows the picture without the look. A wide window puts the preview beside the nodes, a narrow one above.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace Keel
{
    partial class Ui
    {
        class LookSample { public string Id, Game, Scene, Credit, Path; }

        List<LookSample> lookSamples = new List<LookSample>();
        Process pvProc;
        StreamWriter pvIn;
        bool pvReady, pvBusy, pvDirty, pvHolding;
        int pvSeq, pvFileNo, pvStarts;
        string pvDir;
        BitmapSource pvLast;  // the newest preview
        string pvLastFor;  // the picture it was drawn on
        readonly Dictionary<string, BitmapSource> pvOriginals = new Dictionary<string, BitmapSource>();
        DispatcherTimer pvSizeTimer;
        const double WidePreview = 860;  // from this width of the page's right side, the preview goes beside the nodes

        void WireLookPreview()
        {
            Click("BtnPreviewPrev", () => StepSample(-1));
            Click("BtnPreviewNext", () => StepSample(1));
            var box = E<Grid>("LookPreviewBox");
            box.MouseLeftButtonDown += (s, e) => { pvHolding = true; ShowPreviewPicture(); e.Handled = true; };
            box.MouseLeftButtonUp += (s, e) => { pvHolding = false; ShowPreviewPicture(); };
            box.MouseLeave += (s, e) => { if (pvHolding) { pvHolding = false; ShowPreviewPicture(); } };
            E("LookRight").SizeChanged += (s, e) => LayoutPreview();
            Window.Closed += (s, e) => StopPreview();
        }

        static void ParseSamples(Dictionary<string, object> root, List<LookSample> into)
        {
            into.Clear();
            if (!root.ContainsKey("samples")) return;
            foreach (Dictionary<string, object> s in (object[])root["samples"])
                into.Add(new LookSample { Id = (string)s["id"], Game = (string)s["game"], Scene = (string)s["scene"], Credit = (string)s["credit"], Path = (string)s["path"] });
        }

        LookSample CurrentSample()
        {
            if (lookSamples.Count == 0) return null;
            string id = CaptureDevices.Pref(prefs, "look-sample:");
            return lookSamples.FirstOrDefault(s => s.Id == id) ?? lookSamples[0];
        }

        void StepSample(int by)
        {
            var s = CurrentSample();
            if (s == null) return;
            int i = (lookSamples.IndexOf(s) + by + lookSamples.Count) % lookSamples.Count;
            CaptureDevices.Choose(prefs, "look-sample:", lookSamples[i].Id);
            try { Prefs.Save(prefs); } catch (Exception) { }
            RenderPreviewChrome();
            ShowPreviewPicture();
            RequestPreview();
        }

        // ---- the card
        void RenderPreviewChrome()
        {
            var s = CurrentSample();
            Show(E("LookPreviewCard"), s != null && Picked() != null);
            if (s == null) return;
            T("LookPreviewTitle").Text = s.Game + "  ·  " + s.Scene;
            int c = s.Credit.IndexOf('©');
            T("LookPreviewCredit").Text = c >= 0 ? s.Credit.Substring(c) : s.Credit;
            T("LookPreviewCredit").ToolTip = s.Credit + ". A screenshot from the game, to show what the look does; not part of Keel's licence.";
            T("LookPreviewCount").Text = (lookSamples.IndexOf(s) + 1) + " / " + lookSamples.Count;
            var e = Picked();
            T("LookPreviewBadge").Text = pvHolding || pvLast == null || pvLastFor != s.Id ? "ORIGINAL" : (e == null ? "LOOK" : e.Name.ToUpperInvariant());
            LayoutPreview();
        }

        void LayoutPreview()
        {
            var right = E<Grid>("LookRight");
            var card = E<Border>("LookPreviewCard");
            var box = E<Grid>("LookPreviewBox");
            var scroll = E<ScrollViewer>("LookScroll");
            double width = right.ActualWidth, height = right.ActualHeight;
            if (width <= 0) return;
            bool wide = width >= WidePreview;
            double w;
            if (wide)
            {
                w = Math.Min(720, Math.Floor((width - 22) * 0.5));
                Grid.SetRow(card, 2); Grid.SetColumn(card, 1); Grid.SetColumnSpan(card, 1);
                card.Margin = new Thickness(22, 12, 0, 0);
                card.Width = w + 12;
                Grid.SetColumnSpan(scroll, 1);
            }
            else
            {
                double maxH = Math.Max(130, height * 0.34);
                w = Math.Min(width - 12, maxH * 16 / 9);
                Grid.SetRow(card, 1); Grid.SetColumn(card, 0); Grid.SetColumnSpan(card, 2);
                card.Margin = new Thickness(0, 12, 0, 0);
                card.Width = double.NaN;
                Grid.SetColumnSpan(scroll, 2);
            }
            w = Math.Floor(w);
            if (Math.Abs(box.Width - w) < 1) return;
            box.Width = w;
            box.Height = Math.Floor(w * 9 / 16);
            // Redraw at the new size once it settles.
            if (pvSizeTimer == null)
            {
                pvSizeTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(150) };
                pvSizeTimer.Tick += (s, e) => { pvSizeTimer.Stop(); RequestPreview(); };
            }
            pvSizeTimer.Stop();
            pvSizeTimer.Start();
        }

        /// <summary>The preview's size in screen pixels: what it's drawn at, so grain and scanlines look as they will
        /// in the video window.</summary>
        void PreviewPixels(out int w, out int h)
        {
            var box = E<Grid>("LookPreviewBox");
            double scale = 1;
            var src = PresentationSource.FromVisual(Window);
            if (src != null && src.CompositionTarget != null) scale = src.CompositionTarget.TransformToDevice.M11;
            double bw = double.IsNaN(box.Width) ? box.ActualWidth : box.Width;
            w = Math.Max(64, Math.Min(1920, (int)Math.Round(bw * scale / 2) * 2));
            h = (int)Math.Round(w * 9 / 16.0);
        }

        void ShowPreviewPicture()
        {
            var s = CurrentSample();
            if (s == null) return;
            var img = E<Image>("LookPreviewImage");
            bool look = !pvHolding && pvLast != null && pvLastFor == s.Id;
            img.Source = look ? pvLast : Original(s);
            var e = Picked();
            T("LookPreviewBadge").Text = look ? (e == null ? "LOOK" : e.Name.ToUpperInvariant()) : "ORIGINAL";
        }

        BitmapSource Original(LookSample s)
        {
            BitmapSource b;
            if (pvOriginals.TryGetValue(s.Id, out b)) return b;
            try { b = LoadPicture(s.Path, 960); }
            catch (Exception ex) { Program.Trace("look preview: " + ex.Message); return null; }
            pvOriginals[s.Id] = b;
            return b;
        }

        static BitmapSource LoadPicture(string file, int decodeWidth)
        {
            var bmp = new BitmapImage();
            using (var ms = new MemoryStream(File.ReadAllBytes(file)))  // (read now: the file is written again later)
            {
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;  // (from a stream: WPF's image cache, keyed by file, isn't used)
                if (decodeWidth > 0) bmp.DecodePixelWidth = decodeWidth;
                bmp.StreamSource = ms;
                bmp.EndInit();
            }
            bmp.Freeze();
            return bmp;
        }

        // ---- the drawing process
        void StartPreview()
        {
            if (pvProc != null || !paths.RuntimeReady || pvStarts >= 3) return;  // (three tries: then the originals only)
            pvStarts++;
            try
            {
                pvDir = Path.Combine(Path.GetTempPath(), "Keel-preview");
                Directory.CreateDirectory(pvDir);
                var psi = Proc.Python(paths.Python, "-m framegen.looks_preview", paths.App);
                psi.RedirectStandardInput = true;
                var pr = new Process { StartInfo = psi, EnableRaisingEvents = true };
                pr.OutputDataReceived += (s, e) =>
                {
                    if (e.Data == null || !e.Data.StartsWith("{")) return;
                    string line = e.Data;
                    OnUi(() => OnPreviewLine(pr, line));
                };
                pr.ErrorDataReceived += (s, e) => { if (e.Data != null && e.Data.Trim().Length > 0) Program.Trace("look preview: " + e.Data); };
                pr.Exited += (s, e) => OnUi(() =>
                {
                    if (pvProc != pr) return;
                    Program.Trace("look preview: the process ended");
                    pvProc = null; pvIn = null; pvReady = false; pvBusy = false;
                    Show(E("LookPreviewWait"), false);
                });
                pr.Start();
                pvIn = new StreamWriter(pr.StandardInput.BaseStream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
                pr.BeginOutputReadLine();
                pr.BeginErrorReadLine();
                pvProc = pr;
                Show(E("LookPreviewWait"), true);
            }
            catch (Exception ex)
            {
                Program.Trace("look preview: " + ex.Message);
                pvProc = null;
                pvIn = null;
            }
        }

        void StopPreview()
        {
            var pr = pvProc;
            pvProc = null;
            pvReady = false;
            if (pr == null) return;
            try { if (pvIn != null) pvIn.Dispose(); } catch (Exception) { }  // its stdin closes: it ends by itself
            pvIn = null;
            try { if (!pr.WaitForExit(800)) pr.Kill(); } catch (Exception) { }
        }

        void OnPreviewLine(Process pr, string line)
        {
            if (pr != pvProc) return;
            var d = ParseCli(line);
            if (d.ContainsKey("ready"))
            {
                pvReady = true;
                if (lookSamples.Count == 0) { ParseSamples(d, lookSamples); RenderPreviewChrome(); }
                RequestPreview();
                return;
            }
            pvBusy = false;
            object file;
            if (d.TryGetValue("file", out file) && d.ContainsKey("sample"))
            {
                try
                {
                    pvLast = LoadPicture((string)file, 0);
                    pvLastFor = (string)d["sample"];
                    Show(E("LookPreviewWait"), false);
                    ShowPreviewPicture();
                }
                catch (Exception ex) { Program.Trace("look preview: " + ex.Message); }
            }
            else if (d.ContainsKey("error")) Program.Trace("look preview: " + d["error"]);
            if (pvDirty) { pvDirty = false; RequestPreview(); }
        }

        /// <summary>Draw the picked look on the current picture (now, or once the one being drawn is done).</summary>
        void RequestPreview()
        {
            if (page != "PageLooks") return;
            var e = Picked();
            var s = CurrentSample();
            if (e == null || s == null) return;
            if (pvProc == null) { StartPreview(); return; }  // (its first line asks again)
            if (!pvReady) return;
            if (pvBusy) { pvDirty = true; return; }
            int w, h;
            PreviewPixels(out w, out h);
            pvFileNo ^= 1;
            var req = new Dictionary<string, object>
            {
                { "id", ++pvSeq }, { "look", e.Look }, { "sample", s.Id }, { "width", w }, { "height", h },
                { "out", Path.Combine(pvDir, "preview-" + pvFileNo + ".bmp") },
            };
            try
            {
                pvIn.WriteLine(Json.Serialize(req));
                pvBusy = true;
            }
            catch (Exception ex)
            {
                Program.Trace("look preview: " + ex.Message);
                StopPreview();
            }
        }
    }
}
