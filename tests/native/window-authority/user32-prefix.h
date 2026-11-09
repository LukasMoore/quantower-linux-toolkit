/* Original toolkit test boundary/cases; MIT license, see repository LICENSE. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <stdint.h>
#include <stdarg.h>
#include <assert.h>
typedef int BOOL; typedef unsigned UINT; typedef unsigned long DWORD; typedef long LONG;
typedef wchar_t WCHAR; typedef int INT; typedef uintptr_t WPARAM; typedef intptr_t LPARAM;
typedef intptr_t INT_PTR; typedef intptr_t LRESULT; typedef void *HANDLE; typedef void *HWND; typedef void *DPI_AWARENESS_CONTEXT;
typedef unsigned char BYTE; typedef const char *LPCSTR; typedef LRESULT (*WNDPROC)(HWND,UINT,WPARAM,LPARAM);
#define TRUE 1
#define FALSE 0
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define WM_NCLBUTTONDOWN 161
#define WM_NCLBUTTONUP 162
#define WM_NCLBUTTONDBLCLK 163
#define WM_NCHITTEST 132
#define WM_SYSCOMMAND 274
#define WM_ENTERSIZEMOVE 561
#define WM_EXITSIZEMOVE 562
#define WM_CANCELMODE 31
#define WM_CAPTURECHANGED 533
#define HTCAPTION 2
#define HTERROR -2
#define HTHELP 21
#define SC_MOVE 0xf010
#define SC_SIZE 0xf000
#define WS_THICKFRAME 0x40000
#define WS_CHILD 0x40000000
#define WS_POPUP ((LONG)0x80000000)
#define WS_VISIBLE 0x10000000
#define WS_DISABLED 0x08000000
#define WS_MINIMIZE 0x20000000
#define WS_MAXIMIZE 0x01000000
#define WS_EX_APPWINDOW 0x40000
#define WS_EX_TOOLWINDOW 0x80
#define WS_EX_NOACTIVATE 0x08000000
#define WS_EX_DLGMODALFRAME 1
#define GA_ROOT 2
#define GW_OWNER 4
#define GWL_STYLE -16
#define GWL_EXSTYLE -20
#define WINPROC_PROC16 ((WNDPROC)(uintptr_t)1)
#define ULongToHandle(x) ((HANDLE)(uintptr_t)(x))
struct win_proc_params { HWND hwnd; UINT msg; WPARAM wparam; LPARAM lparam; WNDPROC procA,procW; void *func; UINT dpi_context; BOOL ansi,ansi_dst; int mapping; };
struct teb { DWORD LastErrorValue; } teb;
#define NtCurrentTeb() (&teb)
struct window { HWND hwnd,owner,root; DWORD pid,tid; LONG style,exstyle; const WCHAR *title,*class_name; BOOL enabled; HANDLE whole,backend,movable; } windows[4];
static const WCHAR *move_option,*trace_option;
static unsigned queries,env_queries,dwp_a,dwp_w,app_calls,group_arms,trace_lines,trace_entries,trace_exits,trace_caps;
static unsigned assertions,cases,nested_calls; static DWORD tick=1; static DPI_AWARENESS_CONTEXT dpi;
static UINT observed_dpi; static LONG trace_call[2200],trace_parent[2200]; static UINT trace_depth[2200]; static int trace_hit[2200];
static void log_line(const char *fmt,...)
{
 va_list ap;char out[1024];va_start(ap,fmt);vsnprintf(out,sizeof(out),fmt,ap);va_end(ap);trace_lines++;
 if(strstr(out,"phase=0")){long a,b;unsigned d;if(sscanf(out,"QTKCAP phase=0 call=%ld parent=%ld depth=%u",&a,&b,&d)==3){trace_call[trace_entries]=a;trace_parent[trace_entries]=b;trace_depth[trace_entries]=d;char *hit=strstr(out," hit=");assert(hit);trace_hit[trace_entries]=strtol(hit+5,0,10);}trace_entries++;}
 if(strstr(out,"phase=1"))trace_exits++;
 if(strstr(out,"phase=2"))trace_caps++;
 assert(!strstr(out,"lparam=")&&!strstr(out,"coords=")&&!strstr(out,"Chart"));
 teb.LastErrorValue=901;
}
#define WARN_(x) log_line
#define TRACE_(x) dummy_trace
static void dummy_trace(const char *fmt,...){(void)fmt;}
static const char *SPY_GetMsgName(UINT msg,HWND hwnd){(void)msg;(void)hwnd;return "inert";}
static struct window *lookup(HWND hwnd){unsigned i;queries++;teb.LastErrorValue=900;for(i=0;i<ARRAY_SIZE(windows);i++)if(windows[i].hwnd==hwnd)return &windows[i];abort();}
static DWORD GetCurrentThreadId(void){return 7;}
static DWORD GetCurrentProcessId(void){return 3;}
static DWORD GetWindowThreadProcessId(HWND h,DWORD *pid){struct window*w=lookup(h);*pid=w->pid;return w->tid;}
static HWND NtUserGetAncestor(HWND h,int type){(void)type;return lookup(h)->root;}
static LONG GetWindowLongW(HWND h,int n){return n==GWL_STYLE?lookup(h)->style:lookup(h)->exstyle;}
static int GetClassNameW(HWND h,WCHAR *out,int n){const WCHAR*s=lookup(h)->class_name;wcsncpy(out,s,n-1);out[n-1]=0;return wcslen(out);}
static int NtUserInternalGetWindowText(HWND h,WCHAR*out,int n){const WCHAR*s=lookup(h)->title;wcsncpy(out,s,n-1);out[n-1]=0;return wcslen(out);}
static HWND GetWindow(HWND h,int n){(void)n;return lookup(h)->owner;}
static BOOL IsWindowEnabled(HWND h){return lookup(h)->enabled;}
static HANDLE GetPropW(HWND h,const WCHAR*n){struct window*w=lookup(h);return wcsstr(n,L"x11")?w->whole:wcsstr(n,L"placement")?w->backend:w->movable;}
static DWORD GetEnvironmentVariableW(const WCHAR*n,WCHAR*out,DWORD cap){const WCHAR*v=wcsstr(n,L"TRACE")?trace_option:move_option;DWORD len;env_queries++;teb.LastErrorValue=902;if(!v)return 0;len=wcslen(v);if(len>=cap)return len+1;wcscpy(out,v);return len;}
static LONG InterlockedCompareExchange(LONG*v,LONG n,LONG c){LONG o=*v;if(o==c)*v=n;return o;}
static LONG InterlockedExchange(LONG*v,LONG n){LONG o=*v;*v=n;return o;}
static LONG InterlockedIncrement(LONG*v){return ++*v;}
static DWORD GetTickCount(void){return tick;}
static DPI_AWARENESS_CONTEXT SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT n){DPI_AWARENESS_CONTEXT o=dpi;dpi=n;return o;}
static LRESULT DefWindowProcA(HWND h,UINT m,WPARAM w,LPARAM l){(void)h;(void)m;(void)w;(void)l;dwp_a++;observed_dpi=(uintptr_t)dpi;teb.LastErrorValue=55;return 71;}
static LRESULT DefWindowProcW(HWND h,UINT m,WPARAM w,LPARAM l){(void)h;(void)m;(void)w;(void)l;dwp_w++;observed_dpi=(uintptr_t)dpi;teb.LastErrorValue=55;return 72;}
static LRESULT WINPROC_wrapper(WNDPROC p,HWND h,UINT m,WPARAM w,LPARAM l){return p(h,m,w,l);}
typedef LRESULT (*winproc_callback_t)(HWND,UINT,WPARAM,LPARAM,LRESULT*,void*);
static LRESULT WINPROC_CallProcWtoA(winproc_callback_t cb,HWND h,UINT m,WPARAM w,LPARAM l,LRESULT*r,void*p){return cb(h,m,w,l,r,p);}
static LRESULT WINPROC_CallProcAtoW(winproc_callback_t cb,HWND h,UINT m,WPARAM w,LPARAM l,LRESULT*r,void*p,int mapping){(void)mapping;return cb(h,m,w,l,r,p);}
static LRESULT wow_proc(HWND h,UINT m,WPARAM w,LPARAM l,LRESULT*r,void*p){(void)h;(void)m;(void)w;(void)l;(void)p;*r=19;return 19;}
static struct {winproc_callback_t call_window_proc;} wow_handlers={wow_proc};
