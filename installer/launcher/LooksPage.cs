// The launcher's Looks page: Keel's own looks and the user's (framegen/looks.py), and an editor for them laid
// out like the nodes of a color grading app: a look is a stack of effects, applied top to bottom, each with a switch,
// an amount and its own settings.
//
// The effect types, their settings and Keel's looks come from the pipeline (python -m framegen.looks schema),
// so a new effect shows up here with no launcher change. The user's looks are JSON files in %APPDATA%\framegen\looks
// ("my:<file name>"), which this page writes itself (framegen/looks.py reads and cleans them); share codes and LUT
// files go through python -m framegen.looks (encode, import, add-lut).
//
// While playing, the video shows the look picked here, live: changes go to the running pipeline as
// "look-show <JSON>" (framegen/remote.py), at most 25 times a second, and leaving the page sends "look-hide", so the
// video goes back to the Look settings' look. "Use this look" sets those (look, look_on). Keel's own looks can
// be changed freely to try things: the changes stay in memory (presetOriginal keeps what each was) and show on the
// preview and the video, and nothing is written until "Save as mine" makes a copy of yours ("My Cinematic"). Reset,
// or putting every change back, makes it Keel's look again.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace Keel
{
    partial class Ui
    {
        class LookParam
        {
            public string Key, Label, Unit;
            public double Min, Max, Step, Default;
            public bool Text;  // a file name (the LUT's)
            public readonly List<string> Names = new List<string>();  // a choice: the names of Min, Min + 1, ...
        }

        class LookType
        {
            public string Key, Label, Kind, Help;
            public readonly List<LookParam> Params = new List<LookParam>();  // the first is "mix", the amount
        }

        class LookEntry
        {
            public string Id, Name, Code, File;  // Code: Keel's looks only (yours are encoded when copied)
            public bool Builtin;
            public Dictionary<string, object> Look;  // {"name", "nodes": List of node dictionaries}
        }

        List<LookType> lookTypes;
        List<LookEntry> lookEntries;
        List<string> lookLuts = new List<string>();
        string lookFolder, lookLutFolder, looksError, lookPicked;
        int lookMaxNodes = 16;
        bool loadingLooks, settingSpecsStale;
        bool lookHidden;  // "Turn the look off" while playing: no preview until the next pick or change
        string lookScrolledTo;  // the look the list last scrolled to
        Session lookShownTo;  // the session a look-show went to (and no look-hide since)
        readonly Dictionary<string, List<bool>> lookOpen = new Dictionary<string, List<bool>>();  // look id -> nodes open
        // One of Keel's looks with changes being tried: its id -> the look as it was (JSON). Gone on Reset, on
        // Save as mine, or once the changes are all put back.
        readonly Dictionary<string, string> presetOriginal = new Dictionary<string, string>();
        DispatcherTimer lookTimer;
        bool lookSendDue;
        LookEntry lookSaveDue;
        DateTime lookSaveAt;

        bool LooksLive { get { return liveState != null && session != null && session.Running; } }

        // ---- loading
        void ShowLooks()
        {
            if (lookEntries == null) LoadLooks(null);
            ShowPage("PageLooks");
            RenderLooks(true);
            PreviewPicked();
        }

        /// <summary>Runs python -m framegen.looks (on a worker thread); its JSON line, or {"error": ...}.</summary>
        string LooksCli(string args)
        {
            var lines = new List<string>();
            int rc;
            try { rc = Proc.Run(Proc.Python(paths.Python, "-m framegen.looks " + args, paths.App), l => { lock (lines) lines.Add(l); }, null); }
            catch (Exception e) { return Json.Serialize(new Dictionary<string, object> { { "error", e.Message } }); }
            lock (lines)
            {
                string json = lines.LastOrDefault(l => l.StartsWith("{"));
                return json ?? Json.Serialize(new Dictionary<string, object> { { "error", lines.LastOrDefault(l => l.Trim().Length > 0) ?? ("exit code " + rc) } });
            }
        }

        static Dictionary<string, object> ParseCli(string json)
        {
            try { return Json.DeserializeObject(json) as Dictionary<string, object> ?? new Dictionary<string, object> { { "error", "no answer" } }; }
            catch (Exception e) { return new Dictionary<string, object> { { "error", e.Message } }; }
        }

        static string CliError(Dictionary<string, object> r) { return r.ContainsKey("error") ? r["error"] as string ?? "it failed" : null; }

        void LoadLooks(Action then)
        {
            if (loadingLooks || !paths.RuntimeReady) return;
            loadingLooks = true;
            Task.Run(() =>
            {
                string json = LooksCli("schema");
                OnUi(() =>
                {
                    loadingLooks = false;
                    var root = ParseCli(json);
                    looksError = CliError(root);
                    if (looksError == null)
                    {
                        try { ParseLooks(root); }
                        catch (Exception e) { looksError = e.Message; }
                    }
                    if (looksError != null) Program.Trace("looks: " + looksError);
                    RenderLooks(true);
                    if (then != null) then();
                });
            });
        }

        static double Num(object o) { return Convert.ToDouble(o, CultureInfo.InvariantCulture); }

        void ParseLooks(Dictionary<string, object> root)
        {
            lookFolder = (string)root["folder"];
            lookLutFolder = (string)root["lut_folder"];
            lookLuts = ((object[])root["luts"]).Cast<string>().ToList();
            lookMaxNodes = Convert.ToInt32(root["max_nodes"], CultureInfo.InvariantCulture);
            var types = new List<LookType>();
            foreach (Dictionary<string, object> t in (object[])root["types"])
            {
                var lt = new LookType { Key = (string)t["key"], Label = (string)t["label"], Kind = (string)t["kind"], Help = (string)t["help"] };
                foreach (Dictionary<string, object> p in (object[])t["params"])
                {
                    var lp = new LookParam { Key = (string)p["key"], Label = (string)p["label"], Unit = p["unit"] as string ?? "", Text = (bool)p["text"] };
                    if (!lp.Text) { lp.Min = Num(p["min"]); lp.Max = Num(p["max"]); lp.Step = Num(p["step"]); lp.Default = Num(p["default"]); }
                    lp.Names.AddRange(((object[])p["names"]).Cast<string>());
                    lt.Params.Add(lp);
                }
                types.Add(lt);
            }
            var entries = new List<LookEntry>();
            foreach (Dictionary<string, object> e in (object[])root["looks"])
            {
                var look = (Dictionary<string, object>)e["look"];
                look["nodes"] = ((object[])look["nodes"]).ToList();
                var entry = new LookEntry
                {
                    Id = (string)e["id"], Name = (string)e["name"], Builtin = (bool)e["builtin"], Look = look,
                    Code = e.ContainsKey("code") ? e["code"] as string : null, File = e.ContainsKey("path") ? e["path"] as string : null,
                };
                // A reload (a pasted code, say) keeps the changes being tried on Keel's looks.
                var was = entry.Builtin && presetOriginal.ContainsKey(entry.Id) && lookEntries != null ? lookEntries.FirstOrDefault(x => x.Id == entry.Id) : null;
                if (was != null) entry.Look = was.Look;
                entries.Add(entry);
            }
            foreach (var id in presetOriginal.Keys.Where(id => !entries.Any(x => x.Builtin && x.Id == id)).ToList()) presetOriginal.Remove(id);
            lookTypes = types;
            lookEntries = entries;
            ParseSamples(root, lookSamples);
            if (lookPicked == null || !entries.Any(x => x.Id == lookPicked))
            {
                string inUse = SettingText("look");
                lookPicked = entries.Any(x => x.Id == inUse) ? inUse : entries.Count > 0 ? entries[0].Id : null;
            }
        }

        LookEntry Picked() { return lookEntries == null ? null : lookEntries.FirstOrDefault(e => e.Id == lookPicked); }
        static List<object> Nodes(LookEntry e) { return (List<object>)e.Look["nodes"]; }
        LookType TypeOf(Dictionary<string, object> node)
        {
            string key = node.ContainsKey("type") ? node["type"] as string : null;
            return lookTypes == null ? null : lookTypes.FirstOrDefault(t => t.Key == key);
        }

        bool LookInUse(string id) { return SettingText("look_on") == "true" && (SettingText("look") ?? "cinematic") == id; }
        /// <summary>One of Keel's looks with changes being tried (not saved anywhere).</summary>
        bool Changed(LookEntry e) { return e != null && e.Builtin && presetOriginal.ContainsKey(e.Id); }

        List<bool> OpenFlags(LookEntry e)
        {
            List<bool> open;
            if (!lookOpen.TryGetValue(e.Id, out open))
            {
                int n = Nodes(e).Count;
                lookOpen[e.Id] = open = Enumerable.Range(0, n).Select(i => n <= 2).ToList();  // a short look: all open
            }
            while (open.Count < Nodes(e).Count) open.Add(false);
            return open;
        }

        // ---- the page
        void RenderLooks(bool nodes)
        {
            bool live = LooksLive;
            Show(E("LooksLive"), live);
            T("LooksStatus").Text =
                looksError != null ? "Couldn't load the looks: " + Shorten(looksError, 160) + ". Repair Keel, on the Help page, puts back anything missing."
                : !paths.RuntimeReady ? "Finish setting up Keel first."
                : lookEntries == null ? "Loading..."
                : live ? "The video shows the look you pick here, as you change it. Use this look keeps it on after you leave this page."
                : "Effects on top of the picture, applied top to bottom. Press Play to see changes live. While you play, K turns the look on and off and J goes to the next one.";
            Show(B("BtnLookNew"), lookEntries != null);
            Show(B("BtnLookPaste"), lookEntries != null);
            RenderLookList();
            RenderLookHead();
            if (nodes) RenderLookNodes();
            RenderPreviewChrome();
            ShowPreviewPicture();
            RequestPreview();
        }

        void RenderLookList()
        {
            var list = E<StackPanel>("LooksList");
            list.Children.Clear();
            if (lookEntries == null) return;
            // Keel's looks always first, so they stay where they are: the first change to one of them adds a look
            // of yours, and with yours on top that pushed every row down, under the pointer, between two clicks.
            var mine = lookEntries.Where(x => !x.Builtin).ToList();
            FrameworkElement picked = null;
            Action<LookEntry> add = e =>
            {
                var item = LookItem(e);
                if (e.Id == lookPicked) picked = item;
                list.Children.Add(item);
            };
            list.Children.Add(ListHeading("KEEL'S LOOKS", 0));
            lookEntries.Where(x => x.Builtin).ToList().ForEach(add);
            list.Children.Add(ListHeading("YOURS", 14));
            mine.ForEach(add);
            if (mine.Count == 0)
            {
                list.Children.Add(new TextBlock
                {
                    Style = S("Small"), Foreground = R("Muted"), Margin = new Thickness(10, 2, 10, 4),
                    Text = "Change a look and press Save as mine, press New look, or paste a code someone shared.",
                });
            }
            if (picked != null && lookScrolledTo != lookPicked)  // (not on every redraw: you may be scrolling the list)
            {
                lookScrolledTo = lookPicked;
                Window.Dispatcher.BeginInvoke(new Action(() => picked.BringIntoView()), DispatcherPriority.Loaded);
            }
        }

        static TextBlock ListHeading(string text, double top)
        {
            return new TextBlock { Text = text, FontSize = 10.5, FontWeight = FontWeights.SemiBold, Foreground = new SolidColorBrush(C("#6F788A")), Margin = new Thickness(10, top, 0, 6) };
        }

        FrameworkElement LookItem(LookEntry e)
        {
            bool picked = e.Id == lookPicked;
            var g = new Grid();
            g.ColumnDefinitions.Add(new ColumnDefinition());
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            g.Children.Add(new TextBlock
            {
                Text = e.Name, FontSize = 13.5, TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center,
                Foreground = R(picked ? "Text" : "Text2"), FontWeight = picked ? FontWeights.SemiBold : FontWeights.Normal,
            });
            var marks = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            if (Changed(e))
                marks.Children.Add(new TextBlock { Text = "changed", FontSize = 11, FontStyle = FontStyles.Italic, Foreground = R("Muted"), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(8, 0, 0, 0), ToolTip = "Changed here, not saved. Save as mine keeps it." });
            if (LookInUse(e.Id))
                marks.Children.Add(new Ellipse { Width = 7, Height = 7, Fill = R("Good"), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(8, 0, 0, 0), ToolTip = "On now" });
            if (marks.Children.Count > 0)
            {
                Grid.SetColumn(marks, 1);
                g.Children.Add(marks);
            }
            var idle = picked ? Solid("#232A36") : Brushes.Transparent;
            var b = new Border { CornerRadius = new CornerRadius(8), Padding = new Thickness(10, 7, 10, 7), Margin = new Thickness(0, 1, 0, 1), Background = idle, Cursor = Cursors.Hand, Child = g };
            if (!picked)
            {
                b.MouseEnter += (s, ev) => b.Background = R("CardHover");
                b.MouseLeave += (s, ev) => b.Background = idle;
            }
            b.MouseLeftButtonUp += (s, ev) => PickLook(e.Id);
            AutomationProperties.SetName(b, e.Name);
            AutomationProperties.SetAutomationId(b, "Look_" + e.Id);
            return b;
        }

        void PickLook(string id)
        {
            FlushLookSave();
            lookPicked = id;
            lookHidden = false;
            RenderLooks(true);
            PreviewPicked();
            E<ScrollViewer>("LookScroll").ScrollToTop();
        }

        void RenderLookHead()
        {
            var head = E<StackPanel>("LookHead");
            var e = Picked();
            // Not while its name is being typed (a state line can come meanwhile): the next change redraws it.
            if (e != null && head.Tag as string == e.Id && head.IsKeyboardFocusWithin && Keyboard.FocusedElement is TextBox) return;
            head.Children.Clear();
            head.Tag = e == null ? null : e.Id;
            if (e == null) return;
            var g = new Grid();
            g.ColumnDefinitions.Add(new ColumnDefinition());
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var left = new StackPanel { Margin = new Thickness(0, 0, 16, 0) };
            if (e.Builtin)
                left.Children.Add(new TextBlock { Text = e.Name, FontFamily = (FontFamily)Window.FindResource("Display"), FontSize = 21, FontWeight = FontWeights.SemiBold, Foreground = R("Text"), Margin = new Thickness(0, 4, 0, 0) });
            else
            {
                var box = new TextBox { Style = S("Field"), Text = e.Name, Height = 40, FontSize = 16, MaxLength = 60 };
                AutomationProperties.SetName(box, "Look name");
                Action rename = () =>
                {
                    string name = string.Join(" ", box.Text.Split((char[])null, StringSplitOptions.RemoveEmptyEntries));
                    if (name.Length == 0 || name == e.Name) { box.Text = e.Name; return; }
                    e.Name = name;
                    e.Look["name"] = name;
                    SaveLook(e);
                    SortLooks();
                    settingSpecsStale = true;
                    RenderLookList();
                    ShowPreviewPicture();  // (its name, on the preview)
                    PreviewPicked();
                };
                box.LostKeyboardFocus += (s, ev) => rename();
                box.KeyDown += (s, ev) => { if (ev.Key == Key.Enter) { rename(); Keyboard.ClearFocus(); } };
                left.Children.Add(box);
            }
            bool changed = Changed(e);
            left.Children.Add(new TextBlock
            {
                Style = S("Small"), Margin = new Thickness(0, 6, 0, 0), TextWrapping = TextWrapping.Wrap,
                Text = changed ? "Changed here, not saved. Save as mine keeps it as a look of your own; Reset puts Keel's back."
                     : e.Builtin ? "One of Keel's looks. Try any change: Save as mine keeps it as a look of your own."
                     : "Yours. Changes are saved as you make them.",
            });
            g.Children.Add(left);

            var buttons = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 2, 0, 0) };
            if (changed)
            {
                // (no Use this look: what's used is a saved look, so this one is saved first)
                var save = new Button { Style = S("Primary"), Height = 36, Padding = new Thickness(16, 0, 16, 0), Content = "Save as mine",
                                        ToolTip = "Keeps this as a look of your own (My " + e.Name + "), to use, change more and share. " + e.Name + " stays as it was." };
                save.Click += (s, ev) => SaveAsMine(e);
                buttons.Children.Add(save);
                var reset = new Button { Style = S("Ghost"), Height = 36, Margin = new Thickness(8, 0, 0, 0), Content = "Reset", ToolTip = "Puts " + e.Name + " back the way Keel has it." };
                reset.Click += (s, ev) => ResetPreset(e);
                buttons.Children.Add(reset);
            }
            else
            {
                bool inUse = LookInUse(e.Id), locked = SessionRunning && !LooksLive;
                var use = new Button { Style = S(inUse ? "Secondary" : "Primary"), Height = 36, Padding = new Thickness(16, 0, 16, 0), Content = inUse ? "Turn the look off" : "Use this look", IsEnabled = !locked };
                use.ToolTip = locked ? "Keel is starting. Try again once the video is on screen."
                    : inUse ? "Plays without a look. K in the video window turns it back on."
                    : "Turns this look on, now and next time. K in the video window turns it off and on.";
                ToolTipService.SetShowOnDisabled(use, true);
                use.Click += (s, ev) => UseLook(e, !inUse);
                buttons.Children.Add(use);
            }
            var copy = new Button { Style = S("Ghost"), Height = 36, Margin = new Thickness(8, 0, 0, 0), Content = "Copy code", ToolTip = "Copies a short code for this look. Anyone with Keel can paste it on their Looks page to get the same look." };
            copy.Click += (s, ev) => CopyLookCode(e);
            buttons.Children.Add(copy);
            if (!e.Builtin)
            {
                var del = new Button { Style = S("Ghost"), Height = 36, Width = 36, Padding = new Thickness(0), Margin = new Thickness(4, 0, 0, 0), ToolTip = "Delete this look",
                                       Content = new TextBlock { Style = S("Icon"), Text = "", FontSize = 14 } };
                AutomationProperties.SetName(del, "Delete this look");
                del.Click += (s, ev) => DeleteLook(e);
                buttons.Children.Add(del);
            }
            Grid.SetColumn(buttons, 1);
            g.Children.Add(buttons);
            head.Children.Add(g);
        }

        void RenderLookNodes()
        {
            var list = E<StackPanel>("LookNodes");
            list.Children.Clear();
            var e = Picked();
            if (e == null || lookTypes == null) return;
            var nodes = Nodes(e);
            for (int i = 0; i < nodes.Count; i++)
            {
                var card = NodeCard(e, i);
                if (card != null) list.Children.Add(card);
            }
            if (nodes.Count == 0)
                list.Children.Add(new TextBlock { Style = S("Small"), Margin = new Thickness(2, 0, 0, 12), Text = "No effects yet. Add one below." });
            list.Children.Add(AddEffect(e));
        }

        FrameworkElement NodeCard(LookEntry e, int i)
        {
            var node = Nodes(e)[i] as Dictionary<string, object>;
            var t = node == null ? null : TypeOf(node);
            if (t == null) return null;
            bool on = !node.ContainsKey("on") || !(node["on"] is bool) || (bool)node["on"];
            var open = OpenFlags(e);
            var card = new Border { Style = S("Card"), Padding = new Thickness(12, 10, 10, 10), Margin = new Thickness(0, 0, 0, 10), Opacity = on ? 1 : 0.6 };
            var stack = new StackPanel();
            card.Child = stack;

            var g = new Grid();
            foreach (var w in new[] { GridLength.Auto, GridLength.Auto, new GridLength(1, GridUnitType.Star), GridLength.Auto })
                g.ColumnDefinitions.Add(new ColumnDefinition { Width = w });
            var badge = new Border
            {
                Width = 24, Height = 24, CornerRadius = new CornerRadius(7), Background = Solid("#1E222B"), Margin = new Thickness(0, 0, 12, 0), VerticalAlignment = VerticalAlignment.Center,
                Child = new TextBlock { Text = (i + 1).ToString(CultureInfo.InvariantCulture), FontSize = 11.5, FontWeight = FontWeights.SemiBold, Foreground = R("Text2"), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center },
            };
            g.Children.Add(badge);
            var sw = new CheckBox { Style = S("Switch"), IsChecked = on, VerticalAlignment = VerticalAlignment.Center, ToolTip = "This effect on or off" };
            AutomationProperties.SetName(sw, t.Label + " on");
            sw.Checked += (s, ev) => { card.Opacity = 1; SetNodeValue(i, "on", true, false); };
            sw.Unchecked += (s, ev) => { card.Opacity = 0.6; SetNodeValue(i, "on", false, false); };
            Grid.SetColumn(sw, 1);
            g.Children.Add(sw);
            var title = new StackPanel { Orientation = Orientation.Horizontal, Background = Brushes.Transparent, Cursor = Cursors.Hand, Margin = new Thickness(2, 0, 8, 0), VerticalAlignment = VerticalAlignment.Center };
            title.Children.Add(new TextBlock { Text = t.Label, FontSize = 14, FontWeight = FontWeights.SemiBold, Foreground = R("Text"), VerticalAlignment = VerticalAlignment.Center });
            var chev = new TextBlock { Style = S("Icon"), Text = open[i] ? "" : "", FontSize = 10, Foreground = R("Muted"), Margin = new Thickness(10, 2, 0, 0) };
            title.Children.Add(chev);
            title.ToolTip = Tip(t.Help);
            Grid.SetColumn(title, 2);
            g.Children.Add(title);
            var tools = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            tools.Children.Add(IconButton("", "Move up", i > 0, () => MoveNode(e, i, -1)));
            tools.Children.Add(IconButton("", "Move down", i < Nodes(e).Count - 1, () => MoveNode(e, i, 1)));
            tools.Children.Add(IconButton("", "Remove this effect", true, () => RemoveNode(e, i)));
            Grid.SetColumn(tools, 3);
            g.Children.Add(tools);
            stack.Children.Add(g);

            var body = new StackPanel { Margin = new Thickness(36, 10, 4, 2), Visibility = open[i] ? Visibility.Visible : Visibility.Collapsed };
            body.Children.Add(new TextBlock { Style = S("Small"), Foreground = R("Muted"), Text = t.Help, Margin = new Thickness(0, 0, 0, 8) });
            foreach (var p in t.Params) body.Children.Add(ParamRow(e, i, node, p));
            stack.Children.Add(body);
            title.MouseLeftButtonUp += (s, ev) =>
            {
                var flags = OpenFlags(Picked());
                flags[i] = !flags[i];
                body.Visibility = flags[i] ? Visibility.Visible : Visibility.Collapsed;
                chev.Text = flags[i] ? "" : "";
            };
            return card;
        }

        Button IconButton(string glyph, string name, bool enabled, Action click)
        {
            var b = new Button
            {
                Style = S("Ghost"), Width = 30, Height = 30, Padding = new Thickness(0), Margin = new Thickness(2, 0, 0, 0), IsEnabled = enabled, ToolTip = name,
                Content = new TextBlock { Style = S("Icon"), Text = glyph, FontSize = 11 },
            };
            if (!enabled) b.Opacity = 0.35;
            AutomationProperties.SetName(b, name);
            b.Click += (s, ev) => click();
            return b;
        }

        FrameworkElement ParamRow(LookEntry e, int i, Dictionary<string, object> node, LookParam p)
        {
            var g = new Grid { Margin = new Thickness(0, 3, 0, 3) };
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(150) });
            g.ColumnDefinitions.Add(new ColumnDefinition());
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(86) });
            g.Children.Add(new TextBlock { Text = p.Label, FontSize = 12.5, Foreground = R("Text2"), VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis });

            if (p.Text)  // the LUT file
            {
                string current = node.ContainsKey(p.Key) ? node[p.Key] as string ?? "" : "";
                var drop = new ComboBox { Style = S("Drop"), Height = 32 };
                AutomationProperties.SetName(drop, p.Label);
                var names = lookLuts.ToList();
                if (current.Length > 0 && !names.Contains(current)) names.Insert(0, current);
                var none = new ComboBoxItem { Style = S("DropItem"), Content = "Pick a .cube file", IsEnabled = false, Visibility = Visibility.Collapsed };
                drop.Items.Add(none);
                drop.SelectedItem = none;
                foreach (var n in names)
                {
                    var item = new ComboBoxItem { Style = S("DropItem"), Content = n + (lookLuts.Contains(n) ? "" : " (missing)"), Tag = n };
                    drop.Items.Add(item);
                    if (n == current) drop.SelectedItem = item;
                }
                var add = new ComboBoxItem { Style = S("DropItem"), Content = "Add a .cube file...", Tag = "" };
                AutomationProperties.SetHelpText(add, "Copies a LUT made in DaVinci Resolve, Photoshop or another app into Keel's looks folder.");
                drop.Items.Add(add);
                drop.SelectionChanged += (s, ev) =>
                {
                    var item = drop.SelectedItem as ComboBoxItem;
                    if (item == null || item == none) return;
                    if (item == add) Window.Dispatcher.BeginInvoke(new Action(() => AddLut(i)), DispatcherPriority.Background);  // once the list has closed
                    else SetNodeValue(i, p.Key, (string)item.Tag, false);
                };
                Grid.SetColumn(drop, 1);
                Grid.SetColumnSpan(drop, 2);
                g.Children.Add(drop);
                return g;
            }

            double value = node.ContainsKey(p.Key) && node[p.Key] != null && IsNumber(node[p.Key]) ? Num(node[p.Key]) : p.Default;
            if (p.Names.Count > 0)  // a choice
            {
                var bar = new StackPanel { Orientation = Orientation.Horizontal };
                for (int k = 0; k < p.Names.Count; k++)
                {
                    double v = p.Min + k;
                    var rb = new RadioButton { Style = S("Seg"), Content = p.Names[k], GroupName = "look_" + i + "_" + p.Key, IsChecked = Math.Abs(v - value) < 1e-6, Padding = new Thickness(10, 0, 10, 0) };
                    if (k > 0) rb.Margin = new Thickness(2, 0, 0, 0);
                    rb.Checked += (s, ev) => SetNodeValue(i, p.Key, v, false);
                    bar.Children.Add(rb);
                }
                var seg = new Border { Style = S("SegBar"), Child = bar, HorizontalAlignment = HorizontalAlignment.Left };
                Grid.SetColumn(seg, 1);
                Grid.SetColumnSpan(seg, 2);
                g.Children.Add(seg);
                return g;
            }

            var slider = new Slider
            {
                Style = S("Slide"), Minimum = p.Min, Maximum = p.Max, Value = value, SmallChange = p.Step, LargeChange = p.Step * 10,
                TickFrequency = p.Step, IsSnapToTickEnabled = true, IsMoveToPointEnabled = true, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 12, 0),
                ToolTip = "Double-click: back to " + Format(p, p.Default),
            };
            AutomationProperties.SetName(slider, p.Label);
            Grid.SetColumn(slider, 1);
            g.Children.Add(slider);
            var right = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Center };
            Ellipse swatch = null;
            if (p.Unit == "°")
            {
                swatch = new Ellipse { Width = 10, Height = 10, Fill = new SolidColorBrush(Hue(value)), Margin = new Thickness(0, 0, 6, 0), VerticalAlignment = VerticalAlignment.Center };
                right.Children.Add(swatch);
            }
            var text = new TextBlock { Text = Format(p, value), FontSize = 12.5, Foreground = R("Text"), FontFamily = (FontFamily)Window.FindResource("Mono"), VerticalAlignment = VerticalAlignment.Center };
            right.Children.Add(text);
            Grid.SetColumn(right, 2);
            g.Children.Add(right);
            slider.ValueChanged += (s, ev) =>
            {
                double v = Math.Round(p.Min + Math.Round((slider.Value - p.Min) / p.Step) * p.Step, 6);
                text.Text = Format(p, v);
                if (swatch != null) swatch.Fill = new SolidColorBrush(Hue(v));
                SetNodeValue(i, p.Key, v, false);
            };
            slider.MouseDoubleClick += (s, ev) => { slider.Value = p.Default; ev.Handled = true; };
            return g;
        }

        static string Format(LookParam p, double v)
        {
            if (p.Key == "mix") return Math.Round(v * 100).ToString(CultureInfo.InvariantCulture) + "%";
            int places = p.Step >= 1 ? 0 : p.Step >= 0.05 && Math.Abs(p.Step * 10 - Math.Round(p.Step * 10)) < 1e-9 ? 1 : 2;
            string s = v.ToString("F" + places, CultureInfo.InvariantCulture);
            if (p.Min < 0 && v > 0) s = "+" + s;
            if (p.Unit == "°" || p.Unit == ":1") return s + p.Unit;
            return p.Unit.Length > 0 ? s + " " + p.Unit : s;
        }

        static Color Hue(double deg)
        {
            double h = ((deg % 360) + 360) % 360 / 60, x = 1 - Math.Abs(h % 2 - 1);
            double[][] rgb = { new[] { 1, x, 0 }, new[] { x, 1, 0 }, new[] { 0, 1, x }, new[] { 0, x, 1 }, new[] { x, 0, 1 }, new[] { 1, 0, x } };
            var c = rgb[(int)h % 6];
            return Color.FromRgb((byte)(c[0] * 255), (byte)(c[1] * 255), (byte)(c[2] * 255));
        }

        FrameworkElement AddEffect(LookEntry e)
        {
            var drop = new ComboBox { Style = S("Drop"), Width = 240, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 2, 0, 0) };
            AutomationProperties.SetName(drop, "Add an effect");
            var prompt = new ComboBoxItem { Style = S("DropItem"), Content = "+  Add an effect", IsEnabled = false, Visibility = Visibility.Collapsed };
            drop.Items.Add(prompt);
            foreach (var t in lookTypes)
            {
                var item = new ComboBoxItem { Style = S("DropItem"), Content = t.Label, Tag = t };
                AutomationProperties.SetHelpText(item, t.Help);  // DropItem shows it under the name
                drop.Items.Add(item);
            }
            drop.SelectedItem = prompt;
            bool full = Nodes(e).Count >= lookMaxNodes;
            drop.IsEnabled = !full;
            if (full) { drop.ToolTip = "A look can have up to " + lookMaxNodes + " effects."; ToolTipService.SetShowOnDisabled(drop, true); }
            drop.SelectionChanged += (s, ev) =>
            {
                var item = drop.SelectedItem as ComboBoxItem;
                var t = item == null ? null : item.Tag as LookType;
                if (t != null) AddNode(t);
            };
            drop.PreviewMouseWheel += (s, ev) => { if (!drop.IsDropDownOpen) { ev.Handled = true; E<ScrollViewer>("LookScroll").RaiseEvent(new MouseWheelEventArgs(ev.MouseDevice, ev.Timestamp, ev.Delta) { RoutedEvent = UIElement.MouseWheelEvent }); } };
            return drop;
        }

        // ---- changes
        /// <summary>The picked look, ready to change. A change to one of Keel's looks is a try-out: what it was is
        /// kept (Reset), and nothing is written until Save as mine.</summary>
        LookEntry EditablePicked()
        {
            var e = Picked();
            if (e == null || !e.Builtin || presetOriginal.ContainsKey(e.Id)) return e;
            presetOriginal[e.Id] = Json.Serialize(e.Look);  // (before the change: the caller makes it next)
            RenderLookList();
            RenderLookHead();  // Save as mine and Reset; the cards stay as they are
            return e;
        }

        static Dictionary<string, object> CopyOfLook(string json)
        {
            var look = (Dictionary<string, object>)Json.DeserializeObject(json);
            look["nodes"] = ((object[])look["nodes"]).ToList();
            return look;
        }

        /// <summary>Puts one of Keel's looks back the way it was before the changes tried on it.</summary>
        void RestorePreset(LookEntry e)
        {
            string was;
            if (!presetOriginal.TryGetValue(e.Id, out was)) return;
            presetOriginal.Remove(e.Id);
            e.Look = CopyOfLook(was);
        }

        void ResetPreset(LookEntry e)
        {
            RestorePreset(e);
            lookHidden = false;
            RenderLooks(true);
            PreviewPicked();
        }

        /// <summary>The changes tried on one of Keel's looks become a look of yours ("My Cinematic"); Keel's
        /// goes back to how it was.</summary>
        void SaveAsMine(LookEntry e)
        {
            if (!Changed(e)) return;
            var copy = new LookEntry { Name = UniqueLookName("My " + e.Name), Builtin = false, Look = CopyOfLook(Json.Serialize(e.Look)) };
            copy.Look["name"] = copy.Name;
            NewLookFile(copy);
            lookOpen[copy.Id] = OpenFlags(e).ToList();
            RestorePreset(e);
            lookEntries.Add(copy);
            SortLooks();
            lookPicked = copy.Id;
            SaveLook(copy);
            settingSpecsStale = true;
            RenderLooks(true);
            PreviewPicked();
            Toast("Saved as your own look: " + copy.Name, false);
        }

        void SortLooks()
        {
            lookEntries = lookEntries.Where(x => x.Builtin).Concat(lookEntries.Where(x => !x.Builtin).OrderBy(x => x.Name, StringComparer.OrdinalIgnoreCase)).ToList();
        }

        string UniqueLookName(string name)
        {
            string n = name;
            for (int k = 2; lookEntries.Any(x => string.Equals(x.Name, n, StringComparison.OrdinalIgnoreCase)); k++) n = name + " " + k;
            return n;
        }

        /// <summary>A new file (and so an id) for a look of yours: its name in lower case, letters and digits.</summary>
        void NewLookFile(LookEntry e)
        {
            string stem = Regex.Replace(e.Name.ToLowerInvariant(), "[^a-z0-9]+", "-").Trim('-');
            if (stem.Length > 40) stem = stem.Substring(0, 40).Trim('-');
            if (stem.Length == 0) stem = "look";
            string s = stem;
            for (int k = 2; File.Exists(System.IO.Path.Combine(lookFolder, s + ".json")) || lookEntries.Any(x => x.Id == "my:" + s); k++) s = stem + "-" + k;
            e.Id = "my:" + s;
            e.File = System.IO.Path.Combine(lookFolder, s + ".json");
        }

        void SetNodeValue(int i, string key, object value, bool rebuild)
        {
            var e = EditablePicked();
            if (e == null || i >= Nodes(e).Count) return;
            ((Dictionary<string, object>)Nodes(e)[i])[key] = value;
            LookChanged(e, rebuild);
        }

        void LookChanged(LookEntry e, bool rebuild)
        {
            string was;
            if (e.Builtin && presetOriginal.TryGetValue(e.Id, out was) && Json.Serialize(e.Look) == was)
            {
                presetOriginal.Remove(e.Id);  // every change put back: Keel's look again
                RenderLookList();
                RenderLookHead();
            }
            lookSaveDue = e;  // (SaveLook leaves Keel's looks alone)
            lookSaveAt = DateTime.UtcNow.AddMilliseconds(500);
            lookHidden = false;
            if (LooksLive) lookSendDue = true;
            LookTimerOn();
            if (rebuild) RenderLooks(true);
            else RequestPreview();
        }

        void LookTimerOn()
        {
            if (lookTimer == null)
            {
                lookTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(40) };
                lookTimer.Tick += (s, ev) => LookTick();
            }
            lookTimer.Start();
        }

        void LookTick()
        {
            if (lookSendDue) { lookSendDue = false; SendPreview(); }
            if (lookSaveDue != null && DateTime.UtcNow >= lookSaveAt) FlushLookSave();
            if (!lookSendDue && lookSaveDue == null) lookTimer.Stop();
        }

        void FlushLookSave()
        {
            var e = lookSaveDue;
            lookSaveDue = null;
            if (e != null) SaveLook(e);
        }

        void SaveLook(LookEntry e)
        {
            if (e.Builtin || e.File == null) return;
            try
            {
                Directory.CreateDirectory(lookFolder);
                var data = new Dictionary<string, object> { { "version", 1 }, { "name", e.Name }, { "nodes", Nodes(e) } };
                string tmp = e.File + ".part";
                File.WriteAllText(tmp, Json.Serialize(data), new UTF8Encoding(false));  // (Python's json refuses a byte-order mark)
                if (File.Exists(e.File)) File.Replace(tmp, e.File, null);
                else File.Move(tmp, e.File);
            }
            catch (Exception ex) { Toast("Couldn't save the look: " + ex.Message, true); return; }
            if (LooksLive) session.Send("looks-reload");  // the running session reads the new version
        }

        void AddNode(LookType t)
        {
            var node = new Dictionary<string, object> { { "type", t.Key }, { "on", true } };
            foreach (var p in t.Params) node[p.Key] = p.Text ? (object)(lookLuts.FirstOrDefault() ?? "") : p.Default;
            var e = EditablePicked();
            if (e == null) return;
            Nodes(e).Add(node);
            var open = OpenFlags(e);
            while (open.Count < Nodes(e).Count) open.Add(false);
            open[Nodes(e).Count - 1] = true;
            LookChanged(e, true);
            Window.Dispatcher.BeginInvoke(new Action(() => E<ScrollViewer>("LookScroll").ScrollToEnd()), DispatcherPriority.Loaded);
        }

        void MoveNode(LookEntry e0, int i, int by)
        {
            var e = EditablePicked();
            var nodes = Nodes(e);
            int j = i + by;
            if (j < 0 || j >= nodes.Count) return;
            var open = OpenFlags(e);
            var n = nodes[i]; nodes[i] = nodes[j]; nodes[j] = n;
            bool o = open[i]; open[i] = open[j]; open[j] = o;
            LookChanged(e, true);
        }

        void RemoveNode(LookEntry e0, int i)
        {
            var e = EditablePicked();
            if (i >= Nodes(e).Count) return;
            Nodes(e).RemoveAt(i);
            var open = OpenFlags(e);
            if (i < open.Count) open.RemoveAt(i);
            LookChanged(e, true);
        }

        // ---- buttons
        void NewLook()
        {
            if (lookEntries == null || lookTypes == null) return;
            FlushLookSave();
            var e = new LookEntry { Name = UniqueLookName("My look"), Builtin = false, Look = new Dictionary<string, object>() };
            var color = lookTypes.FirstOrDefault(t => t.Key == "color") ?? lookTypes[0];
            var node = new Dictionary<string, object> { { "type", color.Key }, { "on", true } };
            foreach (var p in color.Params) node[p.Key] = p.Text ? (object)"" : p.Default;
            e.Look["name"] = e.Name;
            e.Look["nodes"] = new List<object> { node };
            NewLookFile(e);
            lookOpen[e.Id] = new List<bool> { true };
            lookEntries.Add(e);
            SortLooks();
            SaveLook(e);
            settingSpecsStale = true;
            lookPicked = e.Id;
            RenderLooks(true);
            PreviewPicked();
        }

        void DeleteLook(LookEntry e)
        {
            Ask("", "Bad", "Delete " + e.Name + "?", "It's removed from this PC. Anyone you shared its code with keeps their copy.", "Delete", null, "Cancel", r =>
            {
                if (r != 1) return;
                if (lookSaveDue == e) lookSaveDue = null;
                try { if (File.Exists(e.File)) File.Delete(e.File); }
                catch (Exception ex) { Toast("Couldn't delete it: " + ex.Message, true); return; }
                bool wasUsed = (SettingText("look") ?? "") == e.Id;
                lookEntries.Remove(e);
                lookOpen.Remove(e.Id);
                settingSpecsStale = true;
                if (LooksLive) session.Send("looks-reload");  // (it puts the Look setting back to the default if it was this one)
                else if (wasUsed && !SessionRunning)
                {
                    try { WriteSetting("look", "cinematic"); } catch (Exception) { }
                }
                int at = Math.Max(0, lookEntries.FindIndex(x => !x.Builtin));
                lookPicked = lookEntries.Count > 0 ? lookEntries[Math.Min(at, lookEntries.Count - 1)].Id : null;
                RenderLooks(true);
                PreviewPicked();
            });
        }

        void UseLook(LookEntry e, bool on)
        {
            FlushLookSave();
            if (LooksLive)
            {
                session.Send("looks-reload");  // so it knows a look that's new
                if (on) session.Send("set look " + Json.Serialize(e.Id));
                session.Send("set look_on " + (on ? "true" : "false"));
                // Off: the video goes without a look now, until you pick a look or change something here.
                lookHidden = !on;
                if (!on) { lookSendDue = false; HidePreview(); }
                return;  // its state line confirms it, and the page follows
            }
            if (SessionRunning) { Toast("Keel is starting. Try again once the video is on screen.", true); return; }
            try
            {
                if (on) WriteSetting("look", e.Id);
                WriteSetting("look_on", on);
            }
            catch (Exception ex) { Toast("Couldn't save the setting: " + ex.Message, true); return; }
            RenderLooks(false);
            Toast(on ? e.Name + " is on for your next session. K in the video window turns it off and on." : "Looks are off.", false);
        }

        void CopyLookCode(LookEntry e)
        {
            if (e.Builtin && e.Code != null && !Changed(e)) { CopyCode(e.Code); return; }
            FlushLookSave();
            string file = e.File;
            if (e.Builtin)  // changed here and not saved: the code of what's on screen, from a file of the moment
            {
                try
                {
                    file = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "Keel-look-code.json");
                    var data = new Dictionary<string, object> { { "version", 1 }, { "name", "My " + e.Name }, { "nodes", Nodes(e) } };
                    File.WriteAllText(file, Json.Serialize(data), new UTF8Encoding(false));
                }
                catch (Exception ex) { Toast("Couldn't make its code: " + Shorten(ex.Message, 120), true); return; }
            }
            Task.Run(() =>
            {
                string json = LooksCli("encode \"" + file + "\"");
                OnUi(() =>
                {
                    var r = ParseCli(json);
                    string err = CliError(r);
                    if (err != null) Toast("Couldn't make its code: " + Shorten(err, 120), true);
                    else CopyCode((string)r["code"]);
                });
            });
        }

        void CopyCode(string code)
        {
            try { Clipboard.SetText(code); }
            catch (Exception ex) { Toast("Couldn't copy it: " + ex.Message, true); return; }
            Toast("Copied the look's code. Anyone with Keel can paste it on their Looks page.", false);
        }

        void PasteLookCode()
        {
            if (lookEntries == null) return;
            string text = null;
            try { text = Clipboard.GetText(); } catch (Exception) { }
            // (a code that a chat app wrapped onto more lines is one code; words after it on its line aren't)
            var m = text == null ? Match.Empty : Regex.Match(text, @"SPL\d+-[A-Za-z0-9_\-]+([ \t]*\r?\n\s*[A-Za-z0-9_\-]+)*");
            if (!m.Success) { Toast("Copy a look code first (it starts with SPL1-), then press Paste a code", true); return; }
            string code = Regex.Replace(m.Value, @"\s+", "");
            FlushLookSave();
            Task.Run(() =>
            {
                string json = LooksCli("import " + code);  // (letters, digits, - and _ only: safe as an argument)
                OnUi(() =>
                {
                    var r = ParseCli(json);
                    string err = CliError(r);
                    if (err != null) { Toast(Shorten(err, 160), true); return; }
                    lookPicked = (string)r["id"];
                    settingSpecsStale = true;
                    if (LooksLive) session.Send("looks-reload");
                    LoadLooks(() =>
                    {
                        var e = Picked();
                        if (e != null) Toast("Added " + e.Name + " to your looks", false);
                        PreviewPicked();
                    });
                });
            });
        }

        void AddLut(int i)
        {
            var dlg = new Microsoft.Win32.OpenFileDialog { Filter = "LUT files (*.cube)|*.cube", Title = "Add a LUT file" };
            if (dlg.ShowDialog(Window) != true) { RenderLookNodes(); return; }
            string file = dlg.FileName;
            Task.Run(() =>
            {
                string json = LooksCli("add-lut \"" + file + "\"");
                OnUi(() =>
                {
                    var r = ParseCli(json);
                    string err = CliError(r);
                    if (err != null) { Toast("Can't use that LUT: " + Shorten(err, 140), true); RenderLookNodes(); return; }
                    string name = (string)r["name"];
                    if (!lookLuts.Contains(name)) { lookLuts.Add(name); lookLuts.Sort(StringComparer.OrdinalIgnoreCase); }
                    SetNodeValue(i, "file", name, true);
                });
            });
        }

        // ---- the live preview
        void PreviewPicked()
        {
            if (page != "PageLooks" || !LooksLive || Picked() == null || lookHidden) return;
            lookSendDue = true;
            LookTimerOn();
        }

        void SendPreview()
        {
            var e = Picked();
            if (e == null || !LooksLive || page != "PageLooks" || lookHidden) return;
            if (session.Send("look-show " + Json.Serialize(e.Look))) lookShownTo = session;
        }

        /// <summary>Leaving the page: the video goes back to the Look settings' look.</summary>
        void OnLooksLeave()
        {
            FlushLookSave();
            lookSendDue = false;
            lookHidden = false;
            HidePreview();
        }

        void HidePreview()
        {
            if (lookShownTo != null && lookShownTo == session && session.Running) session.Send("look-hide");
            lookShownTo = null;
        }

        /// <summary>A settings state line, or the session started or ended. The preview goes again each time: the
        /// pipeline may have restarted (a restart keeps the session), and that forgets it.</summary>
        void OnLooksState()
        {
            if (page != "PageLooks") return;
            if (lookShownTo != session) lookShownTo = null;
            RenderLooks(false);
            PreviewPicked();
        }
    }
}
