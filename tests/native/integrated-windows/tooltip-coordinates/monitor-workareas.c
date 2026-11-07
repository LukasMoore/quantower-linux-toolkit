/* SPDX-License-Identifier: MIT
 * Original read-only monitor/workarea companion. No windows or external input. */
#define _WIN32_WINNT 0x0a00
#include <windows.h>
#include <stdio.h>

static unsigned count, failures;

static BOOL CALLBACK inventory(HMONITOR handle, HDC dc, LPRECT bounds, LPARAM parameter)
{
    MONITORINFO info = {0};
    RECT overlap;
    (void)dc; (void)bounds; (void)parameter;
    info.cbSize = sizeof(info);
    if (!GetMonitorInfoW(handle,&info)) { failures++; return TRUE; }
    if (info.rcMonitor.right<=info.rcMonitor.left || info.rcMonitor.bottom<=info.rcMonitor.top ||
        info.rcWork.right<=info.rcWork.left || info.rcWork.bottom<=info.rcWork.top ||
        !IntersectRect(&overlap,&info.rcMonitor,&info.rcWork)) failures++;
    count++;
    printf("{\"kind\":\"active-monitor\",\"units\":\"PMv2-caller-screen\","
           "\"monitor\":[%ld,%ld,%ld,%ld],\"work\":[%ld,%ld,%ld,%ld],\"primary\":%s}\n",
           info.rcMonitor.left,info.rcMonitor.top,info.rcMonitor.right,info.rcMonitor.bottom,
           info.rcWork.left,info.rcWork.top,info.rcWork.right,info.rcWork.bottom,
           info.dwFlags&MONITORINFOF_PRIMARY?"true":"false");
    return TRUE;
}

int main(void)
{
    if (!SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)) return 2;
    if (!EnumDisplayMonitors(NULL,NULL,inventory,0) || !count) failures++;
    printf("{\"kind\":\"monitor-result\",\"dpi\":%u,\"count\":%u,\"failures\":%u}\n",
           GetDpiForSystem(),count,failures);
    return failures?1:0;
}
