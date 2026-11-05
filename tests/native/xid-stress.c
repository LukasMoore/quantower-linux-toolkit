#include <X11/Xlib.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <time.h>

static Display *dpy;
static Drawable drawable;
static unsigned long count;
static atomic_ulong completed;
static atomic_uint errors;
static atomic_uint duplicate_ids;
static pthread_mutex_t live_mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned long live_ids[128];

static int error_handler(Display *d, XErrorEvent *e) {
    char message[256];
    XGetErrorText(d,e->error_code,message,sizeof(message));
    fprintf(stderr,"X error code=%d request=%d minor=%d resource=%lu %s\n",e->error_code,e->request_code,e->minor_code,e->resourceid,message);
    errors++;
    return 0;
}
static void *worker(void *unused) {
    unsigned slot = (unsigned)(unsigned long)unused;
    for(unsigned long i=0;i<count;i++) {
        GC gc=XCreateGC(dpy,drawable,0,NULL);
        if(!gc) abort();
        unsigned long id = XGContextFromGC(gc);
        pthread_mutex_lock(&live_mutex);
        for(unsigned n=0;n<128;n++) if(n!=slot && live_ids[n] && live_ids[n]==id) atomic_fetch_add(&duplicate_ids,1);
        live_ids[slot]=id;
        pthread_mutex_unlock(&live_mutex);
        XSetGraphicsExposures(dpy,gc,False);
        XSetSubwindowMode(dpy,gc,IncludeInferiors);
        XFreeGC(dpy,gc);
        pthread_mutex_lock(&live_mutex);
        live_ids[slot]=0;
        pthread_mutex_unlock(&live_mutex);
        if((i%10000)==0) { XFlush(dpy); atomic_fetch_add(&completed,10000); }
    }
    return NULL;
}
int main(int argc,char **argv) {
    int threads=argc>1?atoi(argv[1]):4;
    if(threads<1 || threads>128) return 2;
    count=argc>2?strtoul(argv[2],NULL,10):2000000;
    if(!XInitThreads()) return 2;
    dpy=XOpenDisplay(NULL);
    if(!dpy) { perror("XOpenDisplay"); return 2; }
    XSetErrorHandler(error_handler);
    drawable=DefaultRootWindow(dpy);
    fprintf(stderr,"started threads=%d iterations_each=%lu vendor=%s release=%d\n",threads,count,ServerVendor(dpy),VendorRelease(dpy));
    pthread_t *workers=calloc(threads,sizeof(*workers));
    for(int i=0;i<threads;i++) pthread_create(workers+i,NULL,worker,(void *)(unsigned long)i);
    for(int i=0;i<threads;i++) pthread_join(workers[i],NULL);
    XSync(dpy,False);
    fprintf(stderr,"PASS total=%lu protocol_errors=%u duplicate_active_ids=%u\n",threads*count,errors,duplicate_ids);
    XCloseDisplay(dpy);
    return errors||duplicate_ids?1:0;
}
