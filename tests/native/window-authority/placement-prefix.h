/* Original toolkit test boundary/cases; MIT license, see repository LICENSE. */
/* Original inert protocol boundary. Does not open X, Wine, files or processes. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdarg.h>
typedef unsigned int UINT;
typedef unsigned int DWORD;
typedef int BOOL;
typedef uintptr_t HWND;
typedef unsigned long Window;
typedef unsigned long Atom;
typedef struct { int left,top,right,bottom; } RECT;
struct window_rects { RECT window,client,visible; };
struct monitor_indices { unsigned long generation;long indices[4]; };
struct hints { unsigned long decorations; };
struct state { UINT wm_state,activate,net_wm_state;struct monitor_indices monitors;RECT rect;struct hints mwm_hints; };
struct x11drv_win_data { HWND hwnd;Window whole_window;void *display;BOOL managed,embedded,is_fullscreen,is_resizable,skip_taskbar,add_taskbar,is_offscreen;
    UINT state_locks;unsigned long wm_state_serial,net_wm_state_serial,mwm_hints_serial,configure_serial;
    struct state desired_state,pending_state,current_state;struct window_rects rects; };
typedef struct { struct { int type;Window window;Atom message_type;unsigned long serial;void *display;int send_event,format;union {long l[5];}data; }xclient; }XEvent;
enum { KDE_NET_WM_STATE_SKIP_SWITCHER,NET_WM_STATE_FULLSCREEN,NET_WM_STATE_ABOVE,NET_WM_STATE_MAXIMIZED,NET_WM_STATE_SKIP_PAGER,NET_WM_STATE_SKIP_TASKBAR,NB_NET_WM_STATES };
enum { XATOM__KDE_NET_WM_STATE_SKIP_SWITCHER,XATOM__NET_WM_STATE_FULLSCREEN,XATOM__NET_WM_STATE_ABOVE,
    XATOM__NET_WM_STATE_MAXIMIZED_VERT,XATOM__NET_WM_STATE_SKIP_PAGER,XATOM__NET_WM_STATE_SKIP_TASKBAR,
    XATOM__NET_WM_STATE_MAXIMIZED_HORZ,XATOM__NET_WM_STATE,XATOM__NET_WM_FULLSCREEN_MONITORS };
static const unsigned int net_wm_state_atoms[NB_NET_WM_STATES]={0,1,2,3,4,5};
static Atom X11DRV_Atoms[]={100,101,102,103,104,105,106,107,108};
#define FIRST_XATOM 0
#define x11drv_atom(name) X11DRV_Atoms[XATOM_##name]
#define TRUE 1
#define FALSE 0
#define True 1
#define False 0
#define WithdrawnState 0
#define NormalState 1
#define IconicState 3
#define ClientMessage 33
#define XA_ATOM 4
#define XA_CARDINAL 6
#define PropModeReplace 0
#define SubstructureRedirectMask 0x100000
#define SubstructureNotifyMask 0x80000
#define GWL_STYLE -16
#define GWL_EXSTYLE -20
#define GW_OWNER 4
#define WS_VISIBLE 0x10000000u
#define WS_MINIMIZE 0x20000000u
#define WS_MAXIMIZE 0x01000000u
#define WS_CAPTION 0x00c00000u
#define WS_MINIMIZEBOX 0x20000u
#define WS_MAXIMIZEBOX 0x10000u
#define WS_DISABLED 0x08000000u
#define WS_EX_TOPMOST 8u
#define WS_EX_NOACTIVATE 0x08000000u
#define WS_EX_TOOLWINDOW 0x80u
#define WS_EX_APPWINDOW 0x40000u
#define SC_MAXIMIZE 0xf030u
#define SC_MINIMIZE 0xf020u
#define SC_RESTORE 0xf120u
#define SWP_NOSIZE 1u
#define SWP_NOMOVE 2u
#define SWP_NOZORDER 4u
#define SWP_NOACTIVATE 0x10u
#define SWP_NOSENDCHANGING 0x400u
#define _NET_WM_STATE_ADD 1
#define _NET_WM_STATE_REMOVE 0
#define MAKELONG(a,b) ((UINT)(a)|((UINT)(b)<<16))
#define TRACE(...) ((void)0)
#define WARN(...) ((void)0)
static const UINT config_notify_mask=(1<<NET_WM_STATE_MAXIMIZED)|(1<<NET_WM_STATE_FULLSCREEN)|(1<<NET_WM_STATE_ABOVE);
static Window root_window=1;
static const char *option;
static UINT win_style,win_exstyle;
static HWND win_owner;
static BOOL virtual_desktop,desktop_fullscreen,support_handlers,xinerama_ret;
static unsigned long serial_counter;
static unsigned int fs_messages,other_messages,monitor_messages,state_properties,monitor_properties,xinerama_calls;
static UINT last_property_state;
struct thread_data { UINT net_wm_state_mask; };
static struct thread_data thread_data;
static const char *mock_getenv(const char *name) { (void)name;return option; }
#define getenv mock_getenv
static BOOL is_virtual_desktop(void){return virtual_desktop;}
static BOOL is_desktop_fullscreen(void){return desktop_fullscreen;}
static struct thread_data *x11drv_init_thread_data(void){return &thread_data;}
static UINT NtUserGetWindowLongW(HWND h,int index){(void)h;return index==GWL_STYLE?win_style:win_exstyle;}
static HWND NtUserGetWindowRelative(HWND h,int index){(void)h;(void)index;return win_owner;}
static BOOL X11DRV_DisplayDevices_SupportEventHandlers(void){return support_handlers;}
static BOOL xinerama_get_fullscreen_monitors(const RECT *r,unsigned long *generation,long *indices)
{(void)r;xinerama_calls++;*generation=1;for(unsigned i=0;i<4;i++)indices[i]=0;return xinerama_ret;}
static unsigned long NextRequest(void *d){(void)d;return serial_counter+1;}
static Window DefaultRootWindow(void *d){(void)d;return root_window;}
static int XChangeProperty(void*d,Window w,Atom property,Atom type,int format,int mode,unsigned char *bytes,int count)
{(void)d;(void)w;(void)type;(void)format;(void)mode;serial_counter++;
 if(property==x11drv_atom(_NET_WM_FULLSCREEN_MONITORS))monitor_properties++;
 else {state_properties++;last_property_state=0;Atom *a=(Atom*)bytes;for(int i=0;i<count;i++)for(int j=0;j<NB_NET_WM_STATES;j++)if(a[i]==X11DRV_Atoms[j])last_property_state|=1u<<j;}
 return 1;}
static int XSendEvent(void*d,Window w,int propagate,long mask,XEvent *event)
{(void)d;(void)w;(void)propagate;(void)mask;serial_counter++;
 if(event->xclient.message_type==x11drv_atom(_NET_WM_FULLSCREEN_MONITORS))monitor_messages++;
 else if((Atom)event->xclient.data.l[1]==x11drv_atom(_NET_WM_STATE_FULLSCREEN))fs_messages++;
 else other_messages++;
 return 1;}
static const char *wine_dbg_sprintf(const char *format,...){(void)format;return "inert";}
static RECT window_rect_from_visible(const struct window_rects *r,RECT visible)
{visible.left+=r->window.left-r->visible.left;visible.top+=r->window.top-r->visible.top;
 visible.right+=r->window.right-r->visible.right;visible.bottom+=r->window.bottom-r->visible.bottom;return visible;}
static void OffsetRect(RECT*r,int x,int y){r->left+=x;r->right+=x;r->top+=y;r->bottom+=y;}
static BOOL IsRectEmpty(const RECT*r){return r->right<=r->left||r->bottom<=r->top;}
static BOOL is_window_rect_mapped(const RECT*r){(void)r;return TRUE;}
static void window_set_wm_state(struct x11drv_win_data*d,UINT state,BOOL activate){(void)d;(void)state;(void)activate;}
static void window_set_mwm_hints(struct x11drv_win_data*d,const struct hints*h){(void)d;(void)h;}
static void window_set_config(struct x11drv_win_data*d,RECT r,BOOL above){(void)d;(void)r;(void)above;}
