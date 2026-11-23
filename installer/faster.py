"""200 Mbps, and the frame check overlaps the draw. Safe to run twice, after use_vsr.py."""
from pathlib import Path


def patch_rate(text: str) -> str:
    """Ask chiaki-ng for 200 Mbps, on the settings key it actually reads."""
    text = text.replace("const int bitrate = 100000;", "const int bitrate = 200000;")
    text = text.replace("H.265, 100 Mbps", "H.265, 200 Mbps")
    text = text.replace("H.265 100 Mbps", "H.265 200 Mbps")
    text = text.replace("H.265 and 100 Mbps", "H.265 and 200 Mbps")
    done = ("That is the highest Remote Play size. Keel raises chiaki-ng's bitrate slider from 100 to 200 "
            "and writes 200. The console may send less.")
    for old in (
        "That is the highest Remote Play size, and the top of chiaki-ng's bitrate slider.",
        "That is the highest Remote Play size. chiaki-ng's slider stops at 100; Keel writes 200, "
        "which chiaki-ng sends on. The console may send less.",
    ):
        text = text.replace(old, done)
    # A verbatim C# string. Two backslashes here are one in the path, which is not where chiaki-ng looks.
    text = text.replace(
        '@"Software\\\\Chiaki\\\\Chiaki\\\\settings"',
        '@"Software\\Chiaki\\Chiaki\\settings"',
    )
    text = text.replace(
        '@"Software\\\\Chiaki\\\\" + app + @"\\\\settings"',
        '@"Software\\Chiaki\\" + app + @"\\settings"',
    )
    return text


def patch_cap(text: str) -> str:
    """Before chiaki-ng starts, raise the bitrate slider that is compiled into it from 100 to 200."""
    if "RaiseBitrateCap(" in text:
        return text
    old = '            psi.EnvironmentVariables["CHIAKI_FRAMETAP"] = "1";\n            log(ApplyVideoProfile());\n'
    new = '            psi.EnvironmentVariables["CHIAKI_FRAMETAP"] = "1";\n            log(RaiseBitrateCap(p));\n            log(ApplyVideoProfile());\n'
    if old not in text:
        raise SystemExit("speed patch could not find where chiaki-ng starts")
    text = text.replace(old, new, 1)
    method = '''        /// <summary>chiaki-ng's bitrate slider is compiled in at 100 Mbps. Raise that to 200 in chiaki.exe
        /// before it starts, so 200 is a value the program can hold. A chiaki-ng that is already open is left
        /// alone: quit it and press Play.</summary>
        public static string RaiseBitrateCap(Paths p)
        {
            try
            {
                string script = Path.Combine(p.App, "framegen", "chiaki_bitrate.py");
                if (!File.Exists(script) || string.IsNullOrEmpty(p.Python) || !File.Exists(p.Python))
                    return "[keel] chiaki-ng bitrate cap left as it is (this copy has no updater)";
                var lines = new List<string>();
                int rc = Proc.Run(Proc.Python(p.Python, "-m framegen.chiaki_bitrate \\"" + p.Chiaki + "\\"", p.App),
                    line => lines.Add(line), null, 20000);
                string msg = lines.Count > 0 ? lines[lines.Count - 1] : "";
                if (rc != 0)
                    return "[keel] chiaki-ng bitrate cap left as it is" + (msg.Length > 0 ? " (" + msg + ")" : "");
                return msg.Length > 0 ? msg : "[keel] chiaki-ng bitrate slider now goes to 200 Mbps";
            }
            catch (Exception e)
            {
                return "[keel] chiaki-ng bitrate cap left as it is (" + e.Message + ")";
            }
        }

        public static bool Start'''
    anchor = "        public static bool Start"
    if text.count(anchor) != 1:
        raise SystemExit("speed patch could not find Chiaki.Start")
    return text.replace(anchor, method, 1)
