#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Original app-free, networkless Xvfb/GDI preview differential."""
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import select
import re
import secrets
import subprocess
import sys
import time

class XImage(C.Structure):
    _fields_=[('width',C.c_int),('height',C.c_int),('xoffset',C.c_int),('format',C.c_int),('data',C.c_void_p),('byte_order',C.c_int),('bitmap_unit',C.c_int),('bitmap_bit_order',C.c_int),('bitmap_pad',C.c_int),('depth',C.c_int),('bytes_per_line',C.c_int),('bits_per_pixel',C.c_int),('red_mask',C.c_ulong),('green_mask',C.c_ulong),('blue_mask',C.c_ulong)]
class XRectangle(C.Structure):
    _fields_=[('x',C.c_short),('y',C.c_short),('width',C.c_ushort),('height',C.c_ushort)]

def configure_x():
    x=C.CDLL('libX11.so.6');e=C.CDLL('libXext.so.6')
    x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p
    x.XGetGeometry.argtypes=[C.c_void_p,C.c_ulong,C.POINTER(C.c_ulong),*[C.POINTER(C.c_int)]*2,*[C.POINTER(C.c_uint)]*4]
    x.XGetImage.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_ulong,C.c_int];x.XGetImage.restype=C.POINTER(XImage)
    x.XDestroyImage.argtypes=[C.POINTER(XImage)];x.XFree.argtypes=[C.c_void_p]
    x.XCloseDisplay.argtypes=[C.c_void_p]
    e.XShapeGetRectangles.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.POINTER(C.c_int),C.POINTER(C.c_int)];e.XShapeGetRectangles.restype=C.POINTER(XRectangle)
    return x,e

def snapshot(x,e,d,w,path):
    root=C.c_ulong();xx=C.c_int();yy=C.c_int();width=C.c_uint();height=C.c_uint();border=C.c_uint();depth=C.c_uint()
    if not x.XGetGeometry(d,w,C.byref(root),C.byref(xx),C.byref(yy),C.byref(width),C.byref(height),C.byref(border),C.byref(depth)):raise RuntimeError('Own window geometry unavailable')
    if not(0<width.value<=1200 and 0<height.value<=900):raise RuntimeError('Unexpected own fixture size')
    im=x.XGetImage(d,w,0,0,width.value,height.value,0xffffffff,2)
    if not im:raise RuntimeError('Own image unavailable')
    try:
        data=C.string_at(im.contents.data,im.contents.bytes_per_line*im.contents.height)
        path.write_bytes(data)
        result={'width':width.value,'height':height.value,'sha256':hashlib.sha256(data).hexdigest(),'stride':im.contents.bytes_per_line,'bits_per_pixel':im.contents.bits_per_pixel}
    finally:x.XDestroyImage(im)
    n=C.c_int();order=C.c_int();rects=e.XShapeGetRectangles(d,w,0,C.byref(n),C.byref(order))
    try:result['shape']=[[rects[i].x,rects[i].y,rects[i].width,rects[i].height]for i in range(n.value)]
    finally:x.XFree(rects)
    return result

def mapped_fixture(env,expected):
    matches=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():continue
        try:
            if proc.stat().st_uid!=os.getuid():continue
            argv=(proc/'cmdline').read_bytes().split(b'\0')
            if not any(v.replace(b'\\',b'/').rsplit(b'/',1)[-1]==b'preview-fixture.exe' for v in argv):continue
            before=(proc/'stat').read_text();birth=before[before.rfind(')')+2:].split()[19]
            actual=(proc/'environ').read_bytes().split(b'\0')
            if any((key+'='+env[key]).encode()not in actual for key in('WINEPREFIX','QTK_SYNTHETIC_SESSION_TOKEN')):continue
            rows=[v for v in(proc/'maps').read_text().splitlines()if v.split()[-1:] == ['/runtime/wine/lib/wine/x86_64-unix/winex11.so']]
            after=(proc/'stat').read_text();after_birth=after[after.rfind(')')+2:].split()[19]
            if not rows or birth!=after_birth:continue
            module=Path(rows[0].split()[-1]);digest=hashlib.sha256(module.read_bytes()).hexdigest()
            if digest!=expected:raise RuntimeError('Mapped module hash differs')
            matches.append({'pid':int(proc.name),'uid':os.getuid(),'start_ticks':int(birth),'prefix_authenticated':True,'session_authenticated':True,'module':str(module),'sha256':digest})
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    if len(matches)!=1:raise RuntimeError('One exact mapped original fixture required')
    return matches[0]

