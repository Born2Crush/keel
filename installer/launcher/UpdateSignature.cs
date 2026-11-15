// What makes the launcher trust a downloaded update. Shared with installer\tools\SignUpdate.cs, which signs.
using System;
using System.Security.Cryptography;
using System.Text;

namespace SmoovPlay
{
    /// <summary>
    /// A release carries SmoovPlay-Setup.exe.sig next to the setup. installer\build.py makes it with a private key
    /// that stays on the PC that builds releases (never in the repo or on GitHub), and the launcher checks it against
    /// the public key built into it (BuildInfo.UpdateKey). It names the version and the setup's SHA-256, so a release
    /// installs only the setup that was signed, as the version it was signed as: someone with access to the GitHub
    /// account alone can't push an update, or pass an older setup off as a newer one.
    ///
    ///     SmoovPlay update signature 1
    ///     version 0.2.0
    ///     sha256 3F2A...
    ///     signature BASE64   (RSA PKCS#1 v1.5 with SHA-256 over the three lines above, each ending in \n)
    /// </summary>
    static class UpdateSignature
    {
        const string Head = "SmoovPlay update signature 1";
        public const int KeyBits = 3072;

        static string Signed(string version, string sha256)
        {
            return Head + "\n" + "version " + version + "\n" + "sha256 " + sha256.ToUpperInvariant() + "\n";
        }

        /// <summary>A new key pair, as RSA XML with the private part.</summary>
        public static string NewKey()
        {
            using (var rsa = new RSACryptoServiceProvider(KeyBits) { PersistKeyInCsp = false })
                return rsa.ToXmlString(true);
        }

        public static string PublicPart(string keyXml)
        {
            using (var rsa = new RSACryptoServiceProvider { PersistKeyInCsp = false })
            {
                rsa.FromXmlString(keyXml);
                return rsa.ToXmlString(false);
            }
        }

        /// <summary>The .sig text for a setup (its SHA-256 as hex) released as `version`.</summary>
        public static string Make(string privateKeyXml, string version, string sha256)
        {
            if (version.IndexOfAny(new[] { '\n', '\r', ' ' }) >= 0) throw new ArgumentException("bad version: " + version);
            string text = Signed(version, sha256);
            using (var rsa = new RSACryptoServiceProvider { PersistKeyInCsp = false })
            {
                rsa.FromXmlString(privateKeyXml);
                byte[] sig = rsa.SignData(Encoding.UTF8.GetBytes(text), HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
                return text + "signature " + Convert.ToBase64String(sig) + "\n";
            }
        }

        /// <summary>The version a .sig vouches for, if it's genuine and names this SHA-256 (hex); otherwise null.</summary>
        public static string Check(string publicKeyXml, string sigText, string sha256)
        {
            if (string.IsNullOrEmpty(publicKeyXml) || sigText == null) return null;
            var l = sigText.Replace("\r", "").Split('\n');
            if (l.Length < 4 || l[0] != Head || !l[1].StartsWith("version ") || !l[2].StartsWith("sha256 ") ||
                !l[3].StartsWith("signature ")) return null;
            string version = l[1].Substring(8), hash = l[2].Substring(7);
            if (version.Length == 0 || version.Contains(" ") || !hash.Equals(sha256, StringComparison.OrdinalIgnoreCase)) return null;
            byte[] sig;
            try { sig = Convert.FromBase64String(l[3].Substring(10).Trim()); }
            catch (FormatException) { return null; }
            using (var rsa = new RSACryptoServiceProvider { PersistKeyInCsp = false })
            {
                rsa.FromXmlString(publicKeyXml);
                return rsa.VerifyData(Encoding.UTF8.GetBytes(Signed(version, hash)), sig, HashAlgorithmName.SHA256,
                                      RSASignaturePadding.Pkcs1) ? version : null;
            }
        }
    }
}
