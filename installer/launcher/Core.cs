// Keel launcher and installer: everything except the window (see Ui.cs, Ui.xaml) and the PC checks (Checks.cs).
//
// Keel-Setup.exe is this program with the app files embedded as "payload.zip". It checks the PC, unpacks the
// app into a per-user folder (no admin rights), downloads Python, PyTorch and TensorRT from their official sources,
// prepares the GPU once and adds shortcuts. Keel.exe (the same code without a payload) is the launcher: it
// starts the pipeline before chiaki-ng so the stream's codec header is never missed, shows status, guides the PS5
// setup, and copies diagnostics for bug reports. WPF on .NET Framework 4.8, which Windows 10 and 11 include.
//
// Command line:  --unattended --dir <folder> [--no-shortcuts] [--skip-prepare]   install without UI (exit code 0 = ok)
//                --update --dir <folder> [--wait <pid>]  (setup) update that install, after process <pid> (the
//                                                        launcher that started it, Updater.cs) has closed; then open it
//                --uninstall [--yes]                                               remove an installed copy
//                --diagnostics <file>                                              write what "Copy diagnostics" copies
//                --page <check|play|guide|settings|looks|help>                           open the launcher on that page
// Dev/testing:   KEEL_PYTHON, KEEL_APP, KEEL_CHIAKI, KEEL_CHIAKI_ARGS environment variables run
//                the launcher against a dev checkout (e.g. the repo's venv with scripts\frametap_fake.py as "chiaki");
//                KEEL_RUN_ARGS adds arguments to run.py; KEEL_TRACE=<file> traces; KEEL_PREFS=<file>
//                keeps the launcher's own memory (the PS5 guide's ticks) somewhere else; KEEL_SHOTS=<folder>
//                saves a PNG of the window on every page or state change; KEEL_RECORD=<folder> records it as
//                video (Ui.StartRecorder); KEEL_SETTINGS=<file> reads another settings.json.
//                KEEL_SOURCES=all offers the sources that aren't ready yet (video files) on the Play page;
//                KEEL_WINDOW=<app> overrides the app window picked there (framegen/window.py's --window),
//                KEEL_CAPTURE=<name|index|video file> overrides the capture card picked there, and
//                KEEL_SOURCE_FILE=<video file> feeds the video file source.
// Built by installer\build.py, which also generates BuildInfo.cs (version, Python download and its SHA-256).

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
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            // Tooltips as before .NET Framework 4.8's WCAG 2.1 ones, which stay open while the mouse is between a
            // tooltip's owner and the tooltip (so it can get onto it). Ours open below the mouse and run to the right,
            // over the next buttons in a row (the Settings page's choices: AMD FSR 1, NVIDIA RTX VSR, Bilinear), so
            // moving along a row kept showing the first button's tooltip. First thing: WPF keeps the value it reads first.
            AppContext.SetSwitch("Switch.System.Windows.Controls.ToolTip.OptOutOfWCAG21ToolTipBehavior", true);
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            var opts = Options.Parse(args);
            if (args.Length == 2 && args[0] == "--check-signature")  // testing the Visual C++ download check
                return SystemCheck.SignedByMicrosoft(Path.GetFullPath(args[1])) ? 0 : 1;
            if (opts.Uninstall)
                return Uninstaller.Run(opts.Yes);
            if (opts.Unattended)
                return UnattendedInstall(opts);
            if (opts.Diagnostics != null)
            {
                File.WriteAllText(opts.Diagnostics, Diagnostics.Collect(Paths.Detect(), null), Encoding.UTF8);
                return 0;
            }
            var app = new System.Windows.Application { ShutdownMode = System.Windows.ShutdownMode.OnMainWindowClose };
            app.DispatcherUnhandledException += (s, e) => { Crash(e.Exception); e.Handled = true; };
            AppDomain.CurrentDomain.UnhandledException += (s, e) => Crash(e.ExceptionObject as Exception);
            var ui = new Ui(opts);
            return app.Run(ui.Window);
        }

        public static string CrashLog { get { return Path.Combine(Path.GetTempPath(), "Keel-launcher-errors.log"); } }

        /// <summary>A launcher bug: keep the details in a log (diagnostics include it) and say so.</summary>
        static void Crash(Exception e)
        {
            try { File.AppendAllText(CrashLog, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} Keel {BuildInfo.Version}\r\n{e}\r\n\r\n"); }
            catch (Exception) { }
            System.Windows.MessageBox.Show("Something went wrong in the Keel window:\n\n" + (e != null ? e.Message : "unknown error") +
                                           "\n\nThe details are in " + CrashLog + ".", "Keel",
                                           System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
        }

        static readonly string TraceFile = Environment.GetEnvironmentVariable("KEEL_TRACE");
        /// <summary>Developer tracing (KEEL_TRACE=file): timestamped, with the thread id.</summary>
        public static void Trace(string msg)
        {
            if (string.IsNullOrEmpty(TraceFile)) return;
            lock (TraceFile)
                File.AppendAllText(TraceFile, $"{DateTime.Now:HH:mm:ss.fff} t{System.Threading.Thread.CurrentThread.ManagedThreadId} {msg}" + Environment.NewLine);
        }

        /// <summary>Lets process `pid` (the launcher an update came from) close first, for up to `seconds`.</summary>
        public static void WaitFor(int pid, int seconds)
        {
            if (pid <= 0) return;
            try { using (var pr = Process.GetProcessById(pid)) pr.WaitForExit(seconds * 1000); }
            catch (Exception) { }  // already gone
        }

        static int UnattendedInstall(Options opts)
        {
            WaitFor(opts.Wait, 30);
            Paths paths = opts.Dir != null ? Paths.ForInstall(opts.Dir) : Paths.Detect();
            Directory.CreateDirectory(paths.Install);
            using (var log = new StreamWriter(Path.Combine(paths.Install, "install.log"), false, Encoding.UTF8) { AutoFlush = true })
            {
                bool payload = Payload.Present;
                var inst = new Installer(paths, payload, payload && !opts.NoShortcuts, !opts.SkipPrepare, false, false);
                var gate = new object();
                inst.Log = line => { lock (gate) log.WriteLine(line); };
                double last = -1;
                inst.Progress = (f, s) =>
                {
                    lock (gate)
                        if (f - last >= 0.01 || f >= 1) { last = f; log.WriteLine($"[progress] {f * 100:0}% {s}"); }
                };
                foreach (var c in SystemCheck.RunAll(paths.Install, payload))
                    log.WriteLine($"[check] {c.Level}: {c.Title}: {c.Value}. {c.Detail}");
                try { inst.Run(); log.WriteLine("[done] ok"); return 0; }
                catch (Exception e) { log.WriteLine("[done] FAILED: " + Installer.Message(e)); return 1; }
            }
        }
    }

    class Options
    {
        public bool Uninstall, Unattended, NoShortcuts, SkipPrepare, Yes, Update;
        public string Dir, Diagnostics, Page;
        public int Wait;  // --wait: a process to let close first (the launcher an update came from)

        public static Options Parse(string[] args)
        {
            var o = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i].ToLowerInvariant())
                {
                    case "--uninstall": o.Uninstall = true; break;
                    case "--unattended": o.Unattended = true; break;
                    case "--no-shortcuts": o.NoShortcuts = true; break;
                    case "--skip-prepare": o.SkipPrepare = true; break;
                    case "--yes": o.Yes = true; break;
                    case "--update": o.Update = true; break;
                    case "--wait": if (i + 1 < args.Length) int.TryParse(args[++i], out o.Wait); break;
                    case "--dir": if (i + 1 < args.Length) o.Dir = Path.GetFullPath(args[++i]); break;
                    case "--diagnostics": if (i + 1 < args.Length) o.Diagnostics = Path.GetFullPath(args[++i]); break;
                    case "--page": if (i + 1 < args.Length) o.Page = args[++i].ToLowerInvariant(); break;
                }
            }
            return o;
        }
    }

    /// <summary>Where everything lives. An install holds app\ (run.py, framegen), runtime\python\, chiaki\ and logs\.</summary>
    class Paths
    {
        public string Install, App, Runtime, Python, Chiaki, ChiakiArgs = "", RunArgs = "", Logs, Ready;
        public string Vfx;  // NVIDIA VFG, if the user sets it up (Vfg.cs); every Python process gets it as FRAMEGEN_VFX_PATH
        public bool Dev;

        public static Paths ForInstall(string dir)
        {
            var p = new Paths { Install = dir };
            p.App = Path.Combine(dir, "app");
            p.Runtime = Path.Combine(dir, "runtime");
            p.Python = Path.Combine(p.Runtime, "python", "python.exe");
            p.Chiaki = ChiakiExe(dir);
            p.Logs = Path.Combine(dir, "logs");
            p.Ready = Path.Combine(p.Runtime, "ready.txt");
            p.Vfx = Path.Combine(p.Runtime, "vfx");
            return p;
        }

        public static Paths Detect()
        {
            string py = Environment.GetEnvironmentVariable("KEEL_PYTHON");
            if (string.IsNullOrEmpty(py))
                return ForInstall(ExeDir);
            var p = new Paths { Dev = true, Python = py };
            p.App = Environment.GetEnvironmentVariable("KEEL_APP") ?? ExeDir;
            p.Install = p.App;
            p.Runtime = Path.Combine(Path.GetTempPath(), "Keel-dev");
            p.Chiaki = Environment.GetEnvironmentVariable("KEEL_CHIAKI") ?? "";
            p.ChiakiArgs = Environment.GetEnvironmentVariable("KEEL_CHIAKI_ARGS") ?? "";
            p.RunArgs = Environment.GetEnvironmentVariable("KEEL_RUN_ARGS") ?? "";
            p.Logs = Path.Combine(p.Runtime, "logs");
            p.Ready = Path.Combine(p.Runtime, "ready.txt");
            p.Vfx = Environment.GetEnvironmentVariable("FRAMEGEN_VFX_PATH") ?? Path.Combine(p.Runtime, "vfx");
            return p;
        }

        public static string ChiakiExe(string dir)
        {
            string own = Path.Combine(dir, "chiaki", "chiaki.exe");
            if (File.Exists(own)) return own;
            string env = Environment.GetEnvironmentVariable("KEEL_CHIAKI");
            if (!string.IsNullOrEmpty(env))
            {
                env = env.Trim().Trim('"');
                if (File.Exists(env)) return env;
            }
            string smoov = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs", "SmoovPlay", "chiaki", "chiaki.exe");
            if (File.Exists(smoov)) return smoov;
            return own;
        }

        /// <summary>Copy the frame-tap chiaki-ng out of an existing SmoovPlay install, so Keel starts it itself.</summary>
        public static void AdoptChiaki(Paths p, Action<string> log)
        {
            string own = Path.Combine(p.Install, "chiaki", "chiaki.exe");
            if (File.Exists(own)) { p.Chiaki = own; return; }
            string src = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs", "SmoovPlay", "chiaki");
            if (!File.Exists(Path.Combine(src, "chiaki.exe")))
            {
                p.Chiaki = ChiakiExe(p.Install);
                return;
            }
            string dest = Path.Combine(p.Install, "chiaki");
            CopyTree(src, dest);
            p.Chiaki = own;
            log("chiaki-ng copied from the existing install to " + own);
        }

        static void CopyTree(string src, string dest)
        {
            Directory.CreateDirectory(dest);
            foreach (var file in Directory.GetFiles(src))
                File.Copy(file, Path.Combine(dest, Path.GetFileName(file)), true);
            foreach (var sub in Directory.GetDirectories(src))
                CopyTree(sub, Path.Combine(dest, Path.GetFileName(sub)));
        }

        public static string ExeDir { get { return Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location); } }
        public static string DefaultInstall
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Keel"); }
        }
        /// <summary>Where an earlier install is, from its Apps &amp; features entry (null if none).</summary>
        public static string ExistingInstall
        {
            get
            {
                try
                {
                    using (var k = Registry.CurrentUser.OpenSubKey(Uninstaller.Key))
                    {
                        var dir = k != null ? k.GetValue("InstallLocation") as string : null;
                        return dir != null && ForInstall(dir).IsInstall ? dir : null;
                    }
                }
                catch (Exception) { return null; }
            }
        }
        public static string SettingsDir
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "framegen"); }
        }
        /// <summary>The pipeline's settings (KEEL_SETTINGS overrides it, for tests and recordings).</summary>
        public static string SettingsFile
        {
            get { return Environment.GetEnvironmentVariable("KEEL_SETTINGS") ?? Path.Combine(SettingsDir, "settings.json"); }
        }
        public bool RuntimeReady { get { return Dev ? File.Exists(Python) : File.Exists(Ready) && File.Exists(Python); } }
        public bool IsInstall { get { return File.Exists(Path.Combine(App, "run.py")); } }
    }
}
