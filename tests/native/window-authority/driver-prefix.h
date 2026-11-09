/* Original toolkit test boundary/cases; MIT license, see repository LICENSE. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <assert.h>
typedef int BOOL;typedef uintptr_t Window;typedef void *HANDLE;typedef void *HWND;typedef unsigned short WCHAR;
typedef unsigned UINT;typedef unsigned long DWORD;typedef uintptr_t WPARAM;typedef intptr_t LPARAM;typedef intptr_t LRESULT;
typedef struct {int x,y;} POINT;
#define TRUE 1
#define FALSE 0
#define SC_SCREENSAVE 0xf140
#define SC_MOVE 0xf010
#define SC_SIZE 0xf000
#define SC_KEYMENU 0xf100
#define HTCAPTION 2
#define WS_MAXIMIZE 0x01000000
#define WS_THICKFRAME 0x40000
#define WS_SYSMENU 0x80000
#define GWL_STYLE -16
#define GWLP_ID -12
#define WMSZ_LEFT 1
#define WMSZ_RIGHT 2
#define WMSZ_TOP 3
#define WMSZ_TOPLEFT 4
#define WMSZ_TOPRIGHT 5
#define WMSZ_BOTTOM 6
#define WMSZ_BOTTOMLEFT 7
#define WMSZ_BOTTOMRIGHT 8
#define WithdrawnState 0
#define NormalState 1
#define _NET_WM_MOVERESIZE_MOVE 8
#define _NET_WM_MOVERESIZE_MOVE_KEYBOARD 10
#define _NET_WM_MOVERESIZE_SIZE_LEFT 7
#define _NET_WM_MOVERESIZE_SIZE_RIGHT 3
#define _NET_WM_MOVERESIZE_SIZE_TOP 1
#define _NET_WM_MOVERESIZE_SIZE_TOPLEFT 0
#define _NET_WM_MOVERESIZE_SIZE_TOPRIGHT 2
#define _NET_WM_MOVERESIZE_SIZE_BOTTOM 5
#define _NET_WM_MOVERESIZE_SIZE_BOTTOMLEFT 6
#define _NET_WM_MOVERESIZE_SIZE_BOTTOMRIGHT 4
#define _NET_WM_MOVERESIZE_SIZE_KEYBOARD 9
#define x11drv_atom(x) 12
#define TRACE(...) ((void)0)
struct x11drv_win_data{HWND hwnd;Window whole_window;BOOL managed,embedded;struct{UINT wm_state;}desired_state;};
static struct x11drv_win_data data;
static Window root_window=9;static BOOL virtual_desktop,supported=1;static HANDLE backend,cap;
static DWORD style;static unsigned assertions,cases,sets,removes,releases,moves;static int last_dir;static POINT last_pos;
static BOOL is_virtual_desktop(void){return virtual_desktop;}
static BOOL is_net_supported(int atom){(void)atom;return supported;}
static int marker(const WCHAR *n){return n[18]=='c';}
static HANDLE NtUserGetProp(HWND h,const WCHAR*n){(void)h;return marker(n)?cap:backend;}
static void NtUserSetProp(HWND h,const WCHAR*n,HANDLE v){(void)h;sets++;if(marker(n))cap=v;else backend=v;}
static void NtUserRemoveProp(HWND h,const WCHAR*n){(void)h;removes++;if(marker(n))cap=0;else backend=0;}
static struct x11drv_win_data *get_win_data(HWND h){return h==data.hwnd?&data:0;}
static HWND NtUserGetDesktopWindow(void){return (HWND)9;}
static int start_screensaver(void){return 8;}
static void release_win_data(struct x11drv_win_data*d){(void)d;releases++;}
static DWORD NtUserGetWindowLongW(HWND h,int n){(void)h;(void)n;return style;}
static uintptr_t NtUserGetWindowLongPtrW(HWND h,int n){(void)h;(void)n;return 0;}
static void move_resize_window(HWND h,int dir,POINT p){(void)h;moves++;last_dir=dir;last_pos=p;}
