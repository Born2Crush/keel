// Experimental features: built from the best information available, without the hardware to test every setup
// (Low delay presentation, capture cards, window capture, and AMD Radeon and Intel Arc cards through DirectML). Each is marked with ExperimentalBadge() and
// has a Send feedback button: it copies diagnostics (the same text as Copy diagnostics, which leaves out the Windows
// user name and NVIDIA VFG's results) and opens a new GitHub issue from .github/ISSUE_TEMPLATE/experimental-feedback.yml
// in the browser, titled for the feature. SmoovPlay itself sends nothing: the user pastes and posts it.

using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;

namespace SmoovPlay
{
    partial class Ui
    {
        const string ExperimentalWhy = "Experimental: built from the best information we have, without the hardware to test every "
                                     + "setup. Tell us how it goes with Send feedback.";

        static string RepoUrl
        {
            get { return "https://github.com/" + (BuildInfo.ReleasesRepo.Length > 0 ? BuildInfo.ReleasesRepo : "smoovplay/smoovplay"); }
        }

        /// <summary>The new-issue page for feedback on a feature ("" for general feedback), with the form's title and
        /// feature field filled in.</summary>
        static string FeedbackUrl(string feature)
        {
            string title = feature.Length > 0 ? "[Experimental] " + feature + ": " : "[Feedback] ";
            return RepoUrl + "/issues/new?template=experimental-feedback.yml&title=" + Uri.EscapeDataString(title)
                   + (feature.Length > 0 ? "&feature=" + Uri.EscapeDataString(feature) : "");
        }

        /// <summary>A small "EXPERIMENTAL" tag, for next to a feature's name.</summary>
        FrameworkElement ExperimentalBadge()
        {
            var b = new Border
            {
                CornerRadius = new CornerRadius(4), Padding = new Thickness(5, 1, 5, 1), Margin = new Thickness(7, 0, 0, 0),
                Background = Solid("#26FFC24B"), BorderBrush = Solid("#66FFC24B"), BorderThickness = new Thickness(1),
                VerticalAlignment = VerticalAlignment.Center, ToolTip = ExperimentalWhy,
                Child = new TextBlock { Text = "EXPERIMENTAL", FontSize = 9.5, FontWeight = FontWeights.SemiBold, Foreground = R("Warn") },
            };
            AutomationProperties.SetName(b, "Experimental");
            return b;
        }

        /// <summary>A "Send feedback" pill for an Experimental feature.</summary>
        Button FeedbackPill(string feature, string automationId)
        {
            var b = new Button { Style = S("Pill"), Content = "Send feedback", Margin = new Thickness(8, 4, 4, 4), VerticalAlignment = VerticalAlignment.Center,
                                 ToolTip = "Copies diagnostics and opens a GitHub page for your report on " + feature + ". Nothing is sent until you post it.",
                                 Cursor = Cursors.Hand };
            AutomationProperties.SetAutomationId(b, automationId);
            b.Click += (s, e) => SendFeedback(feature);
            return b;
        }

        /// <summary>Copies diagnostics, then opens the feedback form for the feature ("" for general feedback).</summary>
        void SendFeedback(string feature)
        {
            string last = session != null ? session.LogPath : null;
            Toast("Collecting diagnostics...", false);
            Task.Run(() =>
            {
                string text;
                try { text = Diagnostics.Collect(paths, last); }
                catch (Exception e) { text = "Couldn't collect diagnostics: " + e.Message; }
                OnUi(() =>
                {
                    bool copied;
                    try { Clipboard.SetText(text); copied = true; }
                    catch (Exception) { copied = false; }
                    Proc.Open(FeedbackUrl(feature));
                    Toast(copied ? "Diagnostics copied. Paste them into the GitHub page that just opened"
                                 : "Opened the GitHub page. Copy diagnostics on the Help page and paste them there", !copied);
                });
            });
        }
    }
}
