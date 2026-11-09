/* Original toolkit test boundary/cases; MIT license, see repository LICENSE. */
#define CHECK(x) do{assertions++;if(!(x)){fprintf(stderr,"FAIL case=%u line=%u: %s\n",cases,__LINE__,#x);exit(1);}}while(0)
static void fresh(void){data=(struct x11drv_win_data){(HWND)1,100,TRUE,FALSE,{NormalState}};virtual_desktop=0;supported=1;backend=cap=0;style=WS_THICKFRAME;sets=removes=releases=moves=0;setenv("QTK_NATIVE_WINDOW_PLACEMENT","1",1);setenv("QTK_NATIVE_CAPTION_MOVE","1",1);cases++;}
int main(void){const char*bad[]={0,"","0","01","true","1 "};unsigned i;POINT p={11,22};LRESULT r;
 for(i=0;i<sizeof(bad)/sizeof(*bad);i++){fresh();if(bad[i])setenv("QTK_NATIVE_CAPTION_MOVE",bad[i],1);else unsetenv("QTK_NATIVE_CAPTION_MOVE");sync_native_caption_props(&data);CHECK(!backend&&!cap&&!sets);}
 for(i=0;i<sizeof(bad)/sizeof(*bad);i++){fresh();if(bad[i])setenv("QTK_NATIVE_WINDOW_PLACEMENT",bad[i],1);else unsetenv("QTK_NATIVE_WINDOW_PLACEMENT");sync_native_caption_props(&data);CHECK(!backend&&!cap&&!sets);}
 fresh();sync_native_caption_props(&data);CHECK(backend==(HANDLE)100&&cap==(HANDLE)100&&sets==2);sync_native_caption_props(&data);CHECK(sets==2&&!removes);data.whole_window=101;sync_native_caption_props(&data);CHECK(backend==(HANDLE)101&&cap==(HANDLE)101&&sets==4);
 fresh();supported=0;sync_native_caption_props(&data);CHECK(backend==(HANDLE)100&&!cap&&sets==1);supported=1;sync_native_caption_props(&data);CHECK(cap==(HANDLE)100&&sets==2);supported=0;sync_native_caption_props(&data);CHECK(!cap&&removes==1&&backend==(HANDLE)100);
 for(i=0;i<5;i++){fresh();sync_native_caption_props(&data);switch(i){case 0:data.managed=0;break;case 1:data.embedded=1;break;case 2:data.whole_window=0;break;case 3:data.whole_window=root_window;break;case 4:virtual_desktop=1;break;}sync_native_caption_props(&data);CHECK(!backend&&!cap&&removes==2);}
 fresh();sync_native_caption_props(&data);style|=WS_MAXIMIZE;r=X11DRV_SysCommand(data.hwnd,SC_MOVE+HTCAPTION,0,&p);CHECK(!r&&moves==1&&last_dir==8&&last_pos.x==11&&last_pos.y==22&&releases==1);
 fresh();sync_native_caption_props(&data);style|=WS_MAXIMIZE;r=X11DRV_SysCommand(data.hwnd,SC_MOVE,0,&p);CHECK(r==-1&&!moves&&releases==1);
 fresh();sync_native_caption_props(&data);style|=WS_MAXIMIZE;r=X11DRV_SysCommand(data.hwnd,SC_SIZE+WMSZ_RIGHT,0,&p);CHECK(r==-1&&!moves&&releases==1);
 fresh();sync_native_caption_props(&data);style|=WS_MAXIMIZE;cap=(HANDLE)99;CHECK(X11DRV_SysCommand(data.hwnd,SC_MOVE+2,0,&p)==-1&&!moves);
 fresh();sync_native_caption_props(&data);style|=WS_MAXIMIZE;setenv("QTK_NATIVE_CAPTION_MOVE","0",1);CHECK(X11DRV_SysCommand(data.hwnd,SC_MOVE+2,0,&p)==-1&&!moves);
 fresh();sync_native_caption_props(&data);style|=WS_MAXIMIZE;supported=0;CHECK(X11DRV_SysCommand(data.hwnd,SC_MOVE+2,0,&p)==-1&&!moves);
 fresh();sync_native_caption_props(&data);data.desired_state.wm_state=WithdrawnState;CHECK(X11DRV_SysCommand(data.hwnd,SC_MOVE+2,0,&p)==-1&&!moves);
 fresh();sync_native_caption_props(&data);CHECK(X11DRV_SysCommand(data.hwnd,SC_SIZE+WMSZ_RIGHT,0,&p)==0&&moves==1&&last_dir==3);
 fresh();sync_native_caption_props(&data);CHECK(X11DRV_SysCommand(data.hwnd,SC_MOVE,0,&p)==0&&moves==1&&last_dir==10);
 printf("driver actual-function cases=%u assertions=%u PASS\n",cases,assertions);return 0;}
