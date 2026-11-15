// installer\build.py's signing step for updates (the format: launcher\UpdateSignature.cs). Built and run by build.py:
//
//   SignUpdate key <keyfile>                     make the key if there's none yet; print its public part (RSA XML)
//   SignUpdate sign <keyfile> <file> <version>   write <file>.sig
using System;
using System.IO;
using System.Security.Cryptography;

namespace SmoovPlay
{
    static class SignUpdateTool
    {
        static int Main(string[] a)
        {
            if (a.Length == 2 && a[0] == "key")
            {
                if (!File.Exists(a[1]))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(a[1])));
                    File.WriteAllText(a[1], UpdateSignature.NewKey());
                    Console.Error.WriteLine("made a new update signing key: " + a[1]);
                }
                Console.WriteLine(UpdateSignature.PublicPart(File.ReadAllText(a[1])));
                return 0;
            }
            if (a.Length == 4 && a[0] == "sign")
            {
                string sha;
                using (var h = SHA256.Create())
                using (var f = File.OpenRead(a[2]))
                    sha = BitConverter.ToString(h.ComputeHash(f)).Replace("-", "");
                string sig = UpdateSignature.Make(File.ReadAllText(a[1]), a[3], sha);
                if (UpdateSignature.Check(UpdateSignature.PublicPart(File.ReadAllText(a[1])), sig, sha) != a[3])
                    throw new Exception("the signature didn't check out");
                File.WriteAllText(a[2] + ".sig", sig);
                return 0;
            }
            Console.Error.WriteLine("usage: SignUpdate key <keyfile> | SignUpdate sign <keyfile> <file> <version>");
            return 2;
        }
    }
}
