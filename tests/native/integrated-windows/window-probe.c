/* SPDX-License-Identifier: MIT
 * Original own-window diagnostic. No external-window input or vendor resources. */
#define _WIN32_WINNT 0x0a00
#include <windows.h>
#include <commctrl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures, actions, changes, own_count, monitor_count, primary_count;
static RECT monitors[32], areas[32];
static HWND normal, layered, edit, button, popup, tooltip;
static DWORD started;
static int interactive;
static unsigned position_records, position_records_dropped;
static unsigned focus_records, focus_records_dropped;
static const char *phase = "startup";
static int current_monitor = -1, current_cycle = -1;

static VOID CALLBACK cancel_menu(HWND window, UINT message, UINT_PTR timer, DWORD ticks)
{
    (void)window; (void)message; (void)timer; (void)ticks;
    EndMenu();
}

static void check(int value, const char *name)
{
    if (!value) failures++;
    printf("{\"check\":\"%s\",\"passed\":%s}\n", name, value ? "true" : "false");
    fflush(stdout);
}

static BOOL CALLBACK monitor(HMONITOR handle, HDC dc, LPRECT rect, LPARAM param)
{
    MONITORINFO info = {0};
    RECT intersection;
    (void)dc; (void)rect; (void)param;
    info.cbSize = sizeof(info);
    if (!GetMonitorInfoW(handle, &info)) { failures++; return TRUE; }
    check(info.rcMonitor.right > info.rcMonitor.left && info.rcMonitor.bottom > info.rcMonitor.top &&
          info.rcWork.right > info.rcWork.left && info.rcWork.bottom > info.rcWork.top &&
          IntersectRect(&intersection, &info.rcMonitor, &info.rcWork), "monitor-valid-extents");
    printf("{\"kind\":\"monitor\",\"units\":\"caller-context-Win32\",\"monitor\":[%ld,%ld,%ld,%ld],"
           "\"work\":[%ld,%ld,%ld,%ld],\"primary\":%s}\n",
           info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom,
           info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom,
           info.dwFlags & MONITORINFOF_PRIMARY ? "true" : "false");
    if (monitor_count < 32) { monitors[monitor_count] = info.rcMonitor; areas[monitor_count] = info.rcWork; }
    monitor_count++;
    if (info.dwFlags & MONITORINFOF_PRIMARY) primary_count++;
    return TRUE;
}

static BOOL CALLBACK own_window(HWND window, LPARAM ignored)
{
    DWORD pid;
    (void)ignored;
    GetWindowThreadProcessId(window, &pid);
    if (pid == GetCurrentProcessId() && (window == normal || window == layered)) own_count++;
    return TRUE;
}

static void record_window(HWND window, const char *role)
{
    RECT r, client;
    POINT point = {8, 8}, original = point;
    DPI_AWARENESS_CONTEXT context = GetWindowDpiAwarenessContext(window);
    DWORD pid;
    ULONG_PTR xid = (ULONG_PTR)GetPropW(window,L"__wine_x11_whole_window");
    GetWindowThreadProcessId(window, &pid);
    check(pid == GetCurrentProcessId() && IsWindow(window), "exact-own-window");
    check(GetWindowRect(window, &r) && GetClientRect(window, &client), "window-geometry");
    check(ClientToScreen(window, &point) && ScreenToClient(window, &point) &&
          point.x == original.x && point.y == original.y, "client-screen-roundtrip");
    printf("{\"kind\":\"window\",\"role\":\"%s\",\"pid\":%lu,\"hwnd\":\"%p\","
           "\"awareness\":%d,\"dpi\":%u,\"x11_whole_window\":\"0x%llx\","
           "\"rect\":[%ld,%ld,%ld,%ld],\"client\":[%ld,%ld]}\n",
           role, pid, (void *)window, (int)GetAwarenessFromDpiAwarenessContext(context),
           GetDpiForWindow(window), (unsigned long long)xid,
           r.left,r.top,r.right,r.bottom,client.right,client.bottom);
    fflush(stdout);
}

static RECT record_placement(const char *stage, const RECT *requested)
{
    RECT actual = {0};
    BOOL valid = GetWindowRect(normal,&actual);
    check(valid,"own-placement-geometry");
    printf("{\"kind\":\"placement\",\"stage\":\"%s\",\"tick_ms\":%lu,"
           "\"monitor\":%d,\"units\":\"caller-context-Win32\","
           "\"requested\":[%ld,%ld,%ld,%ld],\"actual\":[%ld,%ld,%ld,%ld]}\n",
           stage,GetTickCount(),current_monitor,
           requested->left,requested->top,requested->right,requested->bottom,
           actual.left,actual.top,actual.right,actual.bottom);
    fflush(stdout);
    return actual;
}

