// SPDX-License-Identifier: MIT
// Original own-window regression. No vendor code/assets or external-window hooks.
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static HWND target;
static HANDLE entered,exited;
static int edge, scenario, step, left_side,right_side,top_side,bottom_side;
static int samples,snapped,failures,enters,exits,observed_hit,capture_seen;
static RECT initial,final_rect;
static struct {POINT cursor;RECT proposed,applied;} trace[512];
static const char *names[]={"move","left","right","top","topleft","topright","bottom","bottomleft","bottomright"};
static const char *scenarios[]={"nosnap","snap","reverse","cancel","max","min"};
static int snap_axis(LONG *a,LONG *b,int move,LONG magnet) {
    if(abs(*a-magnet)>20)return 0;
    if(move)*b+=magnet-*a;
    *a=magnet;return 1;
}
static LRESULT CALLBACK proc(HWND h,UINT m,WPARAM w,LPARAM l) {
    if(m==WM_GETMINMAXINFO && scenario>=4) {
        MINMAXINFO *i=(MINMAXINFO *)l;
        i->ptMinTrackSize.x=150;i->ptMinTrackSize.y=100;
        i->ptMaxTrackSize.x=800;i->ptMaxTrackSize.y=600;
        return 0;
    }
    if(m==WM_ENTERSIZEMOVE){enters++;SetEvent(entered);}
    if(m==WM_EXITSIZEMOVE){exits++;SetEvent(exited);}
    if((edge==0&&m==WM_MOVING)||(edge!=0&&m==WM_SIZING)) {
        RECT *r=(RECT *)l,raw=*r;POINT p;GetCursorPos(&p);int hit=0;
        if(scenario>=1 && scenario<=3) {
            if(edge==0)hit=snap_axis(&r->left,&r->right,1,initial.left+200);
            else {
                if(left_side)hit|=snap_axis(&r->left,&r->right,0,initial.left-200);
                if(right_side)hit|=snap_axis(&r->right,&r->left,0,initial.right+200);
                if(top_side)hit|=snap_axis(&r->top,&r->bottom,0,initial.top-200);
                if(bottom_side)hit|=snap_axis(&r->bottom,&r->top,0,initial.bottom+200);
            }
        }
        snapped+=!!hit;
        if(samples<512){trace[samples].cursor=p;trace[samples].proposed=raw;trace[samples].applied=*r;}
        samples++;return TRUE;
    }
    if(m==WM_APP+99){GetWindowRect(h,&final_rect);DestroyWindow(h);return 0;}
    if(m==WM_DESTROY){PostQuitMessage(0);return 0;}
    return DefWindowProcW(h,m,w,l);
}
static int button(DWORD flags){INPUT in={0};in.type=INPUT_MOUSE;in.mi.dwFlags=flags;return SendInput(1,&in,sizeof(in))==1;}
static DWORD WINAPI drive(void *unused) {
    (void)unused;Sleep(200);GetWindowRect(target,&initial);
    int x=left_side?initial.left+2:right_side?initial.right-2:(initial.left+initial.right)/2;
    int y=top_side?initial.top+2:bottom_side?initial.bottom-2:edge==0?initial.top+12:(initial.top+initial.bottom)/2;
    int dx=left_side?-1:right_side?1:edge==0?1:0,dy=top_side?-1:bottom_side?1:0;
    // Begin button state in unused own-Xvfb desktop space, preventing a second
    // native-caption command from racing the explicitly targeted SC command.
    if(!SetCursorPos(30,30))failures++;
    Sleep(30);if(!button(MOUSEEVENTF_LEFTDOWN))failures++;
    Sleep(30);if(!SetCursorPos(x,y))failures++;
    Sleep(30);DWORD_PTR hit=0;
    if(!SendMessageTimeoutW(target,WM_NCHITTEST,0,MAKELPARAM(x,y),SMTO_ABORTIFHUNG,1000,&hit))failures++;
    observed_hit=(int)hit;
    if(hit!=(DWORD_PTR)(edge?HTLEFT+edge-1:HTCAPTION))failures++;
    if(failures){button(MOUSEEVENTF_LEFTUP);PostMessageW(target,WM_APP+99,0,0);return 0;}
    PostMessageW(target,WM_SYSCOMMAND,edge?(SC_SIZE|edge):(SC_MOVE|HTCAPTION),MAKELPARAM(x,y));
    if(WaitForSingleObject(entered,2000)!=WAIT_OBJECT_0)failures++;
    // WM_ENTERSIZEMOVE precedes Wine's SetCapture. Do not send a fast first
    // motion until the modal loop owns capture (same process/thread only).
    ULONGLONG deadline=GetTickCount64()+2000;
    do {
        GUITHREADINFO gui={0};gui.cbSize=sizeof(gui);
        if(GetGUIThreadInfo(GetWindowThreadProcessId(target,NULL),&gui)&&gui.hwndCapture==target){capture_seen=1;break;}
        Sleep(2);
    }while(GetTickCount64()<deadline);
    if(!capture_seen)failures++;
    Sleep(30);
    int distance=scenario==4?350:scenario==5?-550:190;
    int direction=distance<0?-1:1,travel=abs(distance);
    if(!failures)for(int i=step;i<=travel;i+=step){if(!SetCursorPos(x+dx*i*direction,y+dy*i*direction))failures++;Sleep(5);}
    if(scenario==2&&!failures)for(int i=travel-step;i>=0;i-=step){if(!SetCursorPos(x+dx*i,y+dy*i))failures++;Sleep(5);}
    Sleep(50);
    if(scenario==3){PostMessageW(target,WM_KEYDOWN,VK_ESCAPE,0);if(WaitForSingleObject(exited,2000)!=WAIT_OBJECT_0)failures++;}
    if(!button(MOUSEEVENTF_LEFTUP))failures++;
    if(WaitForSingleObject(exited,2000)!=WAIT_OBJECT_0)failures++;
    Sleep(50);PostMessageW(target,WM_APP+99,0,0);return 0;
}
static void print_rect(const RECT *r){printf("[%ld,%ld,%ld,%ld]",r->left,r->top,r->right,r->bottom);}
int main(int argc,char **argv) {
    if(argc!=4||!getenv("QTK_SYNTHETIC_DATAONLY"))return 2;
    edge=-1;scenario=-1;
    for(int i=0;i<9;i++)if(!strcmp(argv[1],names[i]))edge=i;
    for(int i=0;i<6;i++)if(!strcmp(argv[2],scenarios[i]))scenario=i;
    step=atoi(argv[3]);if(edge<0||scenario<0||(step!=1&&step!=250)||(!edge&&scenario>=4))return 2;
    left_side=edge==1||edge==4||edge==7;right_side=edge==2||edge==5||edge==8;
    top_side=edge==3||edge==4||edge==5;bottom_side=edge==6||edge==7||edge==8;
    SetProcessDPIAware();entered=CreateEventW(NULL,TRUE,FALSE,NULL);exited=CreateEventW(NULL,TRUE,FALSE,NULL);
    WNDCLASSW wc={0};wc.lpfnWndProc=proc;wc.hInstance=GetModuleHandleW(NULL);wc.lpszClassName=L"QtkEightEdgeFixture";wc.hCursor=LoadCursorW(NULL,IDC_ARROW);
    if(!RegisterClassW(&wc))return 3;
    target=CreateWindowW(wc.lpszClassName,L"Original synthetic resize fixture",WS_OVERLAPPEDWINDOW|WS_VISIBLE,500,500,600,400,NULL,NULL,wc.hInstance,NULL);
    if(!target)return 4;
    UpdateWindow(target);SetForegroundWindow(target);
    HANDLE worker=CreateThread(NULL,0,drive,NULL,0,NULL);if(!worker)return 5;
    MSG msg;while(GetMessageW(&msg,NULL,0,0)>0){TranslateMessage(&msg);DispatchMessageW(&msg);}
    if(WaitForSingleObject(worker,3000)!=WAIT_OBJECT_0)return 6;
    RECT expected=initial;
    int distance=scenario==2||scenario==3?0:200;
    if(!edge)OffsetRect(&expected,distance,0);
    // Wine's preexisting exclusive mouse_rect.right/bottom bounds subtract1.
    // Preserve exact baseline behavior rather than using a loose tolerance.
    if(left_side)expected.left-=scenario==4?200:scenario==5?-449:distance;
    if(right_side)expected.right+=scenario==4?199:scenario==5?-450:distance;
    if(top_side)expected.top-=scenario==4?200:scenario==5?-299:distance;
    if(bottom_side)expected.bottom+=scenario==4?199:scenario==5?-300:distance;
    printf("{\"edge\":\"%s\",\"scenario\":\"%s\",\"step\":%d,\"failures\":%d,\"samples\":%d,\"snapped_samples\":%d,\"enters\":%d,\"exits\":%d,\"observed_hit\":%d,\"capture_seen\":%s,\"initial\":",names[edge],scenarios[scenario],step,failures,samples,snapped,enters,exits,observed_hit,capture_seen?"true":"false");
    print_rect(&initial);printf(",\"expected\":");print_rect(&expected);printf(",\"actual\":");print_rect(&final_rect);
    printf(",\"passed\":%s,\"trace\":[",!failures&&enters==1&&exits==1&&EqualRect(&expected,&final_rect)?"true":"false");
    for(int i=0;i<samples&&i<512;i++){printf("%s{\"cursor\":[%ld,%ld],\"proposed\":",i?",":"",trace[i].cursor.x,trace[i].cursor.y);print_rect(&trace[i].proposed);printf(",\"applied\":");print_rect(&trace[i].applied);printf("}");}
    puts("]}");CloseHandle(worker);CloseHandle(entered);CloseHandle(exited);
    return failures||!samples||enters!=1||exits!=1?1:0;
}
