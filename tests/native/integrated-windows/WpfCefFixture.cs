// SPDX-License-Identifier: MIT
// Original WPF/CefSharp fixture. Uses own HTML only; never loads vendor UI or hooks.
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using CefSharp;
using CefSharp.Wpf;

internal static class Program
{
    [DllImport("kernel32", CharSet=CharSet.Unicode)] private static extern bool SetDllDirectory(string path);
    [DllImport("user32")] private static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [STAThread] private static int Main(string[] args)
    {
        if (args.Length != 3) throw new ArgumentException("Expected CEF dependency directory, NEW output directory, and unaware|system|pmv2.");
        string native=Path.GetFullPath(args[0]), output=Path.GetFullPath(args[1]);
        if (!Directory.Exists(native) || Directory.Exists(output) || File.Exists(output))
            throw new ArgumentException("Existing dependency directory and new private result directory required.");
        int awareness=args[2]=="unaware"?-1:args[2]=="system"?-2:args[2]=="pmv2"?-4:0;
        if (awareness==0) throw new ArgumentException("Unknown fixture awareness.");
        bool selected=SetProcessDpiAwarenessContext(new IntPtr(awareness));
        AppDomain.CurrentDomain.AssemblyResolve += (_, info) => {
            string file=Path.Combine(native,new AssemblyName(info.Name).Name+".dll");
            return File.Exists(file)?Assembly.LoadFrom(file):null;
        };
        if (!SetDllDirectory(native)) throw new Exception("SetDllDirectory failed for explicit CEF dependencies.");
        return Run(native,output,args[2],selected);
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    private static int Run(string native,string output,string awareness,bool selected)
        => Fixture.Run(native,output,awareness,selected);
}

internal static class Fixture
{
    [DllImport("user32")] private static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32")] private static extern IntPtr GetWindowDpiAwarenessContext(IntPtr hwnd);
    [DllImport("user32")] private static extern int GetAwarenessFromDpiAwarenessContext(IntPtr context);
    [DllImport("user32")] private static extern bool GetWindowRect(IntPtr hwnd,out RectNative rect);
    [StructLayout(LayoutKind.Sequential)] private struct RectNative { public int Left,Top,Right,Bottom; }
    private const string Html="<!doctype html><html><head><meta charset='utf-8'><style>body{margin:0;background:#182433;color:white;font:20px sans-serif}button{position:absolute;left:10px;top:10px;width:240px;height:120px}input{position:absolute;left:10px;top:150px;width:220px;height:40px}canvas{position:absolute;left:280px;top:30px;width:80px;height:80px;image-rendering:pixelated}#count{position:absolute;left:280px;top:140px}</style></head><body><button id='action'>Own CEF action</button><input id='text' value='own input'><canvas id='canvas' width='2' height='2'></canvas><span id='count'>0</span><script>window.actions=0;document.getElementById('action').onclick=()=>{document.getElementById('count').textContent=++window.actions};let c=document.getElementById('canvas').getContext('2d');c.fillStyle='rgb(0,255,0)';c.fillRect(0,0,1,2);c.fillStyle='rgb(255,0,255)';c.fillRect(1,0,1,2);</script></body></html>";

