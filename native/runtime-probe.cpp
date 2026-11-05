// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Quantower Linux Toolkit contributors.
// Synthetic Win32/GDI+/Uniscribe probe. No app, account or market data access.
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <gdiplus.h>
#include <objbase.h>
#include <usp10.h>
#include <stdio.h>
#include <stdint.h>
#include <wchar.h>

using namespace Gdiplus;
using namespace Gdiplus::DllExports;

struct Worker {
    HANDLE start;
    volatile LONG *ready;
    unsigned analysed, shaped, shape_font_missing, placed, errors;
    HRESULT first_error;
};
static void fail(Worker *w, HRESULT hr) { ++w->errors; if (!w->first_error) w->first_error = hr; }
static const wchar_t *samples[] = {
    L"السلام عليكم", L"אבגדה וזחט", L"مرحبا 123 ABC", L"שלום 42 עולם"
};
static DWORD WINAPI shape_worker(void *value) {
    Worker *w = (Worker *)value;
    HRESULT apartment = CoInitializeEx(NULL, COINIT_MULTITHREADED);
    if (FAILED(apartment)) { fail(w, apartment); InterlockedIncrement(w->ready); return 1; }
    HDC dc = CreateCompatibleDC(NULL);
    if (!dc) { fail(w, E_FAIL); InterlockedIncrement(w->ready); CoUninitialize(); return 1; }
    InterlockedIncrement(w->ready);
    if (WaitForSingleObject(w->start, 5000) != WAIT_OBJECT_0) { DeleteDC(dc); fail(w, E_FAIL); CoUninitialize(); return 2; }
    for (unsigned iteration = 0; iteration < 128; ++iteration) {
        HFONT font = CreateFontW(-18 - (int)(iteration % 4), 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                               DEFAULT_CHARSET, OUT_TT_ONLY_PRECIS, CLIP_DEFAULT_PRECIS,
                               ANTIALIASED_QUALITY, DEFAULT_PITCH, L"DejaVu Sans");
        if (!font) { fail(w, E_FAIL); break; }
        HGDIOBJ old = SelectObject(dc, font);
        if (!old || old == HGDI_ERROR) { DeleteObject(font); fail(w, E_FAIL); break; }
        for (unsigned sample = 0; sample < sizeof(samples) / sizeof(samples[0]); ++sample) {
            const wchar_t *text = samples[sample];
            int length = (int)wcslen(text);
            SCRIPT_STRING_ANALYSIS analysis = NULL;
            HRESULT hr = ScriptStringAnalyse(dc, text, length, 256, -1,
                SSA_GLYPHS | SSA_FALLBACK | SSA_LINK, 0, NULL, NULL, NULL, NULL, NULL, &analysis);
            if (SUCCEEDED(hr)) {
                const SIZE *size = ScriptString_pSize(analysis);
                if (size && size->cx > 0 && size->cy > 0) ++w->analysed;
                else fail(w, E_FAIL);
                if (FAILED(ScriptStringOut(analysis, 0, 0, 0, NULL, 0, 0, FALSE))) fail(w, E_FAIL);
                ScriptStringFree(&analysis);
            } else fail(w, hr);
            SCRIPT_ITEM items[32]; int count = 0;
            hr = ScriptItemize(text, length, 31, NULL, NULL, items, &count);
            if (FAILED(hr)) { fail(w, hr); continue; }
            for (int item = 0; item < count; ++item) {
                SCRIPT_CACHE cache = NULL;
                WORD glyphs[256], clusters[128]; SCRIPT_VISATTR attributes[256]; int glyph_count = 0;
                int run_length = items[item + 1].iCharPos - items[item].iCharPos;
                hr = ScriptShape(dc, &cache, text + items[item].iCharPos, run_length,
                                 256, &items[item].a, glyphs, clusters, attributes, &glyph_count);
                if (hr == USP_E_SCRIPT_NOT_IN_FONT) ++w->shape_font_missing;
                else if (SUCCEEDED(hr) && glyph_count > 0) {
                    ++w->shaped;
                    int advances[256]; GOFFSET offsets[256]; ABC abc;
                    hr = ScriptPlace(dc, &cache, glyphs, glyph_count, attributes,
                                     &items[item].a, advances, offsets, &abc);
                    if (SUCCEEDED(hr)) ++w->placed; else fail(w, hr);
                } else fail(w, hr);
                ScriptFreeCache(&cache);
            }
        }
        SelectObject(dc, old); DeleteObject(font);
    }
    DeleteDC(dc);
    CoUninitialize();
    return w->errors ? 3 : 0;
}

