"""Буквы Глеба из его настоящих записей на скриншотах → horo/fonts/gleb_live.npz (ставит hand.py, раньше алфавита).
LIVE_SRC=<папка со скринами> python3 horo/live/build.py horo/fonts/gleb_live.npz"""
import os, sys, numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from samples import S
import os
SRC = os.environ.get('LIVE_SRC', '.')   # папка с его скриншотами (лежат в Brain: raw/images/hand_*), имена как в samples.py
cache = {}
def inkf(f):
    if f not in cache:
        a = np.asarray(Image.open(os.path.join(SRC, f)).convert('RGB')).astype(float); r, g, b = a[..., 0], a[..., 1], a[..., 2]
        cache[f] = np.clip(((b - r) - 40) / 80, 0, 1) * np.clip((b - 120) / 80, 0, 1)   # сила синих чернил 0..1
    return cache[f]
Z=2
def extract(f,ch,x0,x1,base,xh,ymax=None):
    s=inkf(f)
    y0,y1=max(0,int(base-2.8*xh)),int(ymax or base+1.6*xh)
    sub=s[y0:y1,x0:x1]
    m=sub>0.3
    lab,n=ndimage.label(m,structure=np.ones((3,3)))
    if n==0: return None
    sizes=ndimage.sum(m,lab,range(1,n+1))
    cb=lab[max(0,int(base-y0-xh)):int(base-y0)]   # основная часть буквы — в полосе строчной своей строки
    core=[(cb==i).sum() for i in range(1,n+1)]
    main=1+int(np.argmax(core))
    ys,xs=np.nonzero(lab==main)
    keep=lab==main
    for i in range(1,n+1):
        if i==main or sizes[i-1]<4: continue
        yy,xx=np.nonzero(lab==i)
        if xs.min()-6<=xx.mean()<=xs.max()+6 and yy.max()<ys.max() and yy.min()>=ys.min()-xh*0.9 and sizes[i-1]<0.5*sizes[main-1]:
            keep|=lab==i   # точка над i, черта t
    W=sub.shape[1]
    for i in range(1,n+1):   # кусок соседней буквы у края — убрать
        if i==main: continue
        yy,xx=np.nonzero(lab==i)
        if (xx.min()==0 or xx.max()==W-1) and sizes[i-1]<0.4*sizes[main-1]: keep&=lab!=i
    keep=ndimage.binary_dilation(keep,iterations=1)
    a=np.where(keep,np.clip((sub-0.15)/0.55,0,1),0)
    rows=np.nonzero(a.max(1)>0.05)[0]; cols=np.nonzero(a.max(0)>0.05)[0]
    a=a[rows[0]:rows[-1]+1, cols[0]:cols[-1]+1]
    a=np.clip(ndimage.zoom(a,Z,order=1),0,1)
    top=(base-(y0+rows[0]))/xh
    px=1/(xh*Z)
    return a,0.0,top,px
if __name__=='__main__':
    out={}; cnt={}
    for f,ch,*rest in S:
        r=extract(f,ch,*rest)
        if r is None: print('пусто',ch,x0); continue
        a,left,top,px=r
        n=cnt.get(ch,0); cnt[ch]=n+1
        out[f'{ord(ch)}_{n}s']=(a*255).astype(np.uint8)
        out[f'{ord(ch)}_{n}m']=np.array([left,top,px],np.float32)
    np.savez_compressed(sys.argv[1],**out); print(cnt)