def main():
    if not Path('/.dockerenv').exists()or os.getuid()==0 or os.getenv('QTK_SYNTHETIC_DATAONLY')!='1':raise RuntimeError('Disposable nonroot container required')
    if {p.name for p in Path('/sys/class/net').iterdir()}!={'lo'}:raise RuntimeError('Network must be disabled')
    label,flag,expected=sys.argv[1:]
    if not re.fullmatch('[a-z][a-z0-9-]{0,55}',label)or flag not in('unset','0','1')or expected not in('broken','preserved'):
        raise ValueError('Expected a simple new label, unset/0/1 flag, and broken/preserved result')
    out=Path('/evidence')/label;out.mkdir(mode=0o700)
    r,w=os.pipe()
    with(out/'xvfb.log').open('xb')as log:subprocess.Popen(['Xvfb','-displayfd',str(w),'-screen','0','1280x960x24','-nolisten','tcp','-noreset'],pass_fds=(w,),stdout=log,stderr=log)
    os.close(w)
    if not select.select([r],[],[],15)[0]:raise RuntimeError('Own Xvfb timeout')
    number=os.read(r,32).decode().strip();os.close(r)
    if not number.isdecimal():raise RuntimeError('Bad own display')
    env={'PATH':'/runtime/wine/bin:'+os.defpath,'DISPLAY':':'+number,'WINEPREFIX':str(out/'prefix'),'WINEARCH':'win64','WINEDEBUG':'-all','WINEDLLOVERRIDES':'winemenubuilder.exe=d;mscoree,mshtml=d;winex11.drv=b;winewayland.drv=d','LD_LIBRARY_PATH':'/runtime/x11/lib:/runtime/deps:/runtime/wine/lib','XLOCALEDIR':'/runtime/x11/share/X11/locale','QTK_SYNTHETIC_DATAONLY':'1','LANG':'C','LC_ALL':'C'}
    for key in('HOME','USER','LOGNAME'):
        if key in os.environ:env[key]=os.environ[key]
    env['QTK_SYNTHETIC_SESSION_TOKEN']=secrets.token_hex(16)
    if flag!='unset':env['QTK_PRESERVE_MINIMIZED_PREVIEW']=flag
    wine='/runtime/wine/bin/wine'
    with(out/'wineboot.log').open('xb')as log:subprocess.run([wine,'wineboot.exe','--init'],env=env,stdout=log,stderr=log,timeout=90,check=True)
    x,e=configure_x();d=x.XOpenDisplay(env['DISPLAY'].encode())
    if not d:raise RuntimeError('Own X connection failed')
    report={'label':label,'optin':flag,'expected':expected,'driver_sha256':hashlib.sha256(Path('/runtime/wine/lib/wine/x86_64-unix/winex11.so').read_bytes()).hexdigest(),'app_or_host_display_used':False,'phases':{}}
    with(out/'fixture.log').open('xb')as log:
        child=subprocess.Popen([wine,'/probe/preview-fixture.exe','Z:'+str(out).replace('/','\\')],env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL)
        for phase in('normal','minimized','restored','resized','minimized-again','restored-again'):
            deadline=time.monotonic()+12
            while time.monotonic()<deadline and child.poll()is None:
                state=json.loads((out/'state.json').read_bytes())if(out/'state.json').exists()else{}
                if state.get('phase')==phase:break
                time.sleep(.03)
            else:raise RuntimeError('Own phase timeout: '+phase)
            # Fixture is paused and touches only its own window; retain pixels and shape.
            mapping=mapped_fixture(env,report['driver_sha256'])
            report['phases'][phase]={'native':state,'mapping':mapping,**snapshot(x,e,d,state['xid'],out/(phase+'.pixels'))}
            if mapped_fixture(env,report['driver_sha256'])!=mapping:raise RuntimeError('Own fixture identity changed during snapshot')
            (out/(phase+'.go')).touch()
        code=child.wait(timeout=10)
    x.XCloseDisplay(d)
    p=report['phases'];pairs=[('normal','minimized'),('normal','restored'),('resized','minimized-again'),('resized','restored-again')]
    checks={a+'->'+b:p[a]['sha256']==p[b]['sha256']and p[a]['shape']==p[b]['shape']for a,b in pairs}
    report.update(exit_code=code,checks=checks,passed=code==0 and all(v==(expected=='preserved'or'restored'in k)for k,v in checks.items()))
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'label':label,'passed':report['passed'],'checks':checks}));return 0 if report['passed']else 1

if __name__=='__main__':raise SystemExit(main())