struct DrawResult { bool okay; unsigned changed_pixels; uint64_t checksum; float width, height; };
static DrawResult draw_probe() {
    DrawResult result = {};
    GpBitmap *bitmap = NULL; GpGraphics *graphics = NULL; GpFontFamily *family = NULL;
    GpFont *font = NULL; GpSolidFill *brush = NULL;
    const ARGB background = 0xff101010;
    if (GdipCreateBitmapFromScan0(320, 96, 0, PixelFormat32bppARGB, NULL, &bitmap) != Ok) return result;
    if (GdipGetImageGraphicsContext((GpImage *)bitmap, &graphics) != Ok) goto done;
    if (GdipGetGenericFontFamilySansSerif(&family) != Ok) goto done;
    if (GdipCreateFont(family, 18.0f, FontStyleRegular, UnitPixel, &font) != Ok) goto done;
    if (GdipCreateSolidFill(0xfff0f0f0, &brush) != Ok) goto done;
    if (GdipGraphicsClear(graphics, background) != Ok) goto done;
    {
        const wchar_t *text = L"Runtime 123 אבג العربية";
        RectF box(4, 4, 312, 88), bounds; INT fitted = 0, lines = 0;
        if (GdipMeasureString(graphics, text, -1, font, &box, NULL, &bounds, &fitted, &lines) != Ok ||
            bounds.Width <= 0 || bounds.Height <= 0 || fitted <= 0 || lines <= 0) goto done;
        result.width = bounds.Width; result.height = bounds.Height;
        if (GdipDrawString(graphics, text, -1, font, &box, NULL, (GpBrush *)brush) != Ok) goto done;
        GdipDeleteGraphics(graphics); graphics = NULL;
        Rect rect(0, 0, 320, 96); BitmapData data = {};
        if (GdipBitmapLockBits(bitmap, &rect, ImageLockModeRead, PixelFormat32bppARGB, &data) != Ok) goto done;
        uint64_t checksum = 1469598103934665603ULL;
        for (int y = 0; y < 96; ++y) {
            const ARGB *row = (const ARGB *)((const BYTE *)data.Scan0 + y * data.Stride);
            for (int x = 0; x < 320; ++x) {
                if ((row[x] & 0x00ffffff) != (background & 0x00ffffff)) ++result.changed_pixels;
                checksum ^= row[x]; checksum *= 1099511628211ULL;
            }
        }
        GdipBitmapUnlockBits(bitmap, &data);
        result.checksum = checksum; result.okay = result.changed_pixels > 20;
    }
done:
    if (graphics) GdipDeleteGraphics(graphics);
    if (brush) GdipDeleteBrush((GpBrush *)brush);
    if (font) GdipDeleteFont(font);
    if (family) GdipDeleteFontFamily(family);
    if (bitmap) GdipDisposeImage((GpImage *)bitmap);
    return result;
}

