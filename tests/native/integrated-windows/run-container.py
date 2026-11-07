#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Own-window smoke test in a new networkless container, prefix and Xvfb."""
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys

def main():
    if (not Path('/.dockerenv').is_file() or os.getuid() == 0 or
        os.getenv('QTK_SYNTHETIC_DATAONLY') != '1' or
        {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise RuntimeError('Explicit non-root networkless data-only container required.')
    root = Path('/test/results')
    if root.exists() or root.is_symlink():
        raise FileExistsError('Use new results; never reuse a prefix.')
    os.umask(0o077)
    root.mkdir(mode=0o700)
    read, write = os.pipe()
    with (root/'xvfb.log').open('xb') as log:
        subprocess.Popen(['Xvfb','-displayfd',str(write),'-screen','0','1920x1080x24',
                          '-nolisten','tcp','-noreset'],pass_fds=(write,),stdout=log,stderr=subprocess.STDOUT)
    os.close(write)
    if not select.select([read],[],[],20)[0]:
        raise RuntimeError('Own display startup failed.')
    number = os.read(read,32).decode().strip()
    os.close(read)
    if not number.isdecimal():
        raise RuntimeError('Invalid own display number.')
    wine = '/runtime/wine/bin/wine'
    env = {'PATH':'/runtime/wine/bin:'+os.defpath,'DISPLAY':':'+number,
           'WINEPREFIX':str(root/'prefix'),'WINEARCH':'win64','WINEDEBUG':'-all,err+all,warn+all',
           'WINEDLLOVERRIDES':'winemenubuilder.exe=d;mscoree,mshtml=d;winex11.drv=b;winewayland.drv=d',
           'LD_LIBRARY_PATH':'/runtime/x11/lib:/runtime/deps:/runtime/wine/lib',
           'XLOCALEDIR':'/runtime/x11/share/X11/locale',
           'QUANTOWER_LAUNCH_SESSION':os.urandom(16).hex()}
    for key in ('HOME','USER','LOGNAME'):
        if key in os.environ:
            env[key]=os.environ[key]
    with (root/'wineboot.log').open('xb') as log:
        subprocess.run([wine,'wineboot.exe','--init'],env=env,stdout=log,stderr=subprocess.STDOUT,
                       check=True,timeout=180)
    report={'synthetic_only':True,'display_kind':'own-Xvfb','wm_present':False,'cases':[]}
    for dpi in (96,192):
        for key in (r'HKCU\Control Panel\Desktop',r'HKCU\Software\Wine\Fonts'):
            with (root/'dpi.log').open('ab') as log:
                subprocess.run([wine,'reg.exe','add',key,'/v','LogPixels','/t','REG_DWORD','/d',str(dpi),'/f'],
                               env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
        # Let all own Wine processes finish so the next invocation reads the new DPI.
        with (root/'wait.log').open('ab') as log:
            subprocess.run(['/runtime/wine/bin/wineserver','-w'],env=env,stdout=log,stderr=subprocess.STDOUT,
                           check=True,timeout=90)
        for awareness in range(3):
            # Fresh credential per actual fixture. Prior prefix helpers must never
            # supply the current fixture's backend evidence.
            env['QUANTOWER_LAUNCH_SESSION']=os.urandom(16).hex()
            path=root/f'dpi-{dpi}-awareness-{awareness}.log'
            with path.open('xb') as log:
                child=subprocess.Popen([wine,'/probe/window-probe.exe',str(awareness)],env=env,stdout=log,stderr=subprocess.STDOUT)
                mapped=[]
                identities=set()
                import time
                deadline=time.monotonic()+20
                while child.poll() is None and time.monotonic()<deadline:
                    for proc in Path('/proc').iterdir():
                        if not proc.name.isdecimal(): continue
                        try:
                            entries=(proc/'environ').read_bytes().split(b'\0')
                            if os.fsencode('QUANTOWER_LAUNCH_SESSION='+env['QUANTOWER_LAUNCH_SESSION']) in entries:
                                arguments=(proc/'cmdline').read_bytes().lower().replace(b'\\',b'/')
                                if b'window-probe.exe' not in arguments:
                                    continue
                                before=(proc/'stat').read_text()
                                born=before[before.rfind(')')+2:].split()[19]
                                executable=str((proc/'exe').readlink())
                                if executable not in ('/runtime/wine/bin/wine',
                                        '/runtime/wine/lib/wine/x86_64-unix/wine',
                                        '/runtime/wine/lib/wine/x86_64-unix/wine-preloader'):
                                    continue
                                maps=(proc/'maps').read_text()
                                after=(proc/'stat').read_text()
                                if after[after.rfind(')')+2:].split()[19] != born:
                                    continue
                                identities.add((int(proc.name),int(born),executable))
                                mapped += [line.rsplit(' ',1)[-1] for line in maps.splitlines()
                                           if 'winex11.so' in line or 'winewayland.so' in line]
                        except (OSError,ProcessLookupError): pass
                    time.sleep(.1)
                code=child.wait(timeout=5)
            rows=[json.loads(line) for line in path.read_text(errors='replace').splitlines() if line.startswith('{')]
            result=rows[-1] if rows else {}
            backend=any('winex11.so' in name for name in mapped) and not any('winewayland.so' in name for name in mapped)
            report['cases'].append({'dpi':dpi,'awareness':awareness,'exit_code':code,
                'result':result,'x11_mapped_only':backend,'mapped_drivers':sorted(set(mapped)),
                'exact_fixture_identities':sorted(identities)})
            (root/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    env['DISPLAY']=':65530'
    env['QUANTOWER_LAUNCH_SESSION']=os.urandom(16).hex()
    with (root/'invalid-display.log').open('xb') as log:
        denied=subprocess.run([wine,'/probe/window-probe.exe','0'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=20)
    denied_rows=[json.loads(line) for line in (root/'invalid-display.log').read_text(errors='replace').splitlines() if line.startswith('{')]
    report['invalid_display']={'exit_code':denied.returncode,'result':denied_rows[-1] if denied_rows else None}
    report['invalid_display_refused']=(denied.returncode!=0 and bool(denied_rows) and
        denied_rows[-1].get('kind')=='creation-failed' and denied_rows[-1].get('wayland_loaded') is False)
    report['fixture_sha256']=hashlib.sha256(Path('/probe/window-probe.exe').read_bytes()).hexdigest()
    report['win32u_sha256']=hashlib.sha256(Path('/runtime/wine/lib/wine/x86_64-unix/win32u.so').read_bytes()).hexdigest()
    report['passed']=report['invalid_display_refused'] and all(r['exit_code']==0 and r['result'].get('failures')==0 and r['x11_mapped_only'] for r in report['cases'])
    (root/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'cases':len(report['cases']),'passed':report['passed']}))
    return 0 if report['passed'] else 1

if __name__=='__main__':
    sys.exit(main())
