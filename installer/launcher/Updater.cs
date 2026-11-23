// Updates from GitHub releases: the daily check (UpdateCheck), and installing one (Updater). installer\README.md,
// "Updates", has the release side.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace Keel
{
    /// <summary>A release on GitHub, as far as updating goes: its tag and the ids of its setup and signature files.</summary>
    class Release
    {
        public string Tag;
        public long SetupId, SetupSize, SigId;
        public string SetupDigest;  // "sha256:<hex>", which GitHub works out for each uploaded file (null if not given)
        // A signed .sig is the strong check. Without one, GitHub's own sha256 of the setup is enough:
        // the download has to be the file GitHub attached to the release, and it installs over this copy.
        public bool CanInstall
        {
            get { return SetupId > 0 && (SigId > 0 || (SetupDigest != null && SetupDigest.StartsWith("sha256:"))); }
        }
    }

    /// <summary>
    /// GitHub's releases API. The repo is BuildInfo.ReleasesRepo (empty: no updates). For testing,
    /// KEEL_RELEASES_REPO picks another repo, KEEL_RELEASES_API another server (a local stand-in), and
    /// KEEL_GITHUB_TOKEN is sent to the API, which a private repo needs; never to where downloads redirect.
    /// Requests carry only the app's version, in the User-Agent.
    /// </summary>
    static class GitHub
    {
        public const string SetupName = "Keel-Setup.exe";

        public static string Repo
        {
            get
            {
                string r = Environment.GetEnvironmentVariable("KEEL_RELEASES_REPO") ?? BuildInfo.ReleasesRepo;
                return Regex.IsMatch(r ?? "", @"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") ? r : null;
            }
        }

        static string Api { get { return (Environment.GetEnvironmentVariable("KEEL_RELEASES_API") ?? "https://api.github.com").TrimEnd('/'); } }
        static string Token
        {
            get
            {
                string t = Environment.GetEnvironmentVariable("KEEL_GITHUB_TOKEN");
                if (!string.IsNullOrWhiteSpace(t)) return t.Trim();
                try
                {
                    // One line. A private repo's releases are invisible without it. Never logged.
                    string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                                               "framegen", "github-token.txt");
                    if (!File.Exists(path)) return null;
                    string line = File.ReadAllText(path).Trim();
                    int cut = line.IndexOfAny(new[] { '\r', '\n' });
                    if (cut >= 0) line = line.Substring(0, cut).Trim();
                    return line.Length > 0 ? line : null;
                }
                catch (Exception) { return null; }
            }
        }

        public static string Page(string repo, string tag) { return "https://github.com/" + repo + "/releases/tag/" + Uri.EscapeDataString(tag); }

        static HttpClient Client(TimeSpan timeout)
        {
            var http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = timeout };
            http.DefaultRequestHeaders.UserAgent.ParseAdd("Keel/" + BuildInfo.Version);
            return http;
        }

        static HttpRequestMessage Request(string url, string accept, bool token)
        {
            var req = new HttpRequestMessage(HttpMethod.Get, url);
            req.Headers.Accept.ParseAdd(accept);
            if (token && !string.IsNullOrEmpty(Token)) req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);
            return req;
        }

        /// <summary>The newest release that isn't a draft (pre-releases count: betas are published as those), or null.</summary>
        public static Release Newest(string repo)
        {
            string json;
            using (var http = Client(TimeSpan.FromSeconds(15)))
            using (var req = Request(Api + "/repos/" + repo + "/releases?per_page=30", "application/vnd.github+json", true))
            using (var resp = http.SendAsync(req).Result)
            {
                if ((int)resp.StatusCode == 404)
                    throw new Exception("GitHub hid the releases (404). The repo is private, so put a GitHub token with access to it in %APPDATA%\\framegen\\github-token.txt, as one line, then check again. An update installs over this copy; you don't uninstall.");
                resp.EnsureSuccessStatusCode();
                json = resp.Content.ReadAsStringAsync().Result;
            }
            Release best = null;
            foreach (var o in new JavaScriptSerializer { MaxJsonLength = 16 << 20 }.DeserializeObject(json) as object[] ?? new object[0])
            {
                var r = o as Dictionary<string, object>;
                if (r == null || Get(r, "draft") as bool? == true) continue;
                string tag = Get(r, "tag_name") as string;
                if (UpdateCheck.Parse(tag) == null || (best != null && UpdateCheck.Compare(tag, best.Tag) <= 0)) continue;
                var rel = new Release { Tag = tag };
                foreach (var ao in Get(r, "assets") as object[] ?? new object[0])
                {
                    var a = ao as Dictionary<string, object>;
                    if (a == null) continue;
                    string name = Get(a, "name") as string;
                    if (name == SetupName)
                    {
                        rel.SetupId = Num(Get(a, "id"));
                        rel.SetupSize = Num(Get(a, "size"));
                        rel.SetupDigest = Get(a, "digest") as string;
                    }
                    else if (name == SetupName + ".sig") rel.SigId = Num(Get(a, "id"));
                }
                best = rel;
            }
            return best;
        }

        static object Get(Dictionary<string, object> d, string key) { object v; return d.TryGetValue(key, out v) ? v : null; }
        static long Num(object v) { return v is int ? (int)v : v is long ? (long)v : v is decimal ? (long)(decimal)v : 0; }

        public static void Download(string repo, long assetId, string dest, long maxBytes, Action<double> progress)
        {
            var api = new Uri(Api);
            var url = new Uri(Api + "/repos/" + repo + "/releases/assets/" + assetId);
            using (var http = Client(TimeSpan.FromMinutes(30)))
            {
                HttpResponseMessage resp = null;
                try
                {
                    for (int hop = 0; ; hop++)
                    {
                        using (var req = Request(url.ToString(), "application/octet-stream", hop == 0))
                            resp = http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead).Result;
                        int code = (int)resp.StatusCode;
                        if (code < 300 || code >= 400 || resp.Headers.Location == null) break;
                        if (hop >= 5) throw new Exception("too many redirects");
                        var next = resp.Headers.Location.IsAbsoluteUri ? resp.Headers.Location : new Uri(url, resp.Headers.Location);
                        if (next.Scheme != Uri.UriSchemeHttps && next.Scheme != api.Scheme)
                            throw new Exception("the download was sent somewhere without HTTPS");
                        resp.Dispose();
                        resp = null;
                        url = next;
                    }
                    resp.EnsureSuccessStatusCode();
                    long total = resp.Content.Headers.ContentLength ?? -1, got = 0;
                    if (total > maxBytes) throw new Exception("the file is far bigger than a Keel setup");
                    using (var src = resp.Content.ReadAsStreamAsync().Result)
                    using (var dst = File.Create(dest + ".part"))
                    {
                        var buf = new byte[1 << 16];
                        int n;
                        while ((n = src.Read(buf, 0, buf.Length)) > 0)
                        {
                            dst.Write(buf, 0, n);
                            got += n;
                            if (got > maxBytes) throw new Exception("the file is far bigger than a Keel setup");
                            if (total > 0) progress((double)got / total);
                        }
                    }
                }
                finally
                {
                    if (resp != null) resp.Dispose();
                }
            }
            if (File.Exists(dest)) File.Delete(dest);
            File.Move(dest + ".part", dest);
        }
    }

    static class Updater
    {
        public static string Folder { get { return Path.Combine(Path.GetTempPath(), "Keel-update"); } }

        public static string Unavailable(Paths p)
        {
            if (p.Dev) return "this is a development copy";
            if (!p.IsInstall) return "this isn't an installed copy";
            return null;
        }

        public static string Fetch(Release rel, string repo, Action<double> progress)
        {
            if (!rel.CanInstall) throw new Exception(rel.Tag + " has no setup this version can install by itself");
            if (rel.SetupSize > 1L << 30) throw new Exception("the release's setup is far bigger than expected");
            Directory.CreateDirectory(Folder);
            string setup = Path.Combine(Folder, "Keel-Setup-" + rel.Tag.TrimStart('v') + ".exe");
            string sig = setup + ".sig";
            try
            {
                bool signedRelease = rel.SigId > 0 && !string.IsNullOrEmpty(BuildInfo.UpdateKey);
                if (signedRelease) GitHub.Download(repo, rel.SigId, sig, 64 * 1024, f => { });
                GitHub.Download(repo, rel.SetupId, setup, 1L << 30, progress);
                string sha = Sha256(setup);
                if (rel.SetupDigest == null || !rel.SetupDigest.StartsWith("sha256:") ||
                    !rel.SetupDigest.Substring(7).Equals(sha, StringComparison.OrdinalIgnoreCase))
                    throw new Exception("the download came through damaged");
                string version = rel.Tag;
                if (signedRelease)
                {
                    string signed = UpdateSignature.Check(BuildInfo.UpdateKey, File.ReadAllText(sig), sha);
                    if (signed == null)
                        throw new Exception("the update isn't signed with Keel's key, so it wasn't installed");
                    if (UpdateCheck.Parse(signed) == null || UpdateCheck.Compare(signed, rel.Tag) != 0)
                        throw new Exception("the update was signed as another version (" + signed + "), so it wasn't installed");
                    version = signed;
                }
                if (UpdateCheck.Compare(version, BuildInfo.Version) <= 0)
                    throw new Exception(version + " isn't newer than this version");
                return setup;
            }
            catch
            {
                TryDelete(setup);
                TryDelete(sig);
                TryDelete(setup + ".part");
                throw;
            }
        }

        public static Process[] ChiakiInInstall(Paths p)
        {
            string dir = Path.GetFullPath(Path.Combine(p.Install, "chiaki")).TrimEnd('\\') + "\\";
            var found = new List<Process>();
            foreach (var pr in Process.GetProcessesByName("chiaki"))
                try { if (pr.MainModule.FileName.StartsWith(dir, StringComparison.OrdinalIgnoreCase)) found.Add(pr); }
                catch (Exception) { }
            return found.ToArray();
        }

        public static void Launch(string setup, Paths p)
        {
            string dir = Path.GetFullPath(p.Install).TrimEnd('\\');
            Process.Start(new ProcessStartInfo(setup, $"--update --dir \"{dir}\" --wait {Process.GetCurrentProcess().Id}")
            { UseShellExecute = false, WorkingDirectory = Folder });
        }

        public static void CleanUp()
        {
            try
            {
                if (!Directory.Exists(Folder)) return;
                foreach (var f in Directory.GetFiles(Folder)) TryDelete(f);
                Directory.Delete(Folder);
            }
            catch (Exception) { }
        }

        static void TryDelete(string f) { try { if (File.Exists(f)) File.Delete(f); } catch (Exception) { } }

        static string Sha256(string file)
        {
            using (var h = System.Security.Cryptography.SHA256.Create())
            using (var f = File.OpenRead(file))
                return BitConverter.ToString(h.ComputeHash(f)).Replace("-", "");
        }
    }

    static class UpdateCheck
    {
        static string CacheFile { get { return Path.Combine(Paths.SettingsDir, "keel-update.txt"); } }

        public static void Start(Action<string, string> onNewer)
        {
            string repo = GitHub.Repo;
            if (Environment.GetEnvironmentVariable("KEEL_UPDATE_CHECK") == "0" || repo == null) return;
            System.Threading.ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    string tag;
                    if (!Cached(repo, out tag)) tag = Fetch(repo);
                    if (tag != null && Compare(tag, BuildInfo.Version) > 0)
                        onNewer(tag, GitHub.Page(repo, tag));
                }
                catch (Exception) { }
            });
        }

        public static void Now(Action<string, string> done)
        {
            string repo = GitHub.Repo;
            System.Threading.ThreadPool.QueueUserWorkItem(_ =>
            {
                string tag = null, error = null;
                try { if (repo == null) error = "this build has no releases to check"; else tag = Fetch(repo); }
                catch (Exception e) { error = e.Message; }
                done(tag, error);
            });
        }

        static bool Cached(string repo, out string tag)
        {
            tag = null;
            try
            {
                var l = File.ReadAllLines(CacheFile);
                DateTime when;
                if (l.Length < 3 || l[1] != repo ||
                    !DateTime.TryParse(l[0], null, System.Globalization.DateTimeStyles.RoundtripKind, out when) ||
                    DateTime.UtcNow - when.ToUniversalTime() > TimeSpan.FromHours(6)) return false;
                tag = l[2].Length > 0 ? l[2] : null;
                return true;
            }
            catch (Exception) { return false; }
        }

        static string Fetch(string repo)
        {
            var rel = GitHub.Newest(repo);
            string best = rel != null ? rel.Tag : null;
            try
            {
                Directory.CreateDirectory(Paths.SettingsDir);
                File.WriteAllLines(CacheFile, new[] { DateTime.UtcNow.ToString("o"), repo, best ?? "" });
            }
            catch (Exception) { }
            return best;
        }

        public static Tuple<Version, string> Parse(string tag)
        {
            var m = Regex.Match(tag ?? "", @"^v?(\d{1,6})(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$");
            if (!m.Success) return null;
            Func<int, int> g = i => m.Groups[i].Success ? int.Parse(m.Groups[i].Value) : 0;
            return Tuple.Create(new Version(g(1), g(2), g(3), g(4)), m.Groups[5].Success ? m.Groups[5].Value : null);
        }

        public static int Compare(string a, string b)
        {
            var x = Parse(a);
            var y = Parse(b);
            if (x == null || y == null) return 0;
            int c = x.Item1.CompareTo(y.Item1);
            if (c != 0) return c;
            if (x.Item2 == null || y.Item2 == null) return (x.Item2 == null ? 1 : 0) - (y.Item2 == null ? 1 : 0);
            string[] p = x.Item2.Split('.'), q = y.Item2.Split('.');
            for (int i = 0; i < Math.Min(p.Length, q.Length); i++)
            {
                int u, v;
                c = int.TryParse(p[i], out u) && int.TryParse(q[i], out v) ? u.CompareTo(v) : string.CompareOrdinal(p[i], q[i]);
                if (c != 0) return Math.Sign(c);
            }
            return p.Length.CompareTo(q.Length);
        }
    }
}