static bool pump_until(bool (*predicate)(HWND), HWND window, DWORD timeout) {
    ULONGLONG deadline = GetTickCount64() + timeout;
    do {
        MSG message;
        while (PeekMessageW(&message, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&message); DispatchMessageW(&message); }
        if (predicate && predicate(window)) return true;
        Sleep(10);
    } while (GetTickCount64() < deadline);
    return predicate ? predicate(window) : true;
}
static LRESULT CALLBACK fixture_window(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcW(h,m,w,l); }
static bool iconic(HWND h) { return IsIconic(h) != FALSE; }
static bool restored(HWND h) { return IsIconic(h) == FALSE; }
struct TrayLookup { HWND target, tray, button; DWORD explorer_pid; };
static BOOL CALLBACK find_button(HWND h, LPARAM value) {
    TrayLookup *result = (TrayLookup *)value; wchar_t cls[64]; GetClassNameW(h, cls, 64);
    if (GetParent(h) == result->tray && _wcsicmp(cls,L"Button") == 0 && IsWindowVisible(h) &&
        (HWND)GetWindowLongPtrW(h, GWLP_ID) == result->target) {
        DWORD target_pid = 0, button_pid = 0; GetWindowThreadProcessId(result->target,&target_pid); GetWindowThreadProcessId(h,&button_pid);
        if (target_pid == GetCurrentProcessId() && button_pid == result->explorer_pid) result->button = h;
    }
    return TRUE;
}
static BOOL CALLBACK find_tray(HWND h, LPARAM value) {
    TrayLookup *result = (TrayLookup *)value; wchar_t cls[64]; GetClassNameW(h,cls,64);
    if (wcscmp(cls,L"Shell_TrayWnd") != 0 || !IsWindowVisible(h)) return TRUE;
    DWORD pid = 0; GetWindowThreadProcessId(h,&pid);
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,FALSE,pid);
    if (!process) return TRUE;
    wchar_t path[2048]; DWORD count = 2048;
    bool verified = WaitForSingleObject(process,0) == WAIT_TIMEOUT && QueryFullProcessImageNameW(process,0,path,&count);
    if (verified) { wchar_t *leaf = wcsrchr(path,L'\\'); verified = leaf && _wcsicmp(leaf+1,L"explorer.exe")==0; }
    CloseHandle(process);
    if (!verified) return TRUE;
    result->tray=h; result->explorer_pid=pid;
    EnumChildWindows(h,find_button,value);
    return result->button ? FALSE : TRUE;
}
struct TaskbarResult { bool attempted, available, topmost, restored, bounds_preserved; unsigned tray_hit_points; };
static TaskbarResult taskbar_probe() {
    TaskbarResult result = {}; result.attempted=true;
    HINSTANCE instance = GetModuleHandleW(NULL); WNDCLASSW cls = {};
    cls.lpfnWndProc=fixture_window; cls.hInstance=instance; cls.lpszClassName=L"ToolkitRuntimeProbe";
    if (!RegisterClassW(&cls) && GetLastError()!=ERROR_CLASS_ALREADY_EXISTS) return result;
    HWND owner=CreateWindowExW(0,cls.lpszClassName,L"Runtime probe owner",WS_OVERLAPPEDWINDOW,
                              20,20,300,180,NULL,NULL,instance,NULL);
    HWND app=CreateWindowExW(WS_EX_APPWINDOW,cls.lpszClassName,L"Runtime probe panel",WS_OVERLAPPEDWINDOW,
                            0,0,GetSystemMetrics(SM_CXSCREEN),GetSystemMetrics(SM_CYSCREEN),owner,NULL,instance,NULL);
    if (!owner || !app) goto done;
    ShowWindow(owner,SW_SHOWNOACTIVATE); ShowWindow(app,SW_SHOWNOACTIVATE);
    SetWindowPos(app,HWND_TOP,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE);
    pump_until(NULL,NULL,1200);
    {
        TrayLookup lookup = {}; lookup.target=app;
        EnumWindows(find_tray,(LPARAM)&lookup);
        if (!lookup.tray || !lookup.button) goto done;
        result.available=true; result.topmost=(GetWindowLongPtrW(lookup.tray,GWL_EXSTYLE)&WS_EX_TOPMOST)!=0;
        RECT tray, before, after; if (!GetWindowRect(lookup.tray,&tray) || !GetWindowRect(app,&before)) goto done;
        int positions[5]={tray.left+5,tray.left+(tray.right-tray.left)/4,tray.left+(tray.right-tray.left)/2,
                          tray.left+(tray.right-tray.left)*3/4,tray.right-6};
        for (unsigned n=0;n<5;++n) { POINT point={positions[n],tray.top+(tray.bottom-tray.top)/2};
            if(GetAncestor(WindowFromPoint(point),GA_ROOT)==lookup.tray)++result.tray_hit_points; }
        ShowWindow(app,SW_MINIMIZE); if (!pump_until(iconic,app,2000)) goto done;
        pump_until(NULL,NULL,250);
        // Re-enumerate the linked button immediately before sending its own restore.
        lookup.button=NULL; EnumChildWindows(lookup.tray,find_button,(LPARAM)&lookup);
        DWORD target_pid=0; GetWindowThreadProcessId(app,&target_pid);
        if(!lookup.button || target_pid!=GetCurrentProcessId())goto done;
        DWORD_PTR ignored=0;
        if(!SendMessageTimeoutW(lookup.button,BM_CLICK,0,0,SMTO_ABORTIFHUNG,2000,&ignored))goto done;
        result.restored=pump_until(restored,app,2000);
        if(GetWindowRect(app,&after))result.bounds_preserved=EqualRect(&before,&after)!=FALSE;
    }
done:
    if(app)DestroyWindow(app);
    if(owner)DestroyWindow(owner);
    return result;
}

