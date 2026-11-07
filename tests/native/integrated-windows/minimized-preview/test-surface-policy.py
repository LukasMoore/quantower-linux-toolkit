#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise exact Wine surface callbacks with inert mocks; no X11 or Wine process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

BASELINE_SHA256 = '13a1fcfd2271d7f3b4da5005a04c6457354cfbe7376c1de5458a880ac76d354c'
CANDIDATE_SHA256 = '8a1f9497e6907eab62cecbfd1765dac20e98605ebb54d9570fb7096bc3e6ed01'


def extract(source, name):
    signature = ('static BOOL ' if name == 'x11drv_surface_flush' else 'BOOL ') + name + '('
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

PRE = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#pragma GCC diagnostic ignored "-Wunused-parameter"
typedef int BOOL; typedef uint32_t UINT,DWORD,ULONG; typedef int32_t LONG; typedef uintptr_t HWND,Window,GC,Pixmap,HBITMAP;
#define TRUE 1
#define FALSE 0
#define WS_MINIMIZE 0x20000000
#define GWL_STYLE -16
#define BI_RGB 0
#define DIB_RGB_COLORS 0
#define HAVE_LIBXSHAPE 1
#define ShapeBounding 0
#define ShapeSet 0
#define None 0
#define TRACE(...) ((void)0)
typedef struct { int left,top,right,bottom; } RECT;
typedef struct { struct { int biBitCount,biCompression; } bmiHeader; DWORD bmiColors[3]; } BITMAPINFO;
typedef struct { char *data; int bits_per_pixel,bytes_per_line; } XImage;
typedef struct { int depth; int visualid; } XVisualInfo;
struct gdi_image_bits {void *ptr;};
struct window_surface { const void *funcs; HWND hwnd; UINT alpha_mask,alpha_bits; };
struct x11drv_image { XImage *ximage; int shminfo; };
struct x11drv_window_surface { struct window_surface header; Window window; GC gc; struct x11drv_image *image; BOOL byteswap; LONG preserve_minimized_preview; BOOL pending_shape; };
struct x11drv_win_data { BOOL managed,embedded,layered,use_alpha; Window whole_window; HWND hwnd; XVisualInfo vis; };
static struct x11drv_window_surface surface;
static struct x11drv_win_data data;
static int funcs_token,foreign_funcs_token;
#define x11drv_surface_funcs funcs_token
static Window root_window=1;
static XVisualInfo default_visual={24,7},argb_visual={32,8};
static void *gdi_display;
static BOOL virtual_desktop,inside_flush,shm_success;
static DWORD style;
static unsigned counts[12];
enum { COPY, MAP, SHAPE, PIXMAP, FREEPIX, SHM, PUT, FLUSH, GETDATA, GETSTYLE, RELEASE, VISUAL };
static unsigned framebuffer,shape_serial;
static struct x11drv_window_surface *get_x11_surface(struct window_surface *s) {return (void*)s;}
static LONG InterlockedCompareExchange(LONG *p,LONG v,LONG expected) { LONG old=*p;if(old==expected)*p=v;return old; }
static LONG InterlockedExchange(LONG *p,LONG v) { LONG old=*p;*p=v;return old; }
static int *get_window_surface_mapping(int bpp,int *map) {counts[MAP]++;return NULL;}
static void copy_image_byteswap(const BITMAPINFO *info,const unsigned char *src,unsigned char *dst,int a,int b,int h,BOOL swap,int *map,UINT mask,UINT alpha) {counts[COPY]++;memcpy(dst,src,(size_t)b*h);}
static Pixmap create_pixmap_from_image(Window w,XVisualInfo *v,const BITMAPINFO *i,struct gdi_image_bits *b,UINT c) {counts[PIXMAP]++;return 9;}
static int XShapeCombineMask(void *d,Window w,int k,int x,int y,Pixmap p,int op) {counts[SHAPE]++;shape_serial++;return 1;}
static int XFreePixmap(void *d,Pixmap p) {counts[FREEPIX]++;return 1;}
static BOOL put_shm_image(XImage *i,int *shm,Window w,GC gc,const RECT *r,const RECT *dirty) {counts[SHM]++;if(shm_success)framebuffer++;return shm_success;}
static int XPutImage(void *d,Window w,GC gc,XImage *i,int a,int b,int c,int e,int f,int g) {counts[PUT]++;framebuffer++;return 1;}
static int XFlush(void *d) {counts[FLUSH]++;return 1;}
static struct x11drv_win_data *get_win_data(HWND h) {assert(!inside_flush);counts[GETDATA]++;return &data;}
static void release_win_data(struct x11drv_win_data *d) {assert(!inside_flush);counts[RELEASE]++;}
static DWORD NtUserGetWindowLongW(HWND h,int which) {assert(!inside_flush);counts[GETSTYLE]++;return style;}
static BOOL is_virtual_desktop(void) {assert(!inside_flush);return virtual_desktop;}
static BOOL enable_direct_drawing(struct x11drv_win_data *d,BOOL layered) {return FALSE;}
static void window_surface_release(struct window_surface *s) {}
static void set_window_visual(struct x11drv_win_data *d,XVisualInfo *v,BOOL b) {counts[VISUAL]++;}
static struct window_surface *create_surface(HWND h,Window w,const XVisualInfo *v,const RECT *r,BOOL alpha) {surface.header.funcs=&funcs_token;surface.window=w;return &surface.header;}
'''

POST = r'''
static unsigned cases;
static void reset_counts(void) {memset(counts,0,sizeof(counts));}
static BOOL flush_it(const BITMAPINFO *info,const void *bits,BOOL changed,const void *shape_bits) {
 RECT r={0,0,2,2};BOOL ret;inside_flush=TRUE;
 ret=x11drv_surface_flush(&surface.header,&r,&r,info,bits,changed,info,shape_bits);
 inside_flush=FALSE;return ret;
}
static void configure(void) {RECT r={0,0,2,2};struct window_surface *p=&surface.header;assert(X11DRV_CreateWindowSurface(99,FALSE,&r,&p));assert(p==&surface.header);}
int main(void) {
 const char *envs[]={NULL,"","0","1","01","true"," 1","1 "};
 unsigned ei,minimized,managed,embedded,vd,root,changed,shm,copy,shape_bits;
 uint32_t dst[4],src[4]={0x123456,0xabcdef,0x234567,0x345678};
 XImage image={(char*)dst,32,8};struct x11drv_image img={&image,0};
 BITMAPINFO info={{32,BI_RGB},{0,0,0}};
 for(ei=0;ei<8;ei++)for(minimized=0;minimized<2;minimized++)for(managed=0;managed<2;managed++)
 for(embedded=0;embedded<2;embedded++)for(vd=0;vd<2;vd++)for(root=0;root<2;root++)
 for(changed=0;changed<2;changed++)for(shm=0;shm<2;shm++)for(copy=0;copy<2;copy++)for(shape_bits=0;shape_bits<2;shape_bits++) {
  BOOL skip=0;unsigned before_frame,before_shape;uint32_t before[4];
  if(envs[ei])assert(!setenv("QTK_PRESERVE_MINIMIZED_PREVIEW",envs[ei],1));else assert(!unsetenv("QTK_PRESERVE_MINIMIZED_PREVIEW"));
  memset(&surface,0,sizeof(surface));surface.header.funcs=&funcs_token;surface.header.hwnd=99;
  surface.header.alpha_bits=0xff000000;surface.image=&img;surface.window=root?1:77;surface.gc=4;
  data=(struct x11drv_win_data){.managed=managed,.embedded=embedded,.whole_window=surface.window,.hwnd=99,.vis=default_visual};virtual_desktop=vd;style=minimized?WS_MINIMIZE:0;shm_success=shm;
  configure();
#ifdef CANDIDATE
  skip=(ei==3)&&minimized&&managed&&!embedded&&!vd&&!root;
  assert(surface.preserve_minimized_preview==skip);
#endif
  dst[0]=0x1;dst[1]=0x2;dst[2]=0x3;dst[3]=0x4;memcpy(before,dst,sizeof(before));
  before_frame=framebuffer;before_shape=shape_serial;reset_counts();
  assert(flush_it(&info,copy?(void*)src:(void*)dst,changed,shape_bits?src:NULL)==!skip);
  assert(!counts[GETDATA]&&!counts[GETSTYLE]&&!counts[RELEASE]);
  if(skip) {
   unsigned i;for(i=0;i<12;i++)assert(!counts[i]);assert(framebuffer==before_frame);assert(shape_serial==before_shape);assert(!memcmp(dst,before,sizeof(dst)));assert(surface.pending_shape==changed);
   /* Repeated rejected flushes retain pending shape without touching pixels. */
   assert(!flush_it(&info,dst,FALSE,NULL));assert(surface.pending_shape==changed);
   style=0;configure();reset_counts();assert(flush_it(&info,copy?(void*)src:(void*)dst,FALSE,shape_bits?src:NULL));
   assert(counts[SHAPE]==changed);assert(!surface.pending_shape);assert(framebuffer==before_frame+1);
   reset_counts();assert(flush_it(&info,dst,FALSE,NULL));assert(!counts[SHAPE]);
  } else {
   assert(framebuffer==before_frame+1);assert(shape_serial==before_shape+changed);assert(counts[SHM]==1);assert(counts[PUT]==!shm);assert(counts[FLUSH]==1);assert(counts[SHAPE]==changed);assert(counts[COPY]==copy);assert(counts[MAP]==copy);
   if(copy)assert(!memcmp(dst,src,sizeof(dst)));else assert(dst[0]==0xff000001);
  }
  cases++;
 }
 /* The disabled path must not query style; foreign/default surfaces are excluded. */
#ifdef CANDIDATE
 unsetenv("QTK_PRESERVE_MINIMIZED_PREVIEW");reset_counts();configure();assert(!counts[GETSTYLE]);
 surface.header.funcs=&foreign_funcs_token;reset_counts();configure();assert(!counts[GETSTYLE]);
#else
 (void)foreign_funcs_token;
#endif
 printf("{\"passed\":true,\"cases\":%u,\"candidate\":%s,\"host_actions\":false}\n",cases,
#ifdef CANDIDATE
 "true"
#else
 "false"
#endif
 );return 0;
}
'''

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='Pinned pre-0009 bitblt.c')
    parser.add_argument('--candidate', type=Path, required=True, help='Pinned post-0009 bitblt.c')
    parser.add_argument('--output', type=Path, required=True, help='New private directory outside this source tree')
    args = parser.parse_args(argv)
    sources = []
    for name, path, expected in [('baseline', args.baseline, BASELINE_SHA256),
                                 ('candidate', args.candidate, CANDIDATE_SHA256)]:
        if not path.is_file() or path.stat().st_size > 1048576:
            raise ValueError('Expected a bounded regular Wine source file: ' + name)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Wine source hash differs: ' + name)
        sources.append((name, data.decode('utf-8'), expected))
    output = args.output.absolute()
    if output.exists() or output.is_symlink() or output.resolve() != output:
        raise FileExistsError('Use a new canonical private output directory.')
    source_root = Path(__file__).resolve().parents[4]
    if output.is_relative_to(source_root):
        raise ValueError('Keep generated binaries and results outside the source tree.')
    compiler = shutil.which('gcc')
    if not compiler:
        raise ValueError('An existing GCC C compiler is required.')
    os.umask(0o077)
    output.mkdir(parents=True, mode=0o700)
    results = []
    for name, source, digest in sources:
        # Preserve Wine copyright and LGPL notice in generated derived test code.
        notice = source[:source.index('*/') + 2]
        functions = '\n'.join(extract(source, n) for n in
                              ('x11drv_surface_flush', 'X11DRV_CreateWindowSurface'))
        generated = output / (name + '.c')
        generated.write_text(notice + '\n' + PRE + '\n' + functions + '\n' + POST)
        binary = output / (name + '-policy')
        command = [compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                   '-Wno-sign-compare', '-Wno-unused-function', '-Wno-unused-variable']
        if name == 'candidate':
            command.append('-DCANDIDATE=1')
        with (output / ('compile-' + name + '.log')).open('xb') as log:
            subprocess.run(command + [generated.name, '-o', binary.name], cwd=output,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
        trial = subprocess.run([str(binary)], cwd=output, capture_output=True,
                               text=True, check=True, timeout=30)
        result = json.loads(trial.stdout)
        if result != {'passed': True, 'cases': 4096, 'candidate': name == 'candidate',
                      'host_actions': False}:
            raise ValueError('Unexpected actual-callback test result.')
        results.append({'variant': name, 'source_sha256': digest, 'result': result})
    report = {'passed': True, 'host_actions': False, 'actual_callbacks':
              ['x11drv_surface_flush', 'X11DRV_CreateWindowSurface'], 'results': results,
              'limits': 'Inert mocks and simplified types; no real lock, display, GPU or app acceptance.'}
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
