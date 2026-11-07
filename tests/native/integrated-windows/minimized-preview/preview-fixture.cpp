// SPDX-License-Identifier: MIT
// Original disposable GDI preview-preservation fixture. No foreign window access.
#define _WIN32_WINNT 0x0a00
#include <windows.h>
#include <cstdio>
#include <string>

static std::wstring directory;
static void pump(unsigned duration) {
 ULONGLONG until=GetTickCount64()+duration;
 do {MSG m;while(PeekMessageW(&m,NULL,0,0,PM_REMOVE)){TranslateMessage(&m);DispatchMessageW(&m);}Sleep(5);}while(GetTickCount64()<until);
}
static LRESULT CALLBACK procedure(HWND hwnd,UINT message,WPARAM w,LPARAM l) {
 if(message==WM_NCCALCSIZE)return 0;
 if(message==WM_PAINT) {
  PAINTSTRUCT ps;HDC dc=BeginPaint(hwnd,&ps);RECT r;GetClientRect(hwnd,&r);
  HBRUSH bg=CreateSolidBrush(IsIconic(hwnd)?RGB(255,255,255):RGB(18,24,32));FillRect(dc,&r,bg);DeleteObject(bg);
  if(!IsIconic(hwnd)) {
   for(int n=0;n<24;n++) {
    int x=18+n*28,y=80+(n*37)%220;
    HBRUSH b=CreateSolidBrush(n%2?RGB(255,92,112):RGB(84,210,174));
    RECT candle={x,y,x+12,y+38+(n*11)%60};FillRect(dc,&candle,b);DeleteObject(b);
   }
   SetBkMode(dc,TRANSPARENT);SetTextColor(dc,RGB(230,230,230));
   TextOutW(dc,20,18,L"Original GDI chart preview fixture",34);
  }
  EndPaint(hwnd,&ps);return 0;
 }
 return DefWindowProcW(hwnd,message,w,l);
}
static bool checkpoint(HWND hwnd,const char *phase) {
 pump(600);GdiFlush();
 std::wstring name=directory+L"\\state.json";
 FILE *f=_wfopen((name+L".tmp").c_str(),L"wb");if(!f)return false;
 RECT rect;GetWindowRect(hwnd,&rect);
 std::fprintf(f,"{\"phase\":\"%s\",\"hwnd\":%llu,\"xid\":%llu,\"iconic\":%s,\"rect\":[%ld,%ld,%ld,%ld]}\n",phase,
  (unsigned long long)(ULONG_PTR)hwnd,(unsigned long long)(ULONG_PTR)GetPropW(hwnd,L"__wine_x11_whole_window"),
  IsIconic(hwnd)?"true":"false",rect.left,rect.top,rect.right,rect.bottom);std::fclose(f);
 if(!MoveFileExW((name+L".tmp").c_str(),name.c_str(),MOVEFILE_REPLACE_EXISTING))return false;
 std::wstring gate=directory+L"\\";for(const char *c=phase;*c;c++)gate+=(wchar_t)*c;gate+=L".go";
 ULONGLONG until=GetTickCount64()+15000;
 while(GetFileAttributesW(gate.c_str())==INVALID_FILE_ATTRIBUTES&&GetTickCount64()<until)pump(20);
 return GetFileAttributesW(gate.c_str())!=INVALID_FILE_ATTRIBUTES;
}
int wmain(int argc,wchar_t **argv) {
 if(argc!=2||!_wgetenv(L"QTK_SYNTHETIC_DATAONLY"))return 2;
 directory=argv[1];
 SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
 WNDCLASSW c={};c.lpfnWndProc=procedure;c.hInstance=GetModuleHandleW(NULL);c.lpszClassName=L"OriginalPreviewFixture";c.hbrBackground=(HBRUSH)GetStockObject(WHITE_BRUSH);
 if(!RegisterClassW(&c))return 3;
 HWND hwnd=CreateWindowExW(WS_EX_APPWINDOW,c.lpszClassName,L"Original preview fixture",WS_OVERLAPPEDWINDOW,80,80,800,500,NULL,NULL,c.hInstance,NULL);
 if(!hwnd)return 4;
 ShowWindow(hwnd,SW_SHOWNORMAL);InvalidateRect(hwnd,NULL,TRUE);UpdateWindow(hwnd);
 if(!checkpoint(hwnd,"normal"))return 5;
 ShowWindow(hwnd,SW_MINIMIZE);InvalidateRect(hwnd,NULL,TRUE);UpdateWindow(hwnd);
 if(!IsIconic(hwnd)||!checkpoint(hwnd,"minimized"))return 6;
 ShowWindow(hwnd,SW_RESTORE);InvalidateRect(hwnd,NULL,TRUE);UpdateWindow(hwnd);
 if(IsIconic(hwnd)||!checkpoint(hwnd,"restored"))return 7;
 SetWindowPos(hwnd,NULL,100,100,960,600,SWP_NOZORDER|SWP_NOACTIVATE);InvalidateRect(hwnd,NULL,TRUE);UpdateWindow(hwnd);
 if(!checkpoint(hwnd,"resized"))return 8;
 ShowWindow(hwnd,SW_MINIMIZE);InvalidateRect(hwnd,NULL,TRUE);UpdateWindow(hwnd);
 if(!IsIconic(hwnd)||!checkpoint(hwnd,"minimized-again"))return 9;
 ShowWindow(hwnd,SW_RESTORE);InvalidateRect(hwnd,NULL,TRUE);UpdateWindow(hwnd);
 if(IsIconic(hwnd)||!checkpoint(hwnd,"restored-again"))return 10;
 DestroyWindow(hwnd);pump(100);return 0;
}
