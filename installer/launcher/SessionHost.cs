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

    /// <summary>One play session: the pipeline (run.py --source chiaki --display), then chiaki-ng once the pipeline waits.
    /// With --control the pipeline also takes settings commands on its stdin (Send) and reports its settings as
    /// "[settings-state] {json}" lines (OnSettings) once the video is up: the launcher's Settings page while playing.
    /// The commands and the state are described in framegen/remote.py.</summary>
    class Session
    {
        public const string StatePrefix = "[settings-state] ";
        readonly Paths p;
        Process py;
        StreamWriter log, commands;
        readonly object gate = new object();
        volatile bool stopping;
        volatile bool live;  // the video window reads commands (it sent its settings): Stop asks it to quit, as Esc does
        bool streamEnded;
        // TestMode: "Test with a 60 fps game" (run.py --test-mode). Every session passes --results, since the
        // video window's menu can also turn the test on; run.py only writes the file once something was tested.
        public bool TestMode;
        public string Source = "chiaki";  // a SourceDef.Kind
        public string CaptureVideo, CaptureAudio;  // a capture card's devices (CaptureDevices), for Source "capture"
        public string WindowApp;  // the app window (AppWindows.Query), for Source "window"
        public string WindowKeysArg;  // its shortcuts, if changed (WindowKeys.Arg), for Source "window"
        public string LogPath, ResultsPath, LastLine = "";
        public Action<string> OnLine = s => { };
        public Action<PlayState, string> OnState = (s, d) => { };
        public Action<string> OnNote = s => { };
        public Action<string> OnSettings = json => { };  // a state line's JSON, on the output thread
        public Action<string> OnWindowKeys = json => { };  // the shortcuts the video window got (App window mode)

        // Windows lets a program bring its window to the front only if the program in front allows it. When the
        // player has just pressed Play here, that's this launcher: let the video window come to the front, so the
        // keys (M, F, Esc) go to it without a click first. (chiaki-ng does the same when its stream starts.)
        [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int pid);

        void LetVideoComeToFront()
        {
            try { if (Running) AllowSetForegroundWindow(py.Id); } catch (Exception) { }
        }
        // py exists a moment before it starts (OnState(Starting) comes in between), and asking it anything then
        // throws "No process is associated with this object": running means started and not exited.
        volatile bool started;
        public bool Running
        {
            get
            {
                var p = py;
                if (p == null || !started) return false;
                try { return !p.HasExited; }
                catch (InvalidOperationException) { return false; }
            }
        }

        public Session(Paths p) { this.p = p; }

        public void Start()
        {
            Directory.CreateDirectory(p.Logs);
            string stamp = $"{DateTime.Now:yyyyMMdd-HHmmss}";
            LogPath = Path.Combine(p.Logs, $"session-{stamp}.log");
            ResultsPath = Path.Combine(p.Logs, $"results-{stamp}.txt");
            log = new StreamWriter(LogPath, false, Encoding.UTF8) { AutoFlush = true };
            if (SystemCheck.GpuName == "unknown") SystemCheck.QueryGpu();  // fills in the GPU and driver
            log.WriteLine($"[launcher] Keel {BuildInfo.Version}, {DateTime.Now:yyyy-MM-dd HH:mm:ss}, {(SystemCheck.GpuName == "unknown" && SystemCheck.OtherGpu != null ? SystemCheck.OtherGpu + " (DirectML)" : SystemCheck.GpuName + ", driver " + SystemCheck.Driver)}"
                          + (Source != "chiaki" ? ", source " + Source : "") + (TestMode ? ", test mode (60 fps game played as 30 fps)" : ""));
            string args = "keel.py " + Sources.RunArgs(Source, Source == "window" ? WindowApp : CaptureVideo, CaptureAudio)
                          + (Source == "window" && !string.IsNullOrEmpty(WindowKeysArg) ? " --window-keys \"" + WindowKeysArg.Replace("\"", "") + "\"" : "")
                          + " --display --control" + (TestMode ? " --test-mode" : "") + $" --results \"{ResultsPath}\" " + p.RunArgs;
            var psi = Proc.Python(p.Python, args.Trim(), p.App);
            psi.RedirectStandardInput = true;
            py = new Process { StartInfo = psi, EnableRaisingEvents = true };
            py.OutputDataReceived += (s, e) => { if (e.Data != null) Line(e.Data); };
            py.ErrorDataReceived += (s, e) => { if (e.Data != null) Line(e.Data); };
            py.Exited += (s, e) =>
            {
                py.WaitForExit();  // the rest of the output
                int rc = py.ExitCode;
                lock (gate) { if (log != null) { log.WriteLine($"[launcher] pipeline exited with code {rc}"); log.Dispose(); log = null; } }
                Program.Trace("pipeline exited, code " + rc);
                if (stopping) OnState(PlayState.Stopped, null);
                else if (rc == 0 && streamEnded) OnState(PlayState.Ended, null);
                else if (rc == 0) OnState(PlayState.Stopped, null);
                else OnState(PlayState.Error, LastLine);
            };
            OnState(PlayState.Starting, null);
            py.Start();
            started = true;
            LetVideoComeToFront();
            // Our own writer: UTF-8 without a BOM (Process's own uses the console's code page).
            commands = new StreamWriter(py.StandardInput.BaseStream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
            py.BeginOutputReadLine();
            py.BeginErrorReadLine();
        }

        /// <summary>One command for the running pipeline, e.g. "set hz 90" or "restart". False if it has gone.</summary>
        public bool Send(string command)
        {
            lock (gate)
            {
                // (the Looks page sends a look up to 25 times a second while a slider moves: just its size)
                if (log != null) log.WriteLine(command == "quit" ? "[launcher] Stop: asked the video window to close"
                    : "[launcher] settings: " + (command.StartsWith("look-show ") ? "look-show (" + command.Length + " characters)" : command));
                try
                {
                    if (commands == null || !Running) return false;
                    commands.WriteLine(command);
                    return true;
                }
                catch (Exception) { return false; }  // it exited meanwhile
            }
        }

        /// <summary>Stops the session. Once the video is up it's asked to close, as Esc does, so a clip being saved is
        /// finished and chiaki-ng comes back; it's ended outright if it hasn't gone 20 s later, if it isn't up yet,
        /// or on a second Stop.</summary>
        public void Stop()
        {
            bool again = stopping;
            stopping = true;
            var pr = py;
            try
            {
                if (pr == null || pr.HasExited) return;
                if (!again && live && Send("quit"))
                {
                    System.Threading.ThreadPool.QueueUserWorkItem(_ => { try { if (!pr.WaitForExit(20000)) pr.Kill(); } catch (Exception) { } });
                    return;
                }
                pr.Kill();
            }
            catch (Exception) { }
        }

        void Line(string line)
        {
            if (line.StartsWith(StatePrefix))  // not for the log; it also means the video window reads commands
            {
                live = true;
                OnSettings(line.Substring(StatePrefix.Length));
                return;
            }
            lock (gate) { if (log != null) log.WriteLine(line); }
            if (line.Trim().Length > 0) LastLine = line.Trim();
            OnLine(line);
            if (line.StartsWith("[chiaki] waiting", StringComparison.OrdinalIgnoreCase))
            {
                if (Source == "chiaki" && Chiaki.Ours(p) == null) Chiaki.Start(p, OnLine);
                OnState(PlayState.Waiting, null);
            }
            else if (line.StartsWith("[tap] waiting for an app"))  // the frame tap: the app starts when the player likes
                OnState(PlayState.Waiting, null);
            else if (line.StartsWith("[chiaki] stream:"))
            {
                LetVideoComeToFront();  // (the video window opens next)
                OnState(PlayState.Found, line.Substring("[chiaki] stream:".Length).Trim());
            }
            else if (line.StartsWith("[tap] frames:"))
                OnState(PlayState.Found, line.Substring("[tap] frames:".Length).Trim());
            else if (line.StartsWith("[trt] building"))
                OnState(PlayState.Building, null);
            else if (line.StartsWith("[run] restarting for "))  // "[run] restarting for AI engine: RIFE ..."
            {
                string why = line.Substring("[run] restarting for ".Length);
                int colon = why.IndexOf(": ");
                OnState(PlayState.Building, colon > 0 ? why.Substring(0, colon) : why);
            }
            else if (line.StartsWith("[run] playing"))  // the video is up: first, and after every in-app restart
                OnState(PlayState.Playing, null);
            else if (line.StartsWith("[chiaki] this stream was already running"))
                OnNote("This stream started before Keel. In chiaki-ng, end the stream and connect again.");
            else if (line.StartsWith("[tap] this stream was already running"))  // another app's compressed frames
                OnNote("This stream started before Keel. Restart the stream in the app.");
            else if (line.StartsWith("[chiaki] WARNING:") || line.StartsWith("[tap] WARNING:"))
                OnNote(line.Substring(line.IndexOf("WARNING:") + "WARNING:".Length).Trim());
            else if (line.StartsWith("[chiaki] stream ended") || line.StartsWith("[tap] the app stopped sending frames"))
                streamEnded = true;
            else if (line.StartsWith("[window-keys] "))
                OnWindowKeys(line.Substring("[window-keys] ".Length));
        }
    }

    /// <summary>Before &amp; after videos being made (framegen/replay.py, SAVING_DIR). Each is a process of its own that
    /// carries on after the video window and this launcher close, and keeps %APPDATA%\framegen\saving\&lt;process id&gt;.txt
    /// while it works: the video's path, then "failed: why" if it failed. Updating, repairing or uninstalling replaces
    /// the Python it runs on, so those stop it.</summary>
    static class Saving
    {
        public class Video { public int Pid; public string Out, Failed; public bool Running; }

        static string Dir { get { return Path.Combine(Paths.SettingsDir, "saving"); } }

        /// <summary>Every one there: still being made, failed, or left by one that was stopped.</summary>
        public static List<Video> Read()
        {
            var all = new List<Video>();
            try
            {
                if (!Directory.Exists(Dir)) return all;
                foreach (var f in Directory.GetFiles(Dir, "*.txt"))
                {
                    int pid;
                    string[] l;
                    if (!int.TryParse(Path.GetFileNameWithoutExtension(f), out pid)) continue;
                    try { l = File.ReadAllLines(f); } catch (Exception) { continue; }  // being written
                    if (l.Length == 0 || l[0].Trim().Length == 0) continue;
                    var v = new Video { Pid = pid, Out = l[0].Trim() };
                    v.Failed = l.Length > 1 && l[1].StartsWith("failed: ") ? l[1].Substring(8).Trim() : null;
                    v.Running = v.Failed == null && Alive(pid, File.GetLastWriteTime(f));
                    all.Add(v);
                }
            }
            catch (Exception) { }
            return all;
        }

        /// <summary>That process is still the one that wrote the file at `since` (not a later one given its id).</summary>
        static bool Alive(int pid, DateTime since)
        {
            try
            {
                using (var pr = Process.GetProcessById(pid))
                    return !pr.HasExited && pr.ProcessName.StartsWith("python", StringComparison.OrdinalIgnoreCase)
                           && pr.StartTime <= since.AddSeconds(2);
            }
            catch (Exception) { return false; }
        }

        /// <summary>Done with it: its file goes, and an unfinished video's ".part" with it.</summary>
        public static void Forget(Video v)
        {
            try { File.Delete(Path.Combine(Dir, v.Pid + ".txt")); } catch (Exception) { }
            if (!v.Running) { try { File.Delete(v.Out + ".part"); } catch (Exception) { } }
        }

        public static void Stop(IEnumerable<Video> videos)
        {
            foreach (var v in videos.Where(x => x.Running).ToList())
            {
                try { using (var pr = Process.GetProcessById(v.Pid)) { pr.Kill(); pr.WaitForExit(5000); } } catch (Exception) { }
                v.Running = false;
                Forget(v);
            }
        }
    }

    static class Diagnostics
    {
        static void AppendLog(StringBuilder sb, string logFile, int max)
        {
            string[] lines;
            using (var fs = new FileStream(logFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (var sr = new StreamReader(fs))
                lines = sr.ReadToEnd().Split('\n');
            int n = Math.Min(max, lines.Length);
            sb.AppendLine($"Log {Path.GetFileName(logFile)}, last {n} lines:");
            foreach (var l in lines.Skip(lines.Length - n))
                sb.AppendLine(l.TrimEnd('\r'));
        }

        public static string Collect(Paths p, string lastLog)
        {
            var sb = new StringBuilder();
            sb.AppendLine($"Keel {BuildInfo.Version} diagnostics, {DateTime.Now:yyyy-MM-dd HH:mm}");
            const string nt = @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion";
            string product = Registry.GetValue(nt, "ProductName", "") as string, build = Registry.GetValue(nt, "CurrentBuild", "") as string;
            string release = Registry.GetValue(nt, "DisplayVersion", "") as string;
            sb.AppendLine($"Windows: {WindowsName(product, build)}{(string.IsNullOrEmpty(release) ? "" : " " + release)} build {build}");
            var prefs = Prefs.Load();
            string kind = prefs.Where(f => f.StartsWith("source:")).Select(f => f.Substring(7)).FirstOrDefault() ?? "chiaki";
            sb.AppendLine("Source: " + kind);
            foreach (var c in SystemCheck.RunAll(p.Install, false, kind))
                sb.AppendLine($"Check [{c.Level}] {c.Title}: {c.Value}. {c.Detail}");
            if (kind == "capture" || CaptureDevices.Loaded)
            {
                if (!CaptureDevices.Loaded && p.RuntimeReady) CaptureDevices.Load(p);
                sb.Append(CaptureDevices.Summary());
                sb.Append(SystemCheck.CaptureHardware());
            }
            if (kind == "window" || AppWindows.Loaded)
            {
                if (!AppWindows.Loaded && p.RuntimeReady) AppWindows.Load(p);
                sb.Append(AppWindows.Summary());
            }
            // (The user's name is taken out below: a path like C:\Users\<name with Chinese letters> would look fine.)
            sb.AppendLine("Install: " + p.Install + (p.Dev ? " (dev)" : "")
                          + (p.Install.Any(c => c > 127) ? " (the path has letters outside English A-Z)" : ""));
            sb.AppendLine("Runtime: " + (File.Exists(p.Ready) ? File.ReadAllText(p.Ready).Trim() : "not ready"));
            sb.AppendLine("chiaki-ng: " + (Chiaki.Ours(p) != null ? "running (started by Keel)" : "not started by Keel")
                          + $", {Chiaki.Others(p).Length} other chiaki-ng running");
            string settings = Paths.SettingsFile;
            sb.AppendLine("Settings: " + (File.Exists(settings) ? File.ReadAllText(settings).Trim() : "none saved"));
            string logFile = lastLog;
            if (logFile == null && Directory.Exists(p.Logs))
                logFile = new DirectoryInfo(p.Logs).GetFiles("*.log").OrderByDescending(f => f.LastWriteTime).Select(f => f.FullName).FirstOrDefault();
            if (logFile != null && File.Exists(logFile))
                AppendLog(sb, logFile, 200);
            // Copied after setup (its log is the launcher's last): the newest game session's log too, which is what a
            // report about playing needs, or a note that there's none yet
            if (logFile == null || Path.GetFileName(logFile).StartsWith("setup-", StringComparison.OrdinalIgnoreCase))
            {
                string session = Directory.Exists(p.Logs)
                    ? new DirectoryInfo(p.Logs).GetFiles("session-*.log").OrderByDescending(f => f.LastWriteTime).Select(f => f.FullName).FirstOrDefault()
                    : null;
                if (session != null) AppendLog(sb, session, 200);
                else sb.AppendLine("No game session log yet: after playing, press Copy diagnostics again.");
            }
            string results = LatestResults(p);
            if (results != null)
            {
                string text = ReadShared(results).TrimEnd();
                sb.AppendLine($"Test results {Path.GetFileName(results)}:");
                // NVIDIA's licence forbids publishing VFG's results, and bug reports get pasted in public places.
                sb.AppendLine(text.Contains(VfgPrivate) ? "(left out: they include NVIDIA VFG, whose licence doesn't allow sharing its results)" : text);
            }
            if (File.Exists(Program.CrashLog))
            {
                var crash = File.ReadAllLines(Program.CrashLog);
                sb.AppendLine("Launcher errors (last 40 lines):");
                foreach (var l in crash.Skip(Math.Max(0, crash.Length - 40))) sb.AppendLine(l);
            }
            return Anonymize(sb.ToString(), Environment.GetFolderPath(Environment.SpecialFolder.UserProfile));
        }

        /// <summary>A device name without its owner's: "Alex's iPhone Camera" becomes "(name)'s iPhone Camera".</summary>
        public static string NoOwner(string name)
        {
            return Regex.Replace(name ?? "", @"[^\s()""]+['’]s\b", "(name)'s");
        }

        /// <summary>Windows 11 still calls itself "Windows 10 Pro" (and so on) in the registry's ProductName, so the build
        /// number decides: 22000 and up is Windows 11.</summary>
        public static string WindowsName(string product, string build)
        {
            product = string.IsNullOrEmpty(product) ? "Windows" : product;
            return int.TryParse(build, out int b) && b >= 22000 ? Regex.Replace(product, @"\bWindows 10\b", "Windows 11") : product;
        }

        /// <summary>Bug reports get pasted in public places, so the Windows user name is left out: the user's folder
        /// becomes %USERPROFILE% however a log spells it (pip writes it in lower case, JSON doubles the backslashes,
        /// some tools use forward slashes or the short 8.3 name), and so does any other C:\Users\NAME folder.</summary>
        public static string Anonymize(string text, string home)
        {
            const RegexOptions ic = RegexOptions.IgnoreCase | RegexOptions.CultureInvariant;
            const string sep = @"(?:\\\\|\\|/)";  // \\ (JSON), \ or /
            if (!string.IsNullOrEmpty(home))
            {
                var parts = home.TrimEnd('\\', '/').Split(new[] { '\\', '/' }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length > 0)  // the whole folder name, so C:\Users\Al leaves C:\Users\Alice alone
                    text = Regex.Replace(text, string.Join(sep, parts.Select(Regex.Escape)) + @"(?=$|[\\/""'\s:;,)\]])", "%USERPROFILE%", ic | RegexOptions.Multiline);
            }
            // Any other user folder: X:\Users\NAME (not Public or Default), whatever the case and separators
            return Regex.Replace(text, @"\b[A-Za-z]:" + sep + "Users" + sep + @"(?!(?:Public|Default|All Users)(?:\\|/|""|$))[^\\/""\r\n]+", "%USERPROFILE%", ic | RegexOptions.Multiline);
        }

        /// <summary>run.py's PRIVATE_RESULTS line, in a test report that includes NVIDIA VFG.</summary>
        const string VfgPrivate = "These results include NVIDIA VFG.";

        /// <summary>The newest test-mode report (logs\results-*.txt, written by run.py --results), or null.</summary>
        public static string LatestResults(Paths p)
        {
            if (!Directory.Exists(p.Logs)) return null;
            return new DirectoryInfo(p.Logs).GetFiles("results-*.txt").OrderByDescending(f => f.LastWriteTime)
                .Select(f => f.FullName).FirstOrDefault();
        }

        /// <summary>A text file's contents, even while run.py may be replacing it.</summary>
        public static string ReadShared(string path)
        {
            try
            {
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var sr = new StreamReader(fs, Encoding.UTF8))
                    return sr.ReadToEnd();
            }
            catch (IOException) { return ""; }
            catch (UnauthorizedAccessException) { return ""; }
        }
    }

    /// <summary>What the launcher remembers between runs (the setup guides' ticks, the source picked on the Play page),
    /// next to the pipeline's settings.</summary>
    static class Prefs
    {
        static string File_
        {
            get { return Environment.GetEnvironmentVariable("KEEL_PREFS") ?? Path.Combine(Paths.SettingsDir, "keel-launcher.txt"); }
        }

        public static HashSet<string> Load()
        {
            try { return new HashSet<string>(File.ReadAllLines(File_).Select(l => l.Trim()).Where(l => l.Length > 0)); }
            catch (Exception) { return new HashSet<string>(); }
        }

        public static void Save(HashSet<string> flags)
        {
            try { Directory.CreateDirectory(Path.GetDirectoryName(File_)); File.WriteAllLines(File_, flags.OrderBy(f => f)); }
            catch (Exception) { }
        }

        /// <summary>A value from the pipeline's settings.json (a flat JSON object), or null.</summary>
        public static string Setting(string key)
        {
            try
            {
                var m = Regex.Match(File.ReadAllText(Paths.SettingsFile), "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"?([^\",}\\s]+)");
                return m.Success ? m.Groups[1].Value : null;
            }
            catch (Exception) { return null; }
        }
    }
}
