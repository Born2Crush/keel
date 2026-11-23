using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace Keel
{

    // ------------------------------------------------------------------------------------------------ play
    /// <summary>The chiaki-ng the launcher started. Its pid and start time are saved so a later launcher run can tell
    /// it from a chiaki-ng started some other way, which wouldn't have the frame tap switched on.</summary>
    static class Chiaki
    {
        static string Marker(Paths p) { return Path.Combine(p.Runtime, "chiaki.pid"); }
        static bool IsChiakiExe(Paths p) { return Path.GetFileNameWithoutExtension(p.Chiaki).Equals("chiaki", StringComparison.OrdinalIgnoreCase); }

        public static Process Ours(Paths p)
        {
            try
            {
                var parts = File.ReadAllText(Marker(p)).Split(' ');
                var pr = Process.GetProcessById(int.Parse(parts[0]));
                if (!pr.HasExited && pr.StartTime.Ticks.ToString() == parts[1].Trim()) return pr;
            }
            catch (Exception) { }
            return null;
        }

        /// <summary>chiaki-ng windows the launcher didn't start.</summary>
        public static Process[] Others(Paths p)
        {
            if (!IsChiakiExe(p)) return new Process[0];
            var ours = Ours(p);
            return Process.GetProcessesByName("chiaki").Where(x => ours == null || x.Id != ours.Id).ToArray();
        }

        /// <summary>1080p, 60 fps, H.265, 100 Mbps, the profile in framegen/stream_quality.py. Written before
        /// chiaki-ng starts, into the settings hive it will load (the current profile, or the default).</summary>
        public static string ApplyVideoProfile()
        {
            const int bitrate = 100000;
            try
            {
                string profile = null;
                using (var def = Registry.CurrentUser.OpenSubKey(@"Software\\Chiaki\\Chiaki\\settings"))
                    profile = def?.GetValue("current_profile") as string;
                if (!string.IsNullOrEmpty(profile) && (profile.IndexOf('\\') >= 0 || profile.IndexOf('/') >= 0))
                    profile = null;
                string app = string.IsNullOrEmpty(profile) ? "Chiaki" : "Chiaki-" + profile.Trim();
                using (var key = Registry.CurrentUser.CreateSubKey(@"Software\\Chiaki\\" + app + @"\\settings"))
                {
                    key.SetValue("resolution_local_ps5", "1080p");
                    key.SetValue("resolution_remote_ps5", "1080p");
                    key.SetValue("fps_local_ps5", 60, RegistryValueKind.DWord);
                    key.SetValue("fps_remote_ps5", 60, RegistryValueKind.DWord);
                    key.SetValue("bitrate_local_ps5", bitrate, RegistryValueKind.DWord);
                    key.SetValue("bitrate_remote_ps5", bitrate, RegistryValueKind.DWord);
                    key.SetValue("codec_local_ps5", "h265");
                    key.SetValue("codec_remote_ps5", "h265");
                }
            }
            catch (Exception e)
            {
                return "[keel] couldn't set chiaki-ng's video profile (" + e.Message + ")";
            }
            return "[keel] chiaki-ng video: 1080p 60 fps H.265 100 Mbps";
        }

        public static bool Start(Paths p, Action<string> log)
        {
            if (Process.GetProcessesByName("chiaki").Length > 0)
            {
                log("[launcher] chiaki-ng is already open");
                return true;
            }
            if (string.IsNullOrEmpty(p.Chiaki) || !File.Exists(p.Chiaki))
                p.Chiaki = Paths.ChiakiExe(p.Install);
            if (string.IsNullOrEmpty(p.Chiaki) || !File.Exists(p.Chiaki))
            {
                log("[launcher] chiaki-ng isn't at " + p.Chiaki + "; start it yourself with CHIAKI_FRAMETAP=1");
                return false;
            }
            var psi = new ProcessStartInfo(p.Chiaki, p.ChiakiArgs)
            {
                UseShellExecute = false, CreateNoWindow = true,  // no console window; chiaki-ng's own windows still open
                WorkingDirectory = p.Dev ? p.App : Path.GetDirectoryName(p.Chiaki),
            };
            psi.EnvironmentVariables["CHIAKI_FRAMETAP"] = "1";
            log(ApplyVideoProfile());
            string renderer = Renderer(psi.EnvironmentVariables["CHIAKI_FRAMETAP_RENDERER"]);
            if (renderer != null) psi.EnvironmentVariables["CHIAKI_FRAMETAP_RENDERER"] = renderer;
            var pr = Process.Start(psi);
            try
            {
                Directory.CreateDirectory(p.Runtime);
                File.WriteAllText(Marker(p), pr.Id + " " + pr.StartTime.Ticks);
            }
            catch (Exception) { }
            log("[launcher] started chiaki-ng" + (renderer == "opengl"
                ? " (drawing with OpenGL this time: chiaki-ng's Vulkan has crashed on AMD and Intel graphics; Keel shows the picture)"
                : ""));
            return true;
        }

        /// <summary>What chiaki-ng draws its own window with (CHIAKI_FRAMETAP_RENDERER, our chiaki-ng's switch, for that
        /// session only): OpenGL without an NVIDIA card, where chiaki-ng's default, Vulkan (with Vulkan decoding), has
        /// crashed (issue 19, a Radeon RX 9070 XT). Keel draws the stream itself, so chiaki-ng's window, hidden
        /// behind it, loses nothing. On NVIDIA it stays as set: Vulkan works there, and an HDR stream needs it. One set
        /// already (e.g. "vulkan" to try it anyway) is kept.</summary>
        public static string Renderer(string already)
        {
            if (!string.IsNullOrEmpty(already)) return already;
            if (SystemCheck.GpuName == "unknown" && SystemCheck.OtherGpu == null) SystemCheck.QueryGpu();
            return SystemCheck.GpuName == "unknown" && SystemCheck.OtherGpu != null ? "opengl" : null;
        }

        public static void Close(Process[] procs)
        {
            foreach (var pr in procs)
                try
                {
                    pr.CloseMainWindow();
                    if (!pr.WaitForExit(5000)) pr.Kill();
                }
                catch (Exception) { }
        }
    }

    enum PlayState { Idle, Starting, Waiting, Found, Building, Playing, Ended, Stopped, Error }

    /// <summary>One video source, from the pipeline's list (python -m framegen.sources --json, framegen/sources.py).</summary>
    class SourceDef
    {
        public string Kind, Name, Guide;
        public bool Hidden, TestMode = true;
        public bool Experimental;  // offered, but built without the hardware to test it: a badge and Send feedback
        public bool RemotePlay { get { return Kind == "chiaki"; } }
    }

    /// <summary>The video sources. PS5 Remote Play is the one offered; capture cards and video files work in the
    /// pipeline but are hidden until they're ready (KEEL_SOURCES=all shows them, for testing).</summary>
    static class Sources
    {
        public static readonly SourceDef RemotePlay = new SourceDef { Kind = "chiaki", Name = "PS5 Remote Play" };
        static List<SourceDef> all;
        static readonly object gate = new object();

        /// <summary>Asks the pipeline once (it answers in well under a second); just Remote Play if it can't.</summary>
        public static List<SourceDef> All(Paths p)
        {
            lock (gate)
            {
                if (all != null) return all;
                var list = new List<SourceDef>();
                if (p.RuntimeReady)
                {
                    try
                    {
                        var lines = new List<string>();
                        int rc = Proc.Run(Proc.Python(p.Python, "-m framegen.sources --json", p.App), l => { lock (lines) lines.Add(l); }, null);
                        string json = lines.LastOrDefault(l => l.StartsWith("{"));
                        if (rc == 0 && json != null) list = Parse(json);
                        else Program.Trace("source list: " + (lines.LastOrDefault(l => l.Trim().Length > 0) ?? "exit code " + rc));
                    }
                    catch (Exception e) { Program.Trace("source list: " + e.Message); }
                }
                if (!list.Any(s => s.RemotePlay)) list.Insert(0, RemotePlay);
                return all = list;
            }
        }

        public static List<SourceDef> Parse(string json)
        {
            var root = (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
            var list = new List<SourceDef>();
            foreach (Dictionary<string, object> d in (object[])root["sources"])
                list.Add(new SourceDef
                {
                    Kind = (string)d["kind"], Name = (string)d["name"], Guide = d.ContainsKey("guide") ? d["guide"] as string : null,
                    Hidden = d.ContainsKey("hidden") && (bool)d["hidden"], TestMode = !d.ContainsKey("test_mode") || (bool)d["test_mode"],
                    Experimental = d.ContainsKey("experimental") && (bool)d["experimental"],
                });
            return list;
        }

        public static List<SourceDef> Offered(Paths p) { return All(p).Where(s => !s.Hidden).ToList(); }

        /// <summary>The source to play from: the one picked on the Play page if it's still offered, else Remote Play.</summary>
        public static SourceDef Chosen(Paths p, HashSet<string> prefs)
        {
            var offered = Offered(p);
            return offered.FirstOrDefault(s => prefs.Contains("source:" + s.Kind)) ?? offered.FirstOrDefault(s => s.RemotePlay) ?? RemotePlay;
        }

        public static void Choose(HashSet<string> prefs, string kind)
        {
            prefs.RemoveWhere(f => f.StartsWith("source:"));
            if (kind != "chiaki") prefs.Add("source:" + kind);  // Remote Play is the default, so it needs no flag
        }

        /// <summary>What run.py needs to play from a source. video, audio: the capture card and its sound as picked on
        /// the Play page (CaptureDevices; null: the first card, and the sound device named like it; audio "off": none).</summary>
        public static string RunArgs(string kind, string video = null, string audio = null)
        {
            if (kind == "capture")
                return "--source capture --capture " + Quote(Environment.GetEnvironmentVariable("KEEL_CAPTURE") ?? video ?? "0")
                       + (audio != null ? " --capture-audio " + Quote(audio) : "");
            if (kind == "window")  // video: the app's window, as AppWindows.Query gives it
                return "--source window --window " + Quote(Environment.GetEnvironmentVariable("KEEL_WINDOW") ?? video ?? "ps-remote-play");
            if (kind == "tap")  // KEEL_TAP: another shared-memory name (docs/frame-tap.md)
                return "--source tap" + (Environment.GetEnvironmentVariable("KEEL_TAP") != null
                                         ? " --tap " + Quote(Environment.GetEnvironmentVariable("KEEL_TAP")) : "");
            if (kind == "file")
                return "--source file " + Quote(Environment.GetEnvironmentVariable("KEEL_SOURCE_FILE") ?? "") + " --raw --realtime";
            return "--source chiaki";
        }

        static string Quote(string s) { return "\"" + s.Replace("\"", "") + "\""; }
    }

    /// <summary>The app windows that window capture could read (python -m framegen.window --list --json): the Play
    /// page's App choice when playing from another app's window. Read in the background when that source is picked, and
    /// again on Look again (windows come and go, so it isn't kept for long).</summary>
    static class AppWindows
    {
        public class Win { public long Hwnd; public string Title, Exe, App; public int W, H; public bool Known; }
        public static List<Win> List = new List<Win>();
        /// <summary>Streaming apps that are running but weren't listed, and why ("Xbox app" -> "minimised"), from the
        /// pipeline's left_out: only apps it knows are named, never other windows.</summary>
        public static Dictionary<string, string> Missed = new Dictionary<string, string>();
        public static string Error;  // why the last look failed, for diagnostics (null: it worked)
        public static bool Loaded;
        static readonly object gate = new object();

        public static void Load(Paths p)
        {
            lock (gate)
            {
                var lines = new List<string>();
                try
                {
                    int rc = Proc.Run(Proc.Python(p.Python, "-m framegen.window --list --json", p.App), l => { lock (lines) lines.Add(l); }, null, 30000);
                    string json = lines.LastOrDefault(l => l.StartsWith("{"));
                    var list = new List<Win>();
                    var missed = new Dictionary<string, string>();
                    if (rc == 0 && json != null)
                    {
                        var root = (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
                        foreach (Dictionary<string, object> d in (object[])root["windows"])
                            list.Add(new Win
                            {
                                Hwnd = Convert.ToInt64(d["hwnd"]), Title = (string)d["title"], Exe = (string)d["exe"], App = (string)d["app"],
                                W = Convert.ToInt32(d["w"]), H = Convert.ToInt32(d["h"]), Known = d.ContainsKey("known") && (bool)d["known"],
                            });
                        if (root.ContainsKey("left_out") && root["left_out"] is object[] left)
                            foreach (Dictionary<string, object> d in left)
                                missed[(string)d["app"]] = string.Join(", ", ((object[])d["why"]).Cast<string>());
                        Error = null;
                    }
                    else
                    {
                        Error = lines.LastOrDefault(l => l.Trim().Length > 0) ?? "exit code " + rc;
                        Program.Trace("app windows: " + Error);
                    }
                    List = list;
                    Missed = missed;
                }
                catch (Exception e) { Error = e.Message; Program.Trace("app windows: " + e.Message); }
                Loaded = true;
            }
        }

        /// <summary>The window to capture: the one picked last time (by its program) if it's open, else the first app it
        /// knows (PS Remote Play, the Xbox app, Moonlight, then browsers), else the first.</summary>
        public static Win Chosen(HashSet<string> prefs)
        {
            string hwnd = CaptureDevices.Pref(prefs, "window-hwnd:"), exe = CaptureDevices.Pref(prefs, "window-app:");
            return List.FirstOrDefault(w => w.Hwnd.ToString() == hwnd) ?? List.FirstOrDefault(w => w.Exe == exe)
                   ?? List.FirstOrDefault(w => w.Known) ?? List.FirstOrDefault();
        }

        /// <summary>For diagnostics: the streaming apps and browsers found, with sizes, and only a count of other windows
        /// (their titles and programs stay private).</summary>
        public static string Summary()
        {
            var known = List.Where(w => w.Known).Select(w => w.App + (w.W > 0 ? $" {w.W}\u00d7{w.H}" : " (minimised)")).ToList();
            int others = List.Count(w => !w.Known);
            var sb = new StringBuilder();
            sb.AppendLine("App windows: " + (known.Count == 0 ? "no streaming app or browser" : string.Join("; ", known))
                          + (others > 0 ? $" (and {others} other windows)" : ""));
            foreach (var m in Missed) sb.AppendLine($"App window left out: {m.Key} ({m.Value})");
            if (Error != null) sb.AppendLine("App window list failed: " + Error);
            return sb.ToString();
        }

        /// <summary>A line on a streaming app that's running but has no window to pick, or null.</summary>
        public static string MissedHint()
        {
            if (Missed.Count == 0) return null;
            var m = Missed.First();
            string fix = m.Value.Contains("full screen") ? "leave full screen (its full screen button, or Shift+Win+Enter)"
                       : m.Value.Contains("minimised") ? "restore its window"
                       : m.Value.Contains("another desktop") ? "bring its window to this desktop"
                       : m.Value.Contains("too small") ? "make its window bigger"
                       : "open its window (not just the tray icon) and start streaming";
            return $"The {m.Key} is running, but its window can't be used ({m.Value}). {Char.ToUpper(fix[0]) + fix.Substring(1)}, then press Look again.";
        }

        /// <summary>What run.py's --window gets: the window itself, or failing that (reopened since) its program.</summary>
        public static string Query(Win w) { return w == null ? null : "hwnd:" + w.Hwnd + "|exe:" + w.Exe; }

        public static string Label(Win w)
        {
            string title = w.Title.Length > 48 ? w.Title.Substring(0, 47) + "\u2026" : w.Title;
            return w.App + (title.Length > 0 && title != w.App ? "  \u00b7  " + title : "") + (w.W > 0 ? $"  ({w.W}\u00d7{w.H})" : "  (minimised)");
        }
    }

    /// <summary>The capture devices DirectShow lists (python -m framegen.capture --list --json): the Play page's Card and
    /// Sound choices when playing from a capture card. Read once, in the background (a second or so), and again on Refresh.</summary>
    static class CaptureDevices
    {
        public static List<string> Video = new List<string>(), Audio = new List<string>();
        public static string Error;  // why the last look failed, or what went wrong after the list came (null: all well)
        public static bool Failed;  // no list came at all (Error says why)
        public static bool Loaded;
        static readonly object gate = new object();
        // Names that are likely a capture card rather than a webcam (also the PC check's, in Checks.cs)
        public const string CardPattern = "capture|cam link|elgato|avermedia|hdmi|usb video|ms2130|live gamer|magewell|razer ripsaw";

        public static void Load(Paths p)
        {
            lock (gate)
            {
                var lines = new List<string>();
                try
                {
                    int rc = Proc.Run(Proc.Python(p.Python, "-m framegen.capture --list --json", p.App), l => { lock (lines) lines.Add(l); }, null, 30000);
                    string json = lines.LastOrDefault(l => l.StartsWith("{"));
                    if (json != null)  // the list came out, even if something went wrong after (the exit code says)
                    {
                        var root = (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
                        Video = Names(root, "video"); Audio = Names(root, "audio");
                        Error = root.ContainsKey("error") ? root["error"] as string : rc != 0 ? "after listing, " + Proc.Describe(rc) : null;
                        Failed = Video.Count == 0 && Audio.Count == 0 && root.ContainsKey("error");
                    }
                    else
                    {
                        string last = lines.LastOrDefault(l => l.Trim().Length > 0 && !l.StartsWith("Traceback"));
                        Error = rc == Proc.TimedOut ? "Windows' capture devices didn't answer in 30 s (a capture driver may be stuck: unplug the card and plug it back in)"
                              : Proc.Describe(rc) + (last != null ? ": " + last.Trim() : "");
                        Video = new List<string>(); Audio = new List<string>();
                        Failed = true;
                    }
                    if (Error != null) Program.Trace("capture devices: " + Error);
                }
                catch (Exception e) { Error = e.Message; Failed = true; Program.Trace("capture devices: " + e.Message); }
                Loaded = true;
            }
        }

        static List<string> Names(Dictionary<string, object> root, string key)
        {
            var list = new List<string>();
            if (root.ContainsKey(key) && root[key] is object[] items)
                foreach (Dictionary<string, object> d in items) list.Add((string)d["name"]);
            return list;
        }

        /// <summary>For diagnostics: every video device, the sound devices named like a card (other sound devices are only
        /// counted: a headset can carry its owner's name), and why the last look failed.</summary>
        public static string Summary()
        {
            var sb = new StringBuilder();
            sb.AppendLine("Capture devices (DirectShow): " + (Video.Count == 0 ? "no video device" : string.Join("; ", Video.Select(Diagnostics.NoOwner))));
            var cardAudio = Audio.Where(n => Regex.IsMatch(n, CardPattern, RegexOptions.IgnoreCase)).ToList();
            sb.AppendLine("Capture sound: " + (cardAudio.Count == 0 ? "none named like a card" : string.Join("; ", cardAudio.Select(Diagnostics.NoOwner)))
                          + (Audio.Count > cardAudio.Count ? $" (and {Audio.Count - cardAudio.Count} other sound devices)" : ""));
            if (Error != null) sb.AppendLine((Failed ? "Capture device list failed: " : "Capture device list: ") + Error);
            return sb.ToString();
        }

        /// <summary>The card to use: the one picked (if it's still there), else the first that looks like a card, else the first.</summary>
        public static string ChosenVideo(HashSet<string> prefs)
        {
            string picked = Pref(prefs, "capture-video:");
            if (picked != null && Video.Contains(picked)) return picked;
            return Video.FirstOrDefault(n => System.Text.RegularExpressions.Regex.IsMatch(n, CardPattern, System.Text.RegularExpressions.RegexOptions.IgnoreCase))
                   ?? Video.FirstOrDefault();
        }

        /// <summary>The sound: null for "auto" (the device named like the card), "off", or a device's name.</summary>
        public static string ChosenAudio(HashSet<string> prefs)
        {
            string picked = Pref(prefs, "capture-audio:");
            return picked == "off" || (picked != null && Audio.Contains(picked)) ? picked : null;
        }

        public static string Pref(HashSet<string> prefs, string prefix)
        {
            var f = prefs.FirstOrDefault(x => x.StartsWith(prefix));
            return f == null ? null : f.Substring(prefix.Length);
        }

        public static void Choose(HashSet<string> prefs, string prefix, string value)
        {
            prefs.RemoveWhere(f => f.StartsWith(prefix));
            if (value != null) prefs.Add(prefix + value);
        }
    }
}