static void record_focus_event(HWND window, const char *role, UINT message, WPARAM wp, LPARAM lp)
{
    const char *name;
    if (message == WM_ACTIVATE) name="WM_ACTIVATE";
    else if (message == WM_SETFOCUS) name="WM_SETFOCUS";
    else if (message == WM_KILLFOCUS) name="WM_KILLFOCUS";
    else return;
    if (focus_records >= 256) { focus_records_dropped++; return; }
    focus_records++;
    printf("{\"kind\":\"focus-event\",\"message\":\"%s\",\"role\":\"%s\",\"phase\":\"%s\","
           "\"sequence\":%u,\"tick_ms\":%lu,\"monitor\":%d,\"cycle\":%d,\"hwnd\":\"%p\","
           "\"wparam\":\"0x%llx\",\"lparam\":\"0x%llx\"}\n",
           name,role,phase,focus_records,GetTickCount(),current_monitor,current_cycle,(void *)window,
           (unsigned long long)wp,(unsigned long long)(ULONG_PTR)lp);
    fflush(stdout);
}

static LRESULT CALLBACK control_subclass(HWND window, UINT message, WPARAM wp, LPARAM lp,
                                        UINT_PTR id, DWORD_PTR label)
{
    record_focus_event(window,(const char *)label,message,wp,lp);
    if (message == WM_NCDESTROY) RemoveWindowSubclass(window,control_subclass,id);
    return DefSubclassProc(window,message,wp,lp);
}

static LRESULT CALLBACK procedure(HWND window, UINT message, WPARAM wp, LPARAM lp)
{
    record_focus_event(window,window==normal?"ordinary":window==layered?"layered":
                       window==popup?"owned-popup":"own-window-being-created",message,wp,lp);
    if (window == layered && message == WM_NCHITTEST) {
        LRESULT hit=DefWindowProcW(window,message,wp,lp);
        POINT point={(SHORT)LOWORD(lp),(SHORT)HIWORD(lp)};
        RECT client;
        /* Preserve the default frame's resize edges/corners. Only our exact
         * layered HWND's visible client header is a custom drag surface. */
        if (hit == HTCLIENT && ScreenToClient(window,&point) && GetClientRect(window,&client) &&
            point.x>=0 && point.x<client.right && point.y>=0 && point.y<36) return HTCAPTION;
        return hit;
    }
    if (window == normal && (message == WM_WINDOWPOSCHANGING || message == WM_WINDOWPOSCHANGED)) {
        const WINDOWPOS *position = (const WINDOWPOS *)lp;
        if (position_records < 128) {
            position_records++;
            printf("{\"kind\":\"windowpos\",\"message\":\"%s\",\"phase\":\"%s\","
                   "\"sequence\":%u,\"tick_ms\":%lu,\"monitor\":%d,\"cycle\":%d,"
                   "\"hwnd\":\"%p\",\"x\":%d,\"y\":%d,\"cx\":%d,\"cy\":%d,\"flags\":%u}\n",
                   message == WM_WINDOWPOSCHANGING ? "changing" : "changed",phase,
                   position_records,GetTickCount(),current_monitor,current_cycle,(void *)window,
                   position->x,position->y,position->cx,position->cy,position->flags);
            fflush(stdout);
        } else position_records_dropped++;
    }
    if (message == WM_COMMAND && LOWORD(wp) == 100 && HIWORD(wp) == BN_CLICKED) {
        WCHAR text[80];
        actions++;
        swprintf(text,80,L"Original fixture action count: %d",actions);
        SetWindowTextW(window,text);
        InvalidateRect(window,NULL,TRUE);
        return 0;
    }
    if (message == WM_DPICHANGED) changes++;
    if (message == WM_PAINT) {
        PAINTSTRUCT paint;
        HDC dc = BeginPaint(window,&paint);
        RECT client;
        GetClientRect(window,&client);
        FillRect(dc,&client,(HBRUSH)(COLOR_WINDOW+1));
        SetTextColor(dc,RGB(0,0,0));
        if (window == layered) {
            RECT header=client;
            header.bottom=min(header.bottom,36);
            FillRect(dc,&header,(HBRUSH)(COLOR_BTNFACE+1));
            TextOutW(dc,12,10,L"Own layered header: drag here; resize at edges",46);
            TextOutW(dc,12,52,L"Original fixture; no trading actions",36);
        } else TextOutW(dc,12,12,L"Own-window fixture. Drag, resize, type; no trading actions.",59);
        EndPaint(window,&paint);
        return 0;
    }
    if (message == WM_CLOSE) {
        if (window == normal) { PostQuitMessage(0); return 0; }
    }
    return DefWindowProcW(window,message,wp,lp);
}