int wmain(int argc, wchar_t **argv) {
    bool taskbar=false; const wchar_t *desktop_name=NULL;
    for(int n=1;n<argc;++n) {
        if(wcscmp(argv[n],L"--taskbar")==0)taskbar=true;
        else if(wcscmp(argv[n],L"--desktop")==0 && ++n<argc && wcscmp(argv[n],L"AMP-Quantower")==0)desktop_name=argv[n];
        else { printf("{\"error\":\"usage: runtime-probe [--desktop AMP-Quantower] [--taskbar]\"}\n"); return 2; }
    }
    HDESK original_desktop=GetThreadDesktop(GetCurrentThreadId()), desktop=NULL;
    if(desktop_name) {
        desktop=OpenDesktopW(desktop_name,0,FALSE,DESKTOP_READOBJECTS|DESKTOP_CREATEWINDOW|DESKTOP_ENUMERATE|DESKTOP_WRITEOBJECTS);
        if(!desktop || !SetThreadDesktop(desktop)) { printf("{\"error\":\"named_desktop_unavailable\"}\n");return 3; }
    }
    SetProcessDPIAware();
    HRESULT apartment=CoInitializeEx(NULL,COINIT_MULTITHREADED);
    if(FAILED(apartment)) { printf("{\"error\":\"com_startup\"}\n");return 4; }
    DWORD before=GetGuiResources(GetCurrentProcess(),GR_GDIOBJECTS);
    GdiplusStartupInput startup; ULONG_PTR token=0;
    if(GdiplusStartup(&token,&startup,NULL)!=Ok) { printf("{\"error\":\"gdiplus_startup\"}\n");return 4; }
    DrawResult drawing=draw_probe();
    HANDLE start=CreateEventW(NULL,TRUE,FALSE,NULL), threads[4]={}; volatile LONG ready=0; Worker workers[4]={};
    bool complete=start!=NULL;
    for(unsigned n=0;n<4 && complete;++n) { workers[n].start=start;workers[n].ready=&ready;
        threads[n]=CreateThread(NULL,0,shape_worker,&workers[n],0,NULL);complete=threads[n]!=NULL; }
    ULONGLONG deadline=GetTickCount64()+5000;
    while(complete && ready<4 && GetTickCount64()<deadline)Sleep(5);
    if(start)SetEvent(start);
    if(complete)complete=WaitForMultipleObjects(4,threads,TRUE,15000)==WAIT_OBJECT_0;
    if(!complete) { printf("{\"error\":\"worker_setup_or_deadline\"}\n");fflush(stdout);ExitProcess(5); }
    unsigned analysed=0,shaped=0,missing=0,placed=0,errors=0;HRESULT first_error=0;
    for(unsigned n=0;n<4;++n) { analysed+=workers[n].analysed;shaped+=workers[n].shaped;
        missing+=workers[n].shape_font_missing;placed+=workers[n].placed;errors+=workers[n].errors;
        if(!first_error)first_error=workers[n].first_error;
        CloseHandle(threads[n]); }
    CloseHandle(start); GdiplusShutdown(token);
    DWORD after=GetGuiResources(GetCurrentProcess(),GR_GDIOBJECTS);
    TaskbarResult tray={}; if(taskbar)tray=taskbar_probe();
    bool taskbar_ok=!taskbar || (tray.available && tray.topmost && tray.restored && tray.bounds_preserved && tray.tray_hit_points==5);
    bool okay=drawing.okay && analysed==2048 && shaped>0 && shaped==placed && errors==0 && taskbar_ok;
    printf("{\"schema_version\":1,\"synthetic_only\":true,\"architecture_bits\":%u,\"passed\":%s,"
           "\"drawing\":{\"passed\":%s,\"changed_pixels\":%u,\"checksum\":\"%016llx\",\"width\":%.3f,\"height\":%.3f},"
           "\"uniscribe\":{\"workers\":4,\"analysed\":%u,\"shaped\":%u,\"font_missing_runs\":%u,\"placed\":%u,\"errors\":%u,\"first_hresult\":\"0x%08lx\"},"
           "\"gdi_resources\":{\"before\":%lu,\"after\":%lu,\"api_reported_nonzero\":%s},"
           "\"taskbar\":{\"attempted\":%s,\"available\":%s,\"topmost\":%s,\"tray_hit_points\":%u,\"restored\":%s,\"bounds_preserved\":%s}}\n",
           (unsigned)(8*sizeof(void*)),okay?"true":"false",drawing.okay?"true":"false",drawing.changed_pixels,
           (unsigned long long)drawing.checksum,drawing.width,drawing.height,analysed,shaped,missing,placed,errors,(unsigned long)first_error,
           (unsigned long)before,(unsigned long)after,(before||after)?"true":"false",taskbar?"true":"false",tray.available?"true":"false",
           tray.topmost?"true":"false",tray.tray_hit_points,tray.restored?"true":"false",tray.bounds_preserved?"true":"false");
    CoUninitialize();
    if(desktop) { SetThreadDesktop(original_desktop); CloseDesktop(desktop); }
    return okay?0:1;
}
