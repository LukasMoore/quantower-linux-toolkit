/* Original toolkit test boundary/cases; MIT license, see repository LICENSE. */
#define CHECK(x) do{assertions++;if(!(x)){fprintf(stderr,"FAIL case=%u line=%u: %s\n",cases,__LINE__,#x);exit(1);}}while(0)
static LRESULT application(HWND h,UINT m,WPARAM w,LPARAM l){(void)h;(void)l;app_calls++;if(m==WM_NCLBUTTONDOWN&&w==HTCAPTION)group_arms++;teb.LastErrorValue=66;return 88;}
static void fresh(void){
 memset(windows,0,sizeof(windows));
 windows[0]=(struct window){(HWND)1,(HWND)2,(HWND)1,3,7,WS_THICKFRAME|WS_VISIBLE,WS_EX_APPWINDOW,L"Chart",L"HwndWrapper[Starter;;test]",1,(HANDLE)100,(HANDLE)100,(HANDLE)100};
 windows[1]=(struct window){(HWND)2,0,(HWND)2,3,7,WS_VISIBLE,0,L"AMP Quantower",L"HwndWrapper[Starter;;bar]",1,0,0,0};
 move_option=L"1";trace_option=0;queries=env_queries=dwp_a=dwp_w=app_calls=group_arms=trace_lines=trace_entries=trace_exits=trace_caps=0;
 qtk_caption_trace_count=qtk_caption_trace_started=qtk_caption_trace_parent=qtk_caption_trace_depth=0;qtk_caption_trace_start_tick=0;
 dpi=(HANDLE)11;tick=1;teb.LastErrorValue=44;cases++;
}
static struct win_proc_params params(void){return (struct win_proc_params){(HWND)1,WM_NCLBUTTONDOWN,HTCAPTION,0,application,application,application,22,0,0,0};}
static void expect_pass(struct win_proc_params *p){LRESULT r=dispatch_win_proc_params(p);CHECK(r==88);CHECK(app_calls==1);CHECK(!dwp_a&&!dwp_w);CHECK(dpi==(HANDLE)11);CHECK(teb.LastErrorValue==66);}
/* Deliberately models custom controls sharing the native HTCAPTION route.
 * Candidate interception prevents this logical application callback: known regression. */
