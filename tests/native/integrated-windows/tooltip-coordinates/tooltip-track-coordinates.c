/* SPDX-License-Identifier: MIT
 * Original own-tooltip signed screen-coordinate regression. No external input. */
#define _WIN32_WINNT 0x0a00
#include <windows.h>
#include <commctrl.h>
#include <stdio.h>

static void pump(void)
{
    MSG message;
    DWORD until = GetTickCount() + 100;
    while ((LONG)(until - GetTickCount()) > 0) {
        while (PeekMessageW(&message, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        Sleep(5);
    }
}

int main(void)
{
    static const POINT requests[] = {{200, 200}, {400, 300}, {0, 100},
        {-100, 100}, {-500, 100}, {100, -100}, {100, -500}, {-100, -100}, {-500, -500}};
    INITCOMMONCONTROLSEX init = {sizeof(init), ICC_WIN95_CLASSES};
    TTTOOLINFOW tool = {0};
    MONITORINFO monitor = {0};
    HWND owner, tooltip;
    unsigned index;
    int failures = 0, cases = 0;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    if (!InitCommonControlsEx(&init)) return 2;
    owner = CreateWindowExW(WS_EX_NOACTIVATE, L"STATIC", L"Original signed tooltip fixture", WS_OVERLAPPEDWINDOW,
        50, 50, 500, 300, NULL, NULL, GetModuleHandleW(NULL), NULL);
    tooltip = CreateWindowExW(WS_EX_TOPMOST | WS_EX_NOACTIVATE, TOOLTIPS_CLASSW, NULL,
        WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX, CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT,
        owner, NULL, GetModuleHandleW(NULL), NULL);
    if (!owner || !tooltip) return 3;
    ShowWindow(owner, SW_SHOWNOACTIVATE);
    tool.cbSize = sizeof(tool); tool.uFlags = TTF_TRACK | TTF_ABSOLUTE;
    tool.hwnd = owner; tool.uId = 1; tool.lpszText = L"Original own tooltip";
    GetClientRect(owner, &tool.rect);
    if (!SendMessageW(tooltip, TTM_ADDTOOLW, 0, (LPARAM)&tool)) return 4;
    monitor.cbSize = sizeof(monitor);
    if (!GetMonitorInfoW(MonitorFromWindow(owner, MONITOR_DEFAULTTOPRIMARY), &monitor)) return 5;
    printf("{\"kind\":\"inventory\",\"units\":\"PMv2-caller-screen\",\"dpi\":%u,\"work\":[%ld,%ld,%ld,%ld]}\n",
        GetDpiForWindow(owner), monitor.rcWork.left, monitor.rcWork.top, monitor.rcWork.right, monitor.rcWork.bottom);
    for (index = 0; index < sizeof(requests) / sizeof(requests[0]); index++) {
        POINT actual = {0, 0};
        RECT rect;
        LONG expected_x = requests[index].x < monitor.rcWork.left ? monitor.rcWork.left : requests[index].x;
        int passed;
        SendMessageW(tooltip, TTM_TRACKACTIVATE, FALSE, (LPARAM)&tool);
        SendMessageW(tooltip, TTM_TRACKPOSITION, 0, MAKELPARAM(requests[index].x, requests[index].y));
        SendMessageW(tooltip, TTM_TRACKACTIVATE, TRUE, (LPARAM)&tool);
        pump();
        passed = GetWindowRect(tooltip, &rect) && ClientToScreen(tooltip, &actual) && IsWindowVisible(tooltip) &&
            actual.x == expected_x && actual.y == requests[index].y;
        if (!passed) failures++;
        cases++;
        printf("{\"kind\":\"case\",\"request\":[%ld,%ld],\"expected_client_origin\":[%ld,%ld],"
               "\"actual_client_origin\":[%ld,%ld],\"window_rect\":[%ld,%ld,%ld,%ld],\"passed\":%s}\n",
            requests[index].x, requests[index].y, expected_x, requests[index].y, actual.x, actual.y,
            rect.left, rect.top, rect.right, rect.bottom, passed ? "true" : "false");
        fflush(stdout);
    }
    SendMessageW(tooltip, TTM_TRACKACTIVATE, FALSE, (LPARAM)&tool);
    DestroyWindow(tooltip); DestroyWindow(owner);
    printf("{\"kind\":\"result\",\"cases\":%d,\"failures\":%d,\"host_ui_test\":false}\n", cases, failures);
    return failures ? 1 : 0;
}
