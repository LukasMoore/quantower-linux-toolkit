/* Original toolkit test boundary/cases; MIT license, see repository LICENSE. */
static unsigned int checks,cases;
#define CHECK(v) do {checks++;if(!(v)){fprintf(stderr,"FAIL %s:%d: %s\n",__func__,__LINE__,#v);return 1;}}while(0)
#define FS (1u<<NET_WM_STATE_FULLSCREEN)
#define MAX (1u<<NET_WM_STATE_MAXIMIZED)
static struct x11drv_win_data fresh(void)
{
 struct x11drv_win_data d={0};option="1";win_style=WS_VISIBLE;win_exstyle=0;win_owner=0;
 virtual_desktop=desktop_fullscreen=FALSE;support_handlers=xinerama_ret=TRUE;thread_data.net_wm_state_mask=~0u;
 fs_messages=other_messages=monitor_messages=state_properties=monitor_properties=xinerama_calls=0;serial_counter=100;last_property_state=0;
 d.hwnd=10;d.whole_window=20;d.managed=TRUE;d.is_fullscreen=TRUE;
 d.desired_state.wm_state=d.pending_state.wm_state=d.current_state.wm_state=NormalState;
 d.rects.window=d.rects.visible=(RECT){0,0,5120,2880};d.current_state.rect=d.rects.visible;
 for(unsigned i=0;i<4;i++)d.desired_state.monitors.indices[i]=d.pending_state.monitors.indices[i]=d.current_state.monitors.indices[i]=-1;
 return d;
}
static int test_auto_promotion(void)
{
 struct x11drv_win_data d=fresh();update_net_wm_states(&d);
 CHECK(fs_messages==(CANDIDATE?0u:1u));CHECK(monitor_messages==(CANDIDATE?0u:1u));
 CHECK(xinerama_calls==(CANDIDATE?0u:1u));CHECK(d.is_fullscreen==TRUE);cases++;return 0;
}
static int test_exact_option_and_scope(void)
{
 const char *values[]={NULL,"","0","true","01","1 "," 1","2","1"};
 for(unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++){
  struct x11drv_win_data d=fresh();option=values[i];update_net_wm_states(&d);
  CHECK(fs_messages==((CANDIDATE&&i==8)?0u:1u));
 }
#if CANDIDATE
 for(unsigned i=0;i<5;i++){
  struct x11drv_win_data d=fresh();
  if(i==0)d.managed=FALSE;
  if(i==1)d.embedded=TRUE;
  if(i==2)d.whole_window=0;
  if(i==3)d.whole_window=root_window;
  if(i==4)virtual_desktop=TRUE;
  CHECK(!use_native_window_placement(&d));
 }
#endif
 cases++;return 0;
}
static int test_withdrawn_iconic_and_locks(void)
{
 struct x11drv_win_data d=fresh();d.pending_state.wm_state=WithdrawnState;
 window_set_net_wm_state(&d,FS);CHECK(state_properties==(CANDIDATE?0u:1u));
 d=fresh();d.pending_state.wm_state=WithdrawnState;d.current_state.net_wm_state=FS;
 window_set_net_wm_state(&d,0);CHECK((d.desired_state.net_wm_state&FS)==(CANDIDATE?FS:0u));
 if(CANDIDATE)CHECK(last_property_state&FS);
 d=fresh();d.pending_state.wm_state=IconicState;d.current_state.net_wm_state=FS;d.pending_state.net_wm_state=FS;
 window_set_net_wm_state(&d,0);CHECK(fs_messages==0);CHECK(state_properties==0);
 d=fresh();d.state_locks=1;window_set_net_wm_state(&d,FS);CHECK(fs_messages==0);
 d.state_locks=0;window_request_desired_state(&d);CHECK(fs_messages==(CANDIDATE?0u:1u));cases++;return 0;
}
static int test_monitor_sender_and_replay(void)
{
 struct x11drv_win_data d=fresh();struct monitor_indices m={7,{1,1,1,1}};
 d.current_state.net_wm_state=d.pending_state.net_wm_state=d.desired_state.net_wm_state=FS;
 window_set_net_wm_fullscreen_monitors(&d,&m);
 CHECK(monitor_messages==(CANDIDATE?0u:1u));CHECK(d.desired_state.monitors.generation==(CANDIDATE?0u:7u));
 d=fresh();d.current_state.net_wm_state=d.pending_state.net_wm_state=d.desired_state.net_wm_state=FS;
 d.pending_state.wm_state=WithdrawnState;window_set_net_wm_fullscreen_monitors(&d,&m);
 CHECK(monitor_properties==(CANDIDATE?0u:1u));
 d=fresh();d.current_state.net_wm_state=d.pending_state.net_wm_state=d.desired_state.net_wm_state=FS;
 d.desired_state.monitors=m;
 window_request_desired_state(&d);CHECK(monitor_messages==(CANDIDATE?0u:1u));cases++;return 0;
}
static int test_host_fullscreen_entry_exit_and_pending_divergence(void)
{
 struct x11drv_win_data d=fresh();window_net_wm_state_notify(&d,110,FS);
 CHECK(d.current_state.net_wm_state&FS);CHECK(fs_messages==0);
 window_net_wm_state_notify(&d,120,0);CHECK(!(d.current_state.net_wm_state&FS));
 update_net_wm_states(&d);CHECK(fs_messages==(CANDIDATE?0u:1u));
 d=fresh();d.current_state.net_wm_state=FS;
 for(unsigned i=0;i<5;i++)window_set_net_wm_state(&d,0);
 CHECK(fs_messages==0);CHECK((d.desired_state.net_wm_state&FS)==(CANDIDATE?FS:0u));
 d=fresh();d.pending_state.net_wm_state=FS;
 for(unsigned i=0;i<5;i++)window_set_net_wm_state(&d,FS);
 CHECK(fs_messages==0);CHECK((d.desired_state.net_wm_state&FS)==(CANDIDATE?0u:FS));
 d=fresh();d.net_wm_state_serial=200;window_net_wm_state_notify(&d,150,FS);
 CHECK(d.net_wm_state_serial==200);CHECK(d.current_state.net_wm_state&FS);
 window_request_desired_state(&d);CHECK(fs_messages==0);cases++;return 0;
}
static int test_maximize_minimize_and_other_states(void)
{
 struct x11drv_win_data d=fresh();win_style|=WS_MAXIMIZE;update_net_wm_states(&d);
 CHECK((d.desired_state.net_wm_state&MAX)==(CANDIDATE?MAX:0u));
 d=fresh();win_style|=WS_MINIMIZE;d.desired_state.net_wm_state=FS|MAX;
 d.current_state.net_wm_state=FS;d.pending_state.wm_state=IconicState;update_net_wm_states(&d);
 CHECK(d.desired_state.net_wm_state&MAX);CHECK(fs_messages==0);
 d=fresh();d.current_state.net_wm_state=MAX;
 CHECK(window_update_client_state(&d)==(CANDIDATE?SC_MAXIMIZE:0u));
 d=fresh();win_style|=WS_MAXIMIZE;CHECK(window_update_client_state(&d)==(CANDIDATE?SC_RESTORE:0u));
 d=fresh();win_style|=WS_MINIMIZEBOX;d.current_state.wm_state=IconicState;
 CHECK(window_update_client_state(&d)==SC_MINIMIZE);
 d=fresh();win_exstyle=WS_EX_TOPMOST|WS_EX_TOOLWINDOW;update_net_wm_states(&d);
 CHECK(d.desired_state.net_wm_state&(1u<<NET_WM_STATE_ABOVE));
 CHECK(d.desired_state.net_wm_state&(1u<<NET_WM_STATE_SKIP_TASKBAR));
 CHECK(d.desired_state.net_wm_state&(1u<<NET_WM_STATE_SKIP_PAGER));cases++;return 0;
}
static int test_native_config_geometry_acceptance(void)
{
 struct x11drv_win_data d=fresh();d.rects.window=d.rects.visible=(RECT){-1,-1,5121,2881};
 d.current_state.rect=(RECT){0,0,5120,2880};
 UINT flags=window_update_client_config(&d);
 CHECK(flags==(CANDIDATE?(SWP_NOACTIVATE|SWP_NOZORDER):0u));
 CHECK(xinerama_calls==(CANDIDATE?0u:2u));
 d=fresh();d.is_fullscreen=FALSE;d.rects.window=d.rects.visible=(RECT){0,0,1000,800};
 d.current_state.rect=(RECT){100,100,1100,900};
 CHECK(window_update_client_config(&d)==(SWP_NOACTIVATE|SWP_NOZORDER|SWP_NOSIZE));
 d.configure_serial=123;CHECK(window_update_client_config(&d)==0);
 d.configure_serial=0;d.state_locks=1;d.current_state.net_wm_state=MAX;
 CHECK(window_update_client_config(&d)&SWP_NOSENDCHANGING);cases++;return 0;
}
static int test_resizability_and_pending_config_boundaries(void)
{
 struct x11drv_win_data d=fresh();CHECK(is_window_resizable(&d,win_style));
 d.is_fullscreen=FALSE;CHECK(!is_window_resizable(&d,win_style));
 d.is_resizable=TRUE;CHECK(is_window_resizable(&d,win_style));
 d=fresh();d.wm_state_serial=123;window_set_net_wm_state(&d,FS);
 CHECK(fs_messages==0);CHECK(d.wm_state_serial==123);
 d=fresh();d.configure_serial=123;d.pending_state.mwm_hints.decorations=1;
 window_set_net_wm_state(&d,FS);CHECK(fs_messages==0);CHECK(d.configure_serial==123);
 cases++;return 0;
}
int main(void)
{
 if(test_auto_promotion()||test_exact_option_and_scope()||test_withdrawn_iconic_and_locks()||
 test_monitor_sender_and_replay()||test_host_fullscreen_entry_exit_and_pending_divergence()||
 test_maximize_minimize_and_other_states()||test_native_config_geometry_acceptance()||test_resizability_and_pending_config_boundaries())return 1;
 printf("PASS %s: %u actual-function cases, %u assertions; no display or app actions\n",CANDIDATE?"candidate":"baseline",cases,checks);
 return 0;
}