static void pump(unsigned milliseconds)
{
    DWORD until = GetTickCount()+milliseconds;
    MSG message;
    while ((LONG)(until-GetTickCount())>0) {
        while (PeekMessageW(&message,NULL,0,0,PM_REMOVE)) {
            if (message.message == WM_QUIT) return;
            TranslateMessage(&message); DispatchMessageW(&message);
        }
        Sleep(10);
    }
}

int main(int argc, char **argv)
{
    WNDCLASSW type = {0};
    INITCOMMONCONTROLSEX common={sizeof(common),ICC_WIN95_CLASSES};
    DPI_AWARENESS_CONTEXT contexts[] = {DPI_AWARENESS_CONTEXT_UNAWARE,
        DPI_AWARENESS_CONTEXT_SYSTEM_AWARE,DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
    HDESK desktop;
    WCHAR desktop_name[256]; DWORD needed;
    int awareness = argc > 1 ? atoi(argv[1]) : 0;
    interactive = argc > 2 && !strcmp(argv[2],"interactive");
    if (awareness < 0 || awareness > 2) return 2;
    check(SetThreadDpiAwarenessContext(contexts[awareness]) != NULL,"own-thread-context");
    printf("{\"kind\":\"inventory\",\"awareness_variant\":%d,\"pid\":%lu,\"virtual\":[%d,%d,%d,%d]}\n",
           awareness,GetCurrentProcessId(),GetSystemMetrics(SM_XVIRTUALSCREEN),GetSystemMetrics(SM_YVIRTUALSCREEN),
           GetSystemMetrics(SM_CXVIRTUALSCREEN),GetSystemMetrics(SM_CYVIRTUALSCREEN));
    check(EnumDisplayMonitors(NULL,NULL,monitor,0),"monitor-enumeration");
    check(monitor_count > 0 && monitor_count <= 32 && primary_count == 1,"topology-primary");
    check(InitCommonControlsEx(&common),"common-controls-initialized");
    type.lpfnWndProc=procedure; type.hInstance=GetModuleHandleW(NULL);
    type.lpszClassName=L"QtkOriginalIntegratedFixture"; type.hCursor=LoadCursorW(NULL,IDC_ARROW);
    check(RegisterClassW(&type)!=0,"class-registration");
    normal=CreateWindowExW(WS_EX_APPWINDOW,type.lpszClassName,L"Own fixture: ordinary window",WS_OVERLAPPEDWINDOW,
                           CW_USEDEFAULT,CW_USEDEFAULT,680,430,NULL,NULL,type.hInstance,NULL);
    layered=CreateWindowExW(WS_EX_APPWINDOW|WS_EX_LAYERED,type.lpszClassName,L"Own fixture: borderless layered",
                           WS_POPUP|WS_THICKFRAME,100,120,520,330,NULL,NULL,type.hInstance,NULL);
    check(normal && layered,"independent-top-level-created");
    if (!normal || !layered || !monitor_count || monitor_count>32) {
        printf("{\"kind\":\"creation-failed\",\"failures\":%d,\"wayland_loaded\":%s}\n",
               failures,GetModuleHandleW(L"winewayland.drv") ? "true" : "false");
        return 1;
    }
    SetLayeredWindowAttributes(layered,0,245,LWA_ALPHA);
    edit=CreateWindowExW(WS_EX_CLIENTEDGE,L"EDIT",L"Type here",WS_CHILD|WS_VISIBLE|WS_TABSTOP|ES_AUTOHSCROLL,
                         20,70,300,32,normal,(HMENU)101,type.hInstance,NULL);
    button=CreateWindowExW(0,L"BUTTON",L"Count action",WS_CHILD|WS_VISIBLE|WS_TABSTOP|BS_PUSHBUTTON,
                           20,120,180,36,normal,(HMENU)100,type.hInstance,NULL);
    check(SetWindowSubclass(edit,control_subclass,1,(DWORD_PTR)"edit"),"own-edit-focus-observer");
    ShowWindow(normal,SW_SHOW); ShowWindow(layered,SW_SHOW); pump(200);
    desktop=GetThreadDesktop(GetCurrentThreadId());
    check(GetUserObjectInformationW(desktop,UOI_NAME,desktop_name,sizeof(desktop_name),&needed),"caller-owned-desktop-name");
    check(wcscmp(desktop_name,L"AMP-Quantower")!=0,"no-named-virtual-desktop");
    check(EnumDesktopWindows(desktop,own_window,0) && own_count == 2,"desktop-membership");
    record_window(normal,"ordinary"); record_window(layered,"layered");
    if (!interactive) {
        for (int m=0; m<monitor_count; m++) {
            RECT r, requested; int x=areas[m].left+40,y=areas[m].top+50;
            current_monitor=m; current_cycle=-1;
            phase="placement-request";
            SetRect(&requested,x,y,x+640,y+400);
            record_placement("before-request",&requested);
            check(SetWindowPos(normal,NULL,x,y,640,400,SWP_NOACTIVATE|SWP_NOZORDER),"own-placement-request");
            record_placement("immediate",&requested);
            phase="placement-settling";
            pump(100); r=record_placement("after-original-100ms-pump",&requested);
            check(r.left==x && r.top==y && r.right-r.left==640 && r.bottom-r.top==400,"own-placement-exact");
            for (int cycle=0;cycle<3;cycle++) {
                HMENU menu=CreatePopupMenu();
                UINT_PTR timer;
                HWND prior_focus, immediate_focus;
                HWND before_tooltip_focus, before_tooltip_active;
                DWORD focus_error;
                TOOLINFOW tool={0};
                RECT tip_rect={0};
                POINT tip_client={0};
                current_cycle=cycle;
                phase="button-and-minimize";
                SendMessageW(button,BM_CLICK,0,0);
                ShowWindow(normal,SW_MINIMIZE); pump(40);
                check(IsIconic(normal),"own-minimized");
                phase="restore";
                ShowWindow(normal,SW_RESTORE); pump(40);
                check(!IsIconic(normal),"own-restored");
                phase="edit-focus";
                SetLastError(0);
                prior_focus=SetFocus(edit); focus_error=GetLastError(); immediate_focus=GetFocus();
                pump(30);
                printf("{\"kind\":\"focus\",\"monitor\":%d,\"cycle\":%d,\"tick_ms\":%lu,"
                       "\"expected_edit\":\"%p\",\"previous\":\"%p\",\"immediate\":\"%p\","
                       "\"settled\":\"%p\",\"active\":\"%p\",\"foreground\":\"%p\","
                       "\"last_error\":%lu,\"owner_enabled\":%s,\"edit_enabled\":%s}\n",
                       m,cycle,GetTickCount(),(void *)edit,(void *)prior_focus,(void *)immediate_focus,
                       (void *)GetFocus(),(void *)GetActiveWindow(),(void *)GetForegroundWindow(),
                       focus_error,IsWindowEnabled(normal)?"true":"false",IsWindowEnabled(edit)?"true":"false");
                check(GetFocus()==edit,"own-edit-keyboard-focus");
                phase="owned-menu";
                check(AppendMenuW(menu,MF_STRING,200,L"Own menu action"),"menu-item");
                timer=SetTimer(NULL,0,150,cancel_menu);
                check(timer!=0,"bounded-menu-timer");
                if (timer) {
                    TrackPopupMenuEx(menu,TPM_RETURNCMD,x+90,y+90,normal,NULL);
                    KillTimer(NULL,timer);
                }
                DestroyMenu(menu);
                phase="owned-popup";
                popup=CreateWindowExW(WS_EX_TOOLWINDOW,type.lpszClassName,L"Own popup/dialog",
                                      WS_POPUP|WS_CAPTION|WS_VISIBLE,x+40,y+60,300,150,normal,NULL,type.hInstance,NULL);
                check(popup && GetWindow(popup,GW_OWNER)==normal,"popup-owner");
                EnableWindow(normal,FALSE); pump(50);
                check(!IsWindowEnabled(normal),"owned-modal-disable");
                /* Win32 requires enabling the owner before destroying its
                 * modeless dialog, so activation can return to the owner. */
                EnableWindow(normal,TRUE);
                check(IsWindowEnabled(normal),"owned-modal-reenabled");
                DestroyWindow(popup); popup=NULL;
                phase="owned-tooltip";
                before_tooltip_focus=GetFocus(); before_tooltip_active=GetActiveWindow();
                tooltip=CreateWindowExW(WS_EX_TOPMOST|WS_EX_NOACTIVATE,TOOLTIPS_CLASSW,NULL,
                                        WS_POPUP|TTS_NOPREFIX|TTS_ALWAYSTIP,
                                        CW_USEDEFAULT,CW_USEDEFAULT,CW_USEDEFAULT,CW_USEDEFAULT,
                                        normal,NULL,type.hInstance,NULL);
                check(tooltip && GetWindow(tooltip,GW_OWNER)==normal,"tooltip-owner");
                check(SetWindowSubclass(tooltip,control_subclass,2,(DWORD_PTR)"tooltip"),"own-tooltip-focus-observer");
                tool.cbSize=sizeof(tool); tool.uFlags=TTF_TRACK|TTF_ABSOLUTE;
                tool.hwnd=normal; tool.uId=1; tool.hinst=type.hInstance; tool.lpszText=L"Own tracking tooltip";
                GetClientRect(normal,&tool.rect);
                check(SendMessageW(tooltip,TTM_ADDTOOLW,0,(LPARAM)&tool)!=0,"tooltip-tool-added");
                SendMessageW(tooltip,TTM_TRACKPOSITION,0,MAKELPARAM(x+60,y+70));
                SendMessageW(tooltip,TTM_TRACKACTIVATE,TRUE,(LPARAM)&tool);
                pump(50);
                check(IsWindowVisible(tooltip) && GetWindowRect(tooltip,&tip_rect),"tooltip-visible-geometry");
                check(tip_rect.left>=areas[m].left && tip_rect.top>=areas[m].top &&
                      tip_rect.right<=areas[m].right && tip_rect.bottom<=areas[m].bottom &&
                      tip_rect.right>tip_rect.left && tip_rect.bottom>tip_rect.top,
                      "tooltip-within-target-workarea");
                check(MonitorFromWindow(tooltip,MONITOR_DEFAULTTONULL)==
                      MonitorFromWindow(normal,MONITOR_DEFAULTTONULL),"tooltip-target-monitor");
                check(GetFocus()==before_tooltip_focus && GetActiveWindow()==before_tooltip_active,
                      "tooltip-no-activation");
                check(ClientToScreen(tooltip,&tip_client),"tooltip-client-screen");
                printf("{\"kind\":\"tooltip\",\"monitor\":%d,\"cycle\":%d,"
                       "\"units\":\"caller-context-Win32\",\"requested_absolute\":[%d,%d],"
                       "\"window_rect\":[%ld,%ld,%ld,%ld],\"client_origin\":[%ld,%ld],"
                       "\"x11_whole_window\":\"0x%llx\"}\n",m,cycle,x+60,y+70,
                       tip_rect.left,tip_rect.top,tip_rect.right,tip_rect.bottom,tip_client.x,tip_client.y,
                       (unsigned long long)(ULONG_PTR)GetPropW(tooltip,L"__wine_x11_whole_window"));
                SendMessageW(tooltip,TTM_TRACKACTIVATE,FALSE,(LPARAM)&tool);
                DestroyWindow(tooltip); tooltip=NULL;
            }
            phase="after-placement-cycles";
            record_placement("after-lifecycle-cycles",&requested);
            record_window(normal,"after-placement");
        }
        check(actions==3*monitor_count,"button-action-callbacks");
    } else {
        MSG message;
        started=GetTickCount();
        while (GetTickCount()-started < 180000) {
            if (PeekMessageW(&message,NULL,0,0,PM_REMOVE)) {
                if (message.message==WM_QUIT) break;
                if (!IsDialogMessageW(normal,&message)) {TranslateMessage(&message);DispatchMessageW(&message);}
            } else Sleep(10);
        }
        record_window(normal,"after-physical-session"); record_window(layered,"after-physical-session-layered");
    }
    phase="shutdown";
    DestroyWindow(layered); DestroyWindow(normal);
    printf("{\"kind\":\"result\",\"failures\":%d,\"actions\":%d,\"dpi_changes\":%d,\"monitor_count\":%d,"
           "\"windowpos_records\":%u,\"windowpos_records_dropped\":%u,"
           "\"focus_records\":%u,\"focus_records_dropped\":%u,"
           "\"physical_actions_asserted\":false}\n",failures,actions,changes,monitor_count,
           position_records,position_records_dropped,focus_records,focus_records_dropped);
    return failures ? 1 : 0;
}
