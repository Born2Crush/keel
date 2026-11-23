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

    static class Payload
    {
        public static bool Present
        {
            get { return Assembly.GetExecutingAssembly().GetManifestResourceNames().Contains("payload.zip"); }
        }
        public static Stream Open() { return Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"); }
    }

    // ------------------------------------------------------------------------------------------------ install
    /// <summary>The install steps. Each can run again: Retry or Repair picks up where a run stopped.</summary>
    class Installer
    {
        public static readonly string[] StepNames =
            { "Unpack Keel", "Download Python", "Download PyTorch and TensorRT", "Optimise for your graphics card", "Finish up" };

        readonly Paths p;
        readonly bool extract, register, prepare, desktopShortcut, fresh;
        public Action<string> Log = s => { };
        public Action<double, string> Progress = (f, s) => { };
        public Action<int> StepStarted = i => { };
        public Action<double> Downloaded = bytes => { };  // bytes fetched so far by the package step, for speed and ETA
        volatile bool cancelled;
        volatile Process current;

        // Share of the progress bar per step, and what the package downloads add up to (torch, TensorRT, ...).
        const double WExtract = 0.04, WPython = 0.02, WPip = 0.02, WPackages = 0.77, WPrepare = 0.15;
        public const double ExpectedDownloadBytes = 5.0e9;

        /// <param name="fresh">Remove the downloaded runtime first, so everything is downloaded again.</param>
        public Installer(Paths p, bool extract, bool register, bool prepare, bool desktopShortcut, bool fresh)
        {
            this.p = p; this.extract = extract; this.register = register; this.prepare = prepare;
            this.desktopShortcut = desktopShortcut; this.fresh = fresh;
        }

        public static string Message(Exception e)
        {
            while (e is AggregateException && e.InnerException != null) e = e.InnerException;
            return e.Message;
        }

        public void Cancel()
        {
            cancelled = true;
            try { var c = current; if (c != null && !c.HasExited) c.Kill(); } catch (Exception) { }
        }

        public void Run()
        {
            Log($"Keel {BuildInfo.Version} setup, {DateTime.Now:yyyy-MM-dd HH:mm:ss}, into {p.Install}");
            if (p.Install.Any(c => c > 127))  // (diagnostics hide the user's name, which can be where they are)
                Log("Note: the install folder's path has letters outside English A-Z. If a step fails, a folder like C:\\Keel is worth a try.");
            Directory.CreateDirectory(p.Install);
            Directory.CreateDirectory(p.Runtime);
            if (File.Exists(p.Ready)) File.Delete(p.Ready);
            if (fresh)
            {
                Step("Removing the old runtime");
                string pyDir = Path.GetDirectoryName(p.Python);
                if (Directory.Exists(pyDir)) Directory.Delete(pyDir, true);
            }
            double at = 0;
            StepStarted(0);
            if (extract) ExtractPayload(at, WExtract);
            Paths.AdoptChiaki(p, Log);
            at += WExtract;
            StepStarted(1);
            InstallPython(at, WPython); at += WPython;
            InstallPip(at, WPip); at += WPip;
            StepStarted(2);
            InstallPackages(at, WPackages); at += WPackages;
            StepStarted(3);
            if (prepare) Prepare(at, WPrepare);
            StepStarted(4);
            if (register) Register();
            TryDelete(Path.Combine(p.Runtime, "downloads"));
            File.WriteAllText(p.Ready, $"Keel {BuildInfo.Version} runtime ready {DateTime.Now:yyyy-MM-dd HH:mm}\r\n");
            Log("== Done");
            StepStarted(5);
            Progress(1, "Done");
        }

        void Step(string s) { Log("== " + s); }
        void ThrowIfCancelled() { if (cancelled) throw new OperationCanceledException("Cancelled."); }

        void ExtractPayload(double at, double w)
        {
            Step("Unpacking Keel");
            string root = Path.GetFullPath(p.Install).TrimEnd('\\') + "\\";
            int files = 0;
            using (var z = new ZipArchive(Payload.Open(), ZipArchiveMode.Read))
            {
                long total = Math.Max(1, z.Entries.Sum(e => e.Length)), done = 0;
                foreach (var e in z.Entries)
                {
                    ThrowIfCancelled();
                    string dest = Path.GetFullPath(Path.Combine(p.Install, e.FullName));
                    if (!dest.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        continue;  // never write outside the install folder
                    if (e.FullName.EndsWith("/")) { Directory.CreateDirectory(dest); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(dest));
                    try { e.ExtractToFile(dest, true); }
                    catch (IOException ex)
                    {
                        throw new Exception($"Couldn't write {e.FullName} ({ex.Message}). Close Keel and chiaki-ng if they're running, then press Retry.");
                    }
                    files++;
                    done += e.Length;
                    Progress(at + w * done / total, "Unpacking Keel");
                }
            }
            Log($"Unpacked {files} files");
        }

        void InstallPython(double at, double w)
        {
            if (File.Exists(p.Python)) { Log("Python already installed"); return; }
            Step("Downloading Python " + BuildInfo.PythonVersion);
            string dl = Path.Combine(p.Runtime, "downloads");
            Directory.CreateDirectory(dl);
            string zip = Path.Combine(dl, Path.GetFileName(BuildInfo.PythonUrl));
            Download(BuildInfo.PythonUrl, zip, f => Progress(at + w * 0.8 * f, "Downloading Python"));
            string sha = Sha256(zip);
            if (!sha.Equals(BuildInfo.PythonSha256, StringComparison.OrdinalIgnoreCase))
            {
                File.Delete(zip);
                throw new Exception($"The Python download isn't the expected file (SHA-256 {sha}). Press Retry.");
            }
            string dir = Path.GetDirectoryName(p.Python);
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            ZipFile.ExtractToDirectory(zip, dir);
            // The embeddable Python only sees what its ._pth file lists: add site-packages and the app folder.
            var pth = Directory.GetFiles(dir, "python3*._pth").FirstOrDefault();
            if (pth == null) throw new Exception("Unexpected Python package: it has no ._pth file.");
            string zipName = Path.GetFileNameWithoutExtension(pth) + ".zip";
            File.WriteAllText(pth, zipName + "\r\n.\r\nLib\\site-packages\r\n..\\..\\app\r\nimport site\r\n");
            File.Delete(zip);
            Progress(at + w, "Python ready");
        }

        void InstallPip(double at, double w)
        {
            string pyDir = Path.GetDirectoryName(p.Python);
            bool havePip = File.Exists(Path.Combine(pyDir, "Scripts", "pip.exe"));
            bool haveTools = Directory.Exists(Path.Combine(pyDir, "Lib", "site-packages", "setuptools")) &&
                             Directory.Exists(Path.Combine(pyDir, "Lib", "site-packages", "wheel"));
            if (havePip && haveTools) { Log("pip already installed"); return; }
            Step("Installing pip");
            // setuptools and wheel too: tensorrt-cu12 is a source package, and pip's isolated build environment
            // doesn't work in the embeddable Python (its ._pth file hides the environment), so packages are built
            // against these instead (--no-build-isolation below).
            string tools = "\"setuptools>=70,<82\" \"wheel>=0.40\" --no-warn-script-location" + ProxyArg;  // torch wants setuptools<82
            int rc;
            if (havePip)
                rc = RunPython("-m pip install " + tools, p.Runtime, Log);
            else
            {
                string getpip = Path.Combine(p.Runtime, "downloads", "get-pip.py");
                Directory.CreateDirectory(Path.GetDirectoryName(getpip));
                Download("https://bootstrap.pypa.io/get-pip.py", getpip, f => Progress(at + w * 0.5 * f, "Installing pip"));
                rc = RunPython("\"" + getpip + "\" " + tools, p.Runtime, Log);
            }
            if (rc != 0) throw new Exception("Installing pip failed (exit code " + rc + "). See the log, then press Retry.");
            Progress(at + w, "pip ready");
        }

        static string ProxyArg { get { return SystemCheck.Proxy != null ? " --proxy \"" + SystemCheck.Proxy + "\"" : ""; } }

        void InstallPackages(double at, double w)
        {
            Step("Downloading PyTorch, TensorRT and the other packages (about 5 GB)");
            string req = Path.Combine(p.App, "requirements-runtime.txt");
            string cache = Path.Combine(p.Runtime, "pip-cache");  // kept until success, so Retry doesn't download it all again
            double doneBytes = 0, curTotal = 0, cur = 0;
            string pkg = "packages";
            var progress = new Regex(@"^Progress (\d+) of (\d+)");
            var collecting = new Regex(@"^Collecting (\S+)");
            Action<string> onLine = line =>
            {
                var m = progress.Match(line);
                if (m.Success)
                {
                    double c = double.Parse(m.Groups[1].Value), t = double.Parse(m.Groups[2].Value);
                    if (t != curTotal || c < cur) { doneBytes += cur; curTotal = t; }
                    cur = c;
                    Downloaded(doneBytes + c);
                    double f = Math.Min(0.92, (doneBytes + c) / ExpectedDownloadBytes);
                    Progress(at + w * f, $"Downloading {pkg}  ({c / 1e9:0.00} of {t / 1e9:0.00} GB)");
                    return;
                }
                Log(line);
                var mc = collecting.Match(line);
                if (mc.Success) pkg = Regex.Replace(mc.Groups[1].Value, @"[=<>!~;\[].*", "");
                if (line.StartsWith("Installing collected packages"))
                    Progress(at + w * 0.94, "Installing the packages (a few minutes)");
            };
            string args = "-m pip install --no-warn-script-location --disable-pip-version-check --progress-bar raw --no-build-isolation " +
                          "--cache-dir \"" + cache + "\" -r \"" + req + "\" --extra-index-url " + BuildInfo.TorchIndex +
                          " --extra-index-url " + BuildInfo.NvidiaIndex + ProxyArg;  // TensorRT's wheels are on NVIDIA's index
            int rc = RunPython(args, p.App, onLine);
            if (rc != 0) throw new Exception("Installing the packages failed (exit code " + rc + "). Check the internet connection, then press Retry.");
            TryDelete(cache);
            Progress(at + w, "Packages installed");
        }

        void Prepare(double at, double w)
        {
            Step("Optimising for your graphics card (once, a few minutes)");
            Progress(at, "Optimising for your graphics card");
            var stepRe = new Regex(@"step (\d+) of (\d+)");
            string lastError = null;
            int rc = RunPython("-m framegen.prepare", p.App, line =>
            {
                Log(line);
                if (line.Contains("DLL load failed") || line.Contains("ImportError")) lastError = line;
                var m = stepRe.Match(line);
                if (m.Success && line.Contains("optimising"))
                {
                    double i = double.Parse(m.Groups[1].Value), n = double.Parse(m.Groups[2].Value);
                    Progress(at + w * (i - 1) / n, $"Optimising for your graphics card, step {i} of {n} (a minute or two each)");
                }
            });
            if (rc == 2) throw new Exception("PyTorch can't use the NVIDIA card. Update the NVIDIA driver, then press Retry.");
            if (rc != 0 && lastError != null)
                throw new Exception("PyTorch couldn't load (" + lastError.Trim() + "). This usually means the Microsoft Visual C++ runtime " +
                                    "is missing or old: install it from the PC check, then press Retry.");
            if (rc != 0) throw new Exception("Preparing the graphics card failed (exit code " + rc + "). See the log, then press Retry.");
            Progress(at + w, "Graphics card ready");
        }

        void Register()
        {
            Step("Adding shortcuts");
            string exe = Path.Combine(p.Install, "Keel.exe");
            Shortcuts.Create(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Keel.lnk"), exe, p.Install);
            if (desktopShortcut)
                Shortcuts.Create(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Keel.lnk"), exe, p.Install);
            using (var k = Registry.CurrentUser.CreateSubKey(Uninstaller.Key))
            {
                k.SetValue("DisplayName", "Keel");
                k.SetValue("DisplayVersion", BuildInfo.Version);
                k.SetValue("Publisher", "the Keel authors");
                k.SetValue("InstallLocation", p.Install);
                k.SetValue("DisplayIcon", exe);
                k.SetValue("UninstallString", "\"" + exe + "\" --uninstall");
                k.SetValue("NoModify", 1, RegistryValueKind.DWord);
                k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                k.SetValue("EstimatedSize", (int)(DirSize(p.Install) / 1024), RegistryValueKind.DWord);
            }
        }

        int RunPython(string args, string cwd, Action<string> onLine)
        {
            ThrowIfCancelled();
            // Temporary files (pip unpacks multi-GB wheels) go on the install drive, not C:\...\Temp.
            string tmp = Path.Combine(p.Runtime, "tmp");
            Directory.CreateDirectory(tmp);
            var psi = Proc.Python(p.Python, args, cwd);
            psi.EnvironmentVariables["TMP"] = tmp;
            psi.EnvironmentVariables["TEMP"] = tmp;
            try { return Proc.Run(psi, onLine, pr => current = pr); }
            finally
            {
                current = null;
                TryDelete(tmp);
                ThrowIfCancelled();
            }
        }

        void Download(string url, string dest, Action<double> onProgress)
        {
            Log("Downloading " + url);
            using (var http = new HttpClient { Timeout = TimeSpan.FromMinutes(30) })
            {
                http.DefaultRequestHeaders.UserAgent.ParseAdd("Keel-installer/" + BuildInfo.Version);
                using (var resp = http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead).Result)
                {
                    resp.EnsureSuccessStatusCode();
                    long total = resp.Content.Headers.ContentLength ?? -1, got = 0;
                    using (var src = resp.Content.ReadAsStreamAsync().Result)
                    using (var dst = File.Create(dest + ".part"))
                    {
                        var buf = new byte[1 << 16];
                        int n;
                        while ((n = src.Read(buf, 0, buf.Length)) > 0)
                        {
                            ThrowIfCancelled();
                            dst.Write(buf, 0, n);
                            got += n;
                            if (total > 0) onProgress((double)got / total);
                        }
                    }
                }
            }
            if (File.Exists(dest)) File.Delete(dest);
            File.Move(dest + ".part", dest);
        }

        static string Sha256(string file)
        {
            using (var h = System.Security.Cryptography.SHA256.Create())
            using (var f = File.OpenRead(file))
                return BitConverter.ToString(h.ComputeHash(f)).Replace("-", "");
        }

        static void TryDelete(string dir) { try { if (Directory.Exists(dir)) Directory.Delete(dir, true); } catch (Exception) { } }

        public static long DirSize(string dir)
        {
            try { return new DirectoryInfo(dir).EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length); }
            catch (Exception) { return 0; }
        }
    }

    static class Proc
    {
        /// <summary>Where NVIDIA VFG is (or would go): Paths.Vfx, set once the launcher knows its paths.</summary>
        public static string VfxPath;

        /// <summary>A Python process with UTF-8, unbuffered output. The embeddable Python ignores PYTHON* variables
        /// (its ._pth file puts it in isolated mode), hence -u and -X utf8 on the command line.</summary>
        public static ProcessStartInfo Python(string python, string args, string cwd)
        {
            var psi = new ProcessStartInfo(python, "-u -X utf8 " + args)
            {
                WorkingDirectory = cwd, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
            };
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";  // for a dev venv Python, which does read them
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PIP_DISABLE_PIP_VERSION_CHECK"] = "1";
            if (!string.IsNullOrEmpty(VfxPath)) psi.EnvironmentVariables["FRAMEGEN_VFX_PATH"] = VfxPath;  // a plain variable: the embeddable Python passes it on
            return psi;
        }

        /// <summary>Runs to completion, feeding every output line (stdout and stderr) to onLine.</summary>
        /// <summary>Runs a process to the end, each output line to onLine. timeoutMs: past it the process is stopped and
        /// TimedOut (-2) comes back (a capture driver that never answers mustn't leave the Play page looking forever).</summary>
        public static int Run(ProcessStartInfo psi, Action<string> onLine, Action<Process> started, int timeoutMs = -1)
        {
            using (var pr = new Process { StartInfo = psi })
            {
                pr.OutputDataReceived += (s, e) => { if (e.Data != null) onLine(e.Data); };
                pr.ErrorDataReceived += (s, e) => { if (e.Data != null) onLine(e.Data); };
                pr.Start();
                if (started != null) started(pr);
                pr.BeginOutputReadLine();
                pr.BeginErrorReadLine();
                if (!pr.WaitForExit(timeoutMs))
                {
                    try { pr.Kill(); } catch (Exception) { }
                    return TimedOut;
                }
                pr.WaitForExit();  // the rest of the output
                return pr.ExitCode;
            }
        }

        public const int TimedOut = -2;

        /// <summary>A process's exit code in words: Windows' crash codes are named (a camera driver that crashes while
        /// devices are listed takes Python down with 0xC0000005).</summary>
        public static string Describe(int rc)
        {
            switch ((uint)rc)
            {
                case 0xC0000005: return "Python crashed (0xC0000005, access violation): usually a camera, capture or virtual-camera driver";
                case 0xC0000409: return "Python crashed (0xC0000409)";
                case 0xC0000135: return "a file Python needs is missing (0xC0000135): Repair Keel on the Help page";
                case 0xC000013A: return "it was stopped (0xC000013A)";
            }
            return rc == TimedOut ? "it didn't finish in time" : rc < 0 ? $"exit code 0x{(uint)rc:X8}" : "exit code " + rc;
        }

        public static void Open(string target)
        {
            try { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); } catch (Exception) { }
        }
    }

    static class Shortcuts
    {
        public static void Create(string lnk, string target, string workDir)
        {
            var t = Type.GetTypeFromProgID("WScript.Shell");
            object shell = Activator.CreateInstance(t);
            object sc = t.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnk });
            var st = sc.GetType();
            st.InvokeMember("TargetPath", BindingFlags.SetProperty, null, sc, new object[] { target });
            st.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, sc, new object[] { workDir });
            st.InvokeMember("Description", BindingFlags.SetProperty, null, sc, new object[] { "Keel: frame generation for 30 fps console games" });
            st.InvokeMember("IconLocation", BindingFlags.SetProperty, null, sc, new object[] { target + ",0" });
            st.InvokeMember("Save", BindingFlags.InvokeMethod, null, sc, null);
        }
    }

    static class Uninstaller
    {
        public const string Key = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Keel";

        /// <param name="confirmed">The launcher already asked (its Help page).</param>
        public static int Run(bool confirmed)
        {
            string dir = Paths.ExeDir;
            if (!Paths.ForInstall(dir).IsInstall)
            {
                System.Windows.MessageBox.Show("This copy of Keel isn't an installed one, so there's nothing to remove.", "Keel");
                return 1;
            }
            if (!confirmed && System.Windows.MessageBox.Show("Remove Keel from\n" + dir + "?\n\nYour settings are kept.", "Uninstall Keel",
                                               System.Windows.MessageBoxButton.OKCancel, System.Windows.MessageBoxImage.Question)
                != System.Windows.MessageBoxResult.OK)
                return 1;
            foreach (var lnk in new[] {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Keel.lnk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Keel.lnk") })
                try { if (File.Exists(lnk)) File.Delete(lnk); } catch (Exception) { }
            try { Registry.CurrentUser.DeleteSubKeyTree(Key, false); } catch (Exception) { }
            // Only what setup created is removed, then the folder itself if that leaves it empty. This exe is inside
            // it, so a short-lived cmd does the deleting once we've exited.
            var cmd = new StringBuilder("/c ping 127.0.0.1 -n 3 > nul");
            foreach (var sub in new[] { "app", "runtime", "chiaki", "logs" })
                cmd.Append($" & rmdir /s /q \"{Path.Combine(dir, sub)}\"");
            foreach (var f in new[] { "Keel.exe", "install.log", "LICENSE.txt", "THIRD_PARTY_NOTICES.txt" })
                cmd.Append($" & del /q \"{Path.Combine(dir, f)}\"");
            cmd.Append($" & rmdir \"{dir}\"");
            Process.Start(new ProcessStartInfo("cmd.exe", cmd.ToString())
            { CreateNoWindow = true, UseShellExecute = false, WorkingDirectory = Path.GetTempPath() });
            return 0;
        }
    }
}
