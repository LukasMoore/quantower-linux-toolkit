// SPDX-License-Identifier: MIT
// Original supplemental own-window regression. No application, account or market data.
#define UNICODE
#define _UNICODE
#define _WIN32_WINNT 0x0a00
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static HWND parent,target;
static HANDLE entered,exited;
static RECT initial,final_rect,parent_before,parent_after,final_parent_rect;
static int child,outline,keys,border,op,scenario,failures,enters,exits,snapped,capture_seen,intermediate_moved;
static volatile LONG sample_count;
#define samples InterlockedCompareExchange(&sample_count,0,0)
static BOOL prior_drag,configured_drag;
static int dpi;
static int move_mode(void){return op==0||border;}
static int right_mode(void){return op==1||op==2;}
static int bottom_mode(void){return op==2;}
static LRESULT CALLBACK proc(HWND h,UINT m,WPARAM w,LPARAM l)
{
    if(h!=target)return DefWindowProcW(h,m,w,l);
    if(m==WM_ENTERSIZEMOVE){enters++;SetEvent(entered);}
    if(m==WM_EXITSIZEMOVE){exits++;SetEvent(exited);}
    if((move_mode()&&m==WM_MOVING)||(!move_mode()&&m==WM_SIZING))
    {
        RECT *r=(RECT *)l,current;GetWindowRect(h,&current);
        if(!EqualRect(&current,&initial))intermediate_moved++;
        if(scenario!=0)
        {
            const int magnet=keys?40:100;
            if(move_mode()&&abs(r->left-initial.left-magnet)<=20){OffsetRect(r,initial.left+magnet-r->left,0);snapped++;}
            if(!move_mode()&&right_mode()&&abs(r->right-initial.right-magnet)<=20){r->right=initial.right+magnet;snapped++;}
            if(!move_mode()&&bottom_mode()&&abs(r->bottom-initial.bottom-magnet)<=20){r->bottom=initial.bottom+magnet;snapped++;}
        }
        InterlockedIncrement(&sample_count);
        // Queue completion before a resize posts paint/size notifications. This
        // mirrors the bounded queued-command approach of Wine test_SC_SIZE and
        // avoids testing Wine's separate mouse-up/dispatch exit limitation.
        if(keys&&!border)PostMessageW(h,WM_KEYDOWN,scenario==3?VK_ESCAPE:VK_RETURN,0);
        return TRUE;
    }
    if(m==WM_APP+99)
    {
        GetWindowRect(h,&final_rect);final_parent_rect=final_rect;
        if(child)MapWindowPoints(NULL,parent,(POINT *)&final_parent_rect,2);
        if(parent)GetWindowRect(parent,&parent_after);
        DestroyWindow(h);PostQuitMessage(0);return 0;
    }
    return DefWindowProcW(h,m,w,l);
}
static int button(DWORD flags){INPUT i={0};i.type=INPUT_MOUSE;i.mi.dwFlags=flags;return SendInput(1,&i,sizeof(i))==1;}
static int capture(void)
{
    ULONGLONG deadline=GetTickCount64()+2000;
    do{GUITHREADINFO g={0};g.cbSize=sizeof(g);if(GetGUIThreadInfo(GetWindowThreadProcessId(target,NULL),&g)&&g.hwndCapture==target)return 1;Sleep(2);}while(GetTickCount64()<deadline);
    return 0;
}
static DWORD WINAPI drive(void *unused)
{
    (void)unused;Sleep(150);GetWindowRect(target,&initial);if(parent)GetWindowRect(parent,&parent_before);
    int x=move_mode()?(initial.left+initial.right)/2:initial.right-2;
    int y=bottom_mode()?initial.bottom-2:move_mode()?initial.top+12:(initial.top+initial.bottom)/2;
    if(keys)
    {
        if(!SetCursorPos(x,y))failures++;
        Sleep(30);PostMessageW(target,WM_SYSCOMMAND,border?(SC_SIZE|9):SC_SIZE,MAKELPARAM(x,y));
        if(!border){Sleep(80);PostMessageW(target,WM_KEYDOWN,VK_RIGHT,0);}
    }
    else
    {
        if(!SetCursorPos(30,30))failures++;
        Sleep(20);
        if(!button(MOUSEEVENTF_LEFTDOWN))failures++;
        Sleep(20);
        if(!SetCursorPos(x,y))failures++;
        Sleep(20);
        DWORD_PTR hit=0;int desired=op==0?HTCAPTION:op==1?HTRIGHT:HTBOTTOMRIGHT;
        if(!SendMessageTimeoutW(target,WM_NCHITTEST,0,MAKELPARAM(x,y),SMTO_ABORTIFHUNG,1000,&hit)||(int)hit!=desired)failures++;
        if(!failures)PostMessageW(target,WM_SYSCOMMAND,op==0?(SC_MOVE|HTCAPTION):(SC_SIZE|(op==1?WMSZ_RIGHT:WMSZ_BOTTOMRIGHT)),MAKELPARAM(x,y));
    }
    if(WaitForSingleObject(entered,2000)!=WAIT_OBJECT_0)failures++;
    capture_seen=capture();if(!capture_seen)failures++;
    Sleep(50);
    if(keys&&!failures)
    {
        for(int n=0;n<(border?10:1);n++)
        {
            int before=samples;PostMessageW(target,WM_KEYDOWN,VK_RIGHT,0);
            ULONGLONG deadline=GetTickCount64()+1000;
            while(samples==before&&GetTickCount64()<deadline)Sleep(2);
            if(samples==before){failures++;break;}Sleep(8);
        }
    }
    else if(!failures)
    {
        for(int n=1;n<=150;n++){if(!SetCursorPos(x+n,y+(bottom_mode()?n:0)))failures++;Sleep(5);}
        if(scenario==2)for(int n=149;n>=0;n--){if(!SetCursorPos(x+n,y+(bottom_mode()?n:0)))failures++;Sleep(5);}
    }
    Sleep(40);
    if(keys||scenario==3)PostMessageW(target,WM_KEYDOWN,scenario==3?VK_ESCAPE:VK_RETURN,0);
    if(!keys&&!button(MOUSEEVENTF_LEFTUP))failures++;
    if(WaitForSingleObject(exited,2000)!=WAIT_OBJECT_0)failures++;
    Sleep(40);PostMessageW(target,WM_APP+99,0,0);return 0;
}
static void rect(const RECT *r){printf("[%ld,%ld,%ld,%ld]",r->left,r->top,r->right,r->bottom);}
int main(int argc,char **argv)
{
    if(argc!=5||!getenv("QTK_SYNTHETIC_DATAONLY"))return 2;
    if(!strcmp(argv[1],"top-full"))child=outline=0;
    else if(!strcmp(argv[1],"child-full"))child=1;
    else if(!strcmp(argv[1],"child-outline"))child=outline=1;
    else return 2;
    if(!strcmp(argv[2],"pointer-move"))op=0;
    else if(!strcmp(argv[2],"pointer-right"))op=1;
    else if(!strcmp(argv[2],"pointer-bottomright"))op=2;
    else if(!strcmp(argv[2],"keyboard-right")){keys=1;op=1;}
    else if(!strcmp(argv[2],"keyboard-border")){keys=border=1;op=0;}
    else return 2;
    if(!strcmp(argv[3],"nosnap"))scenario=0;
    else if(!strcmp(argv[3],"snap"))scenario=1;
    else if(!strcmp(argv[3],"reverse"))scenario=2;
    else if(!strcmp(argv[3],"cancel"))scenario=3;
    else return 2;
    if(strcmp(argv[4],"96")||(!child&&outline)||(keys&&scenario==2))return 2;
    SetProcessDPIAware();
    if(!SystemParametersInfoW(SPI_GETDRAGFULLWINDOWS,0,&prior_drag,0)||!SystemParametersInfoW(SPI_SETDRAGFULLWINDOWS,!outline,NULL,0)||!SystemParametersInfoW(SPI_GETDRAGFULLWINDOWS,0,&configured_drag,0)||configured_drag!=!outline)return 3;
    entered=CreateEventW(NULL,TRUE,FALSE,NULL);exited=CreateEventW(NULL,TRUE,FALSE,NULL);
    WNDCLASSW wc={0};wc.lpfnWndProc=proc;wc.hInstance=GetModuleHandleW(NULL);wc.lpszClassName=L"QtkOriginalSupplemental";wc.hCursor=LoadCursorW(NULL,IDC_ARROW);
    if(!RegisterClassW(&wc))return 4;
    if(child)parent=CreateWindowW(wc.lpszClassName,L"Original synthetic parent",WS_OVERLAPPEDWINDOW|WS_VISIBLE,300,300,1600,1400,NULL,NULL,wc.hInstance,NULL);
    target=CreateWindowW(wc.lpszClassName,L"Original synthetic child keyboard fixture",WS_OVERLAPPEDWINDOW|WS_VISIBLE|(child?WS_CHILD:0),child?200:500,child?200:500,600,400,parent,NULL,wc.hInstance,NULL);
    if(!target)return 5;
    dpi=GetDpiForWindow(target);if(dpi!=96)failures++;
    UpdateWindow(target);SetForegroundWindow(parent?parent:target);
    HANDLE worker=CreateThread(NULL,0,drive,NULL,0,NULL);if(!worker)return 6;
    MSG m;while(GetMessageW(&m,NULL,0,0)>0){TranslateMessage(&m);DispatchMessageW(&m);}
    if(WaitForSingleObject(worker,3000)!=WAIT_OBJECT_0)return 7;
    RECT expected=initial;int distance=scenario>=2?0:keys?(border?80:8):150;
    if(move_mode())OffsetRect(&expected,distance,0);
    else{expected.right+=distance;if(bottom_mode())expected.bottom+=distance;}
    RECT expected_parent=expected;if(child)MapWindowPoints(NULL,parent,(POINT *)&expected_parent,2);
    int exact=EqualRect(&expected,&final_rect)&&(!child||EqualRect(&expected_parent,&final_parent_rect));
    int parent_fixed=!child||EqualRect(&parent_before,&parent_after);
    int outline_preserved=!outline||intermediate_moved==0;
    int meaningful_snap=scenario==0||(keys&&!border)||snapped>0;
    int valid=!failures&&samples>0&&enters==1&&exits==1&&capture_seen&&dpi==96&&parent_fixed&&outline_preserved&&meaningful_snap;
    printf("{\"scope\":\"%s\",\"operation\":\"%s\",\"scenario\":\"%s\",\"failures\":%d,\"samples\":%ld,\"snapped_samples\":%d,\"enters\":%d,\"exits\":%d,\"capture_seen\":%s,\"dpi\":%d,\"drag_full_windows\":%s,\"intermediate_geometry_changed_samples\":%d,\"parent_unchanged\":%s,\"outline_geometry_preserved_until_commit\":%s,\"initial\":",argv[1],argv[2],argv[3],failures,samples,snapped,enters,exits,capture_seen?"true":"false",dpi,configured_drag?"true":"false",intermediate_moved,parent_fixed?"true":"false",outline_preserved?"true":"false");
    rect(&initial);printf(",\"expected\":");rect(&expected);printf(",\"actual\":");rect(&final_rect);printf(",\"actual_in_parent\":");rect(&final_parent_rect);
    printf(",\"injection_valid\":%s,\"exact_expected_geometry\":%s,\"passed\":%s}\n",valid?"true":"false",exact?"true":"false",valid&&exact?"true":"false");
    SystemParametersInfoW(SPI_SETDRAGFULLWINDOWS,prior_drag,NULL,0);if(parent)DestroyWindow(parent);CloseHandle(worker);CloseHandle(entered);CloseHandle(exited);
    return valid?0:1;
}