static unsigned custom_button_activations;
static LRESULT custom_caption_application(HWND h,UINT m,WPARAM w,LPARAM l){(void)h;if(m==161&&w==2&&l==1234)custom_button_activations++;return 88;}
static LRESULT nested_application(HWND h,UINT m,WPARAM w,LPARAM l){LRESULT r;app_calls++;if(!nested_calls++){call_window_proc(h,WM_EXITSIZEMOVE,w,l,&r,application);}teb.LastErrorValue=77;return 79;}
int main(void){struct win_proc_params p;const WCHAR*bad[]={0,L"",L"0",L"01",L"true",L"1 "};unsigned i;LONG parent;UINT depth;LRESULT r;
 for(i=0;i<ARRAY_SIZE(bad);i++){fresh();p=params();move_option=bad[i];expect_pass(&p);CHECK(group_arms==1);}
 fresh();p=params();r=dispatch_win_proc_params(&p);CHECK(r==72&&dwp_w==1&&!app_calls&&!group_arms);CHECK(observed_dpi==22&&dpi==(HANDLE)11);CHECK(teb.LastErrorValue==55);
 fresh();p=params();p.ansi=1;p.ansi_dst=1;r=dispatch_win_proc_params(&p);CHECK(r==71&&dwp_a==1&&!app_calls);CHECK(teb.LastErrorValue==55);
 fresh();p=params();windows[0].title=L"Group";CHECK(dispatch_win_proc_params(&p)==72&&!app_calls);
 fresh();p=params();windows[0].style|=WS_MAXIMIZE;CHECK(dispatch_win_proc_params(&p)==72&&!app_calls);
 fresh();p=params();windows[0].movable=0;CHECK(dispatch_win_proc_params(&p)==0&&!dwp_a&&!dwp_w&&!app_calls);CHECK(teb.LastErrorValue==44&&dpi==(HANDLE)11);
 for(i=0;i<3;i++){fresh();p=params();if(i==0)windows[0].whole=0;if(i==1)windows[0].backend=(HANDLE)99;if(i==2)windows[0].backend=0;expect_pass(&p);}
 for(i=0;i<26;i++){fresh();p=params();switch(i){
 case 0:windows[0].title=L"Chart Settings";break;case 1:windows[0].class_name=L"Other";break;
 case 2:windows[0].style|=WS_CHILD;break;case 3:windows[0].style|=WS_POPUP;break;
 case 4:windows[0].style&=~WS_THICKFRAME;break;case 5:windows[0].style&=~WS_VISIBLE;break;
 case 6:windows[0].style|=WS_DISABLED;break;case 7:windows[0].style|=WS_MINIMIZE;break;
 case 8:windows[0].exstyle|=WS_EX_TOOLWINDOW;break;case 9:windows[0].exstyle|=WS_EX_NOACTIVATE;break;
 case 10:windows[0].exstyle|=WS_EX_DLGMODALFRAME;break;case 11:windows[0].exstyle&=~WS_EX_APPWINDOW;break;
 case 12:windows[0].owner=0;break;case 13:windows[1].title=L"AMP QuantowerX";break;
 case 14:windows[1].owner=(HWND)1;break;case 15:windows[1].tid=8;break;
 case 16:windows[1].enabled=0;break;case 17:windows[0].root=(HWND)2;break;
 case 18:windows[0].pid=4;break;case 19:windows[0].tid=8;break;case 20:windows[1].pid=4;break;case 21:windows[1].class_name=L"Other";break;case 22:windows[1].style&=~WS_VISIBLE;break;case 23:windows[1].root=(HWND)1;break;case 24:windows[0].title=L"AMP Quantower";break;case 25:windows[0].title=L"Synthetic Preview";break;}expect_pass(&p);}
 {UINT msgs[]={WM_NCLBUTTONUP,WM_NCLBUTTONDBLCLK,WM_NCHITTEST,WM_SYSCOMMAND,561,562,533,31,256,512};for(i=0;i<ARRAY_SIZE(msgs);i++){fresh();p=params();p.msg=msgs[i];expect_pass(&p);CHECK(queries==0&&env_queries==(unsigned)qtk_caption_trace_message(p.msg,p.wparam));}}
 fresh();p=params();p.wparam=3;expect_pass(&p);CHECK(env_queries==1&&queries==0);
 /* Distinct native button/resize hit codes still reach the application.
  * These controls deliberately do not model custom buttons sharing HTCAPTION. */
 {WPARAM hits[]={0,1,8,9,10,11,12,13,14,15,16,17,18,19,20,21};for(i=0;i<ARRAY_SIZE(hits);i++){fresh();p=params();p.wparam=hits[i];expect_pass(&p);CHECK(group_arms==0&&env_queries==1&&queries==0);}}
 for(i=0;i<ARRAY_SIZE(bad);i++){fresh();trace_option=bad[i];parent=0;depth=0;teb.LastErrorValue=49;CHECK(!qtk_caption_trace_begin((HWND)1,WM_NCLBUTTONDOWN,2,0,0,&parent,&depth));CHECK(!trace_lines&&teb.LastErrorValue==49);}
 fresh();p=params();p.procA=WINPROC_PROC16;CHECK(qtk_caption_action(&p)==QTK_CAPTION_PASS&&teb.LastErrorValue==44);
 fresh();p=params();p.procW=WINPROC_PROC16;CHECK(qtk_caption_action(&p)==QTK_CAPTION_PASS&&teb.LastErrorValue==44);
 fresh();trace_option=L"1";teb.LastErrorValue=48;parent=0;depth=0;CHECK(qtk_caption_trace_begin((HWND)1,512,0,123,0,&parent,&depth)==0);CHECK(env_queries==0&&queries==0&&trace_lines==0&&teb.LastErrorValue==48);
 {WPARAM hits[]={2,(WPARAM)0x12345678,(WPARAM)(INT_PTR)-2,21,22};int expected[]={2,0,-2,21,0};for(i=0;i<ARRAY_SIZE(hits);i++){fresh();trace_option=L"1";parent=0;depth=0;LONG call=qtk_caption_trace_begin((HWND)1,161,hits[i],0x12345678,0,&parent,&depth);qtk_caption_trace_end(call,parent,depth,(HWND)1,161,0,0);CHECK(trace_entries==1&&trace_exits==1&&trace_hit[0]==expected[i]);}}
 fresh();trace_option=L"1";nested_calls=0;p=params();p.msg=WM_EXITSIZEMOVE;p.procW=nested_application;CHECK(dispatch_win_proc_params(&p)==79);CHECK(trace_entries==2&&trace_exits==2&&trace_lines==4);CHECK(trace_parent[0]==0&&trace_depth[0]==0&&trace_parent[1]==trace_call[0]&&trace_depth[1]==1);CHECK(teb.LastErrorValue==77&&qtk_caption_trace_parent==0&&qtk_caption_trace_depth==0);
 fresh();trace_option=L"1";for(i=0;i<1030;i++){parent=0;depth=0;teb.LastErrorValue=47;LONG c=qtk_caption_trace_begin((HWND)1,WM_EXITSIZEMOVE,0,0,0,&parent,&depth);qtk_caption_trace_end(c,parent,depth,(HWND)1,WM_EXITSIZEMOVE,0,0);CHECK(teb.LastErrorValue==47);}CHECK(trace_entries==1024&&trace_exits==1024&&trace_caps==1&&trace_lines==2049);
 fresh();trace_option=L"1";parent=0;depth=0;LONG c=qtk_caption_trace_begin((HWND)1,561,0,0,0,&parent,&depth);qtk_caption_trace_end(c,parent,depth,(HWND)1,561,0,0);tick=600001;CHECK(!qtk_caption_trace_begin((HWND)1,562,0,0,0,&parent,&depth));CHECK(trace_lines==2);
 fresh();p=params();p.procW=custom_caption_application;p.lparam=1234;custom_button_activations=0;CHECK(dispatch_win_proc_params(&p)==72&&dwp_w==1&&custom_button_activations==0); /* Expected broken control, not preservation. */
 printf("user32 actual-function cases=%u assertions=%u PASS known_custom_caption_button_regression=demonstrated\n",cases,assertions);return 0;}