    public static int Run(string native,string output,string requestedAwareness,bool awarenessSelected)
    {
        Directory.CreateDirectory(output);
        CefSharpSettings.ShutdownOnExit=false;
        var settings=new CefSettings {
            BrowserSubprocessPath=Path.Combine(native,"CefSharp.BrowserSubprocess.exe"),
            LocalesDirPath=Path.Combine(native,"locales"), ResourcesDirPath=native,
            RootCachePath=Path.Combine(output,"cache"), LogFile=Path.Combine(output,"cef.log"),
            LogSeverity=LogSeverity.Info, ChromeRuntime=false
        };
        // Keep GPU available. No observer/startup hook, no GPU-disable switches.
        var records=new List<object>();
        string failure=null;
        int completed=0,wpfActions=0;
        if (!Cef.Initialize(settings)) throw new Exception("CEF initialization failed.");
        var app=new Application { ShutdownMode=ShutdownMode.OnExplicitShutdown };
        var stack=new StackPanel { Background=Brushes.Navy };
        var status=new TextBlock { Text="Own WPF action counter: 0",Foreground=Brushes.White,FontSize=20,Margin=new Thickness(16) };
        var button=new Button { Content="Own WPF action", Height=60,Margin=new Thickness(16) };
        button.Click+=(_,__)=>status.Text="Own WPF action counter: "+(++wpfActions);
        stack.Children.Add(status);stack.Children.Add(button);
        var marker=new Border { Width=24,Height=24,Background=Brushes.Lime,HorizontalAlignment=HorizontalAlignment.Left };
        stack.Children.Add(marker);
        var main=new Window { Title="Original integrated WPF fixture",Width=640,Height=300,Content=stack };
        var deadline=new DispatcherTimer { Interval=TimeSpan.FromSeconds(35) };
        deadline.Tick+=(_,__)=>{failure="bounded self-test deadline";app.Shutdown(3);};
        main.Loaded+=async(_,__)=> {
            try {
                await Task.Delay(200);
                records.Add(Measure(main,"wpf-main"));
                var bitmap=new RenderTargetBitmap(24,24,96,96,PixelFormats.Pbgra32);
                var localVisual=new DrawingVisual();
                using(var context=localVisual.RenderOpen())
                    context.DrawRectangle(new VisualBrush(marker),null,new Rect(0,0,24,24));
                bitmap.Render(localVisual);
                byte[] pixels=new byte[24*24*4];bitmap.CopyPixels(pixels,24*4,0);
                int offset=(12*24+12)*4;
                records.Add(new { kind="own-wpf-local-visual-pixel",blue=pixels[offset],green=pixels[offset+1],red=pixels[offset+2],alpha=pixels[offset+3] });
                if (pixels[offset]!=0 || pixels[offset+1]!=255 || pixels[offset+2]!=0 || pixels[offset+3]!=255)
                    throw new Exception("Own WPF visual-tree green marker mismatch.");
                // This verifies the callback itself, not a physical WPF click.
                button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                if (wpfActions!=1) throw new Exception("Own WPF callback counter mismatch.");
                for (int cycle=0;cycle<3;cycle++) {
                    using (var browser=new ChromiumWebBrowser()) {
                        var ready=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                        var loaded=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                        string url="https://integrated-fixture.invalid/cycle/"+cycle;
                        browser.IsBrowserInitializedChanged+=(_,args)=>{if(browser.IsBrowserInitialized)ready.TrySetResult(true);};
                        browser.FrameLoadEnd+=(_,args)=>{if(args.Frame.IsMain && args.Url==url)loaded.TrySetResult(true);};
                        var window=new Window { Owner=main,Title="Original CEF cycle "+cycle,Width=540,Height=350,Content=browser };
                        window.Show();
                        await Within(ready.Task,"browser initialized");
                        browser.LoadHtml(Html,url);
                        await Within(loaded.Task,"own HTML loaded");
                        await Task.Delay(250);
                        records.Add(Measure(window,"cef-cycle-"+cycle));
                        var host=browser.GetBrowser().GetHost();
                        host.SendMouseClickEvent(80,80,MouseButtonType.Left,false,1,CefEventFlags.LeftMouseButton);
                        host.SendMouseClickEvent(80,80,MouseButtonType.Left,true,1,CefEventFlags.None);
                        await Task.Delay(150);
                        var result=await browser.EvaluateScriptAsync("(()=>{let p=document.getElementById('canvas').getContext('2d').getImageData(0,0,2,1).data;return JSON.stringify({actions:window.actions,green:p[0]===0&&p[1]===255&&p[2]===0&&p[3]===255,magenta:p[4]===255&&p[5]===0&&p[6]===255&&p[7]===255,dpr:devicePixelRatio,width:innerWidth,height:innerHeight});})()");
                        if (!result.Success || !(result.Result is string)) throw new Exception("Own DOM readback failed: "+result.Message);
                        using (var json=JsonDocument.Parse((string)result.Result)) {
                            var root=json.RootElement;
                            if (root.GetProperty("actions").GetInt32()!=1 || !root.GetProperty("green").GetBoolean() || !root.GetProperty("magenta").GetBoolean())
                                throw new Exception("Own CEF click/DOM/canvas mismatch: "+result.Result);
                        }
                        records.Add(new { kind="own-cef-action-and-canvas",cycle,dom=(string)result.Result });
                        window.WindowState=WindowState.Minimized;
                        await Task.Delay(100);
                        window.WindowState=WindowState.Normal;
                        await Task.Delay(100);
                        records.Add(Measure(window,"cef-restored-"+cycle));
                        window.Close();
                    }
                    completed++;
                }
                app.Shutdown(0);
            } catch(Exception error) { failure=error.ToString();app.Shutdown(5); }
        };
        deadline.Start();
        int exit=app.Run(main);
        deadline.Stop();
        main.Close();
        Cef.Shutdown();
        var report=new { schema_version=1,requested_awareness=requestedAwareness,awareness_api_selected=awarenessSelected,
            exit_code=exit,completed_cef_reopen_cycles=completed,wpf_programmatic_callback_count=wpfActions,
            failure,records, limits="Own WPF visual-tree/CEF event/DOM checks only; not final-window pixels, physical monitor movement/focus, host taskbar, vendor actions or GPU stability acceptance." };
        File.WriteAllText(Path.Combine(output,"result.json"),JsonSerializer.Serialize(report,new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine("OWN_WPF_CEF_RESULT exit="+exit+" cycles="+completed+" failure="+(failure??"none"));
        return exit==0 && completed==3 && failure==null?0:2;
    }
    private static async Task Within(Task task,string label) {
        if (await Task.WhenAny(task,Task.Delay(4000))!=task) throw new Exception(label+" timeout");
        await task;
    }
    private static object Measure(Window window,string phase) {
        IntPtr hwnd=new WindowInteropHelper(window).Handle;
        if (!GetWindowRect(hwnd,out var rect) || rect.Right<=rect.Left || rect.Bottom<=rect.Top)
            throw new Exception("Invalid own HWND geometry.");
        var source=PresentationSource.FromVisual(window);
        var transform=source.CompositionTarget.TransformToDevice;
        return new { phase,hwnd=hwnd.ToInt64(),awareness=GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(hwnd)),
            dpi=GetDpiForWindow(hwnd),native_rect_units="Win32 caller-context screen units",rect=new { left=rect.Left,top=rect.Top,right=rect.Right,bottom=rect.Bottom },
            wpf_units="DIPs",wpf_width=window.ActualWidth,wpf_height=window.ActualHeight,
            wpf_to_device_x=transform.M11,wpf_to_device_y=transform.M22 };
    }
}
