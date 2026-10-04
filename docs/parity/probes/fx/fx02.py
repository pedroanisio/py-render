exec(open('mb.py').read().split("HDR=")[0])
H='<scene version="1.1"><project width="128" height="128" fps="10" duration="1" background="#303030"/><assets><image id="tc" src="testcard.png" width="96" height="96"/></assets><composition><layer id="n" asset="tc" x="40" y="30" scaleX="{s}" scaleY="{s}" rotation="{r}" effects="e"/></composition><effects>{fx}</effects></scene>'
cases={
 'u_blur':(0.75,0,'<effect id="e" type="blur" radius="4"/>'),
 'u_blur_s2':(1.0,0,'<effect id="e" type="blur" radius="4"/>'),
 'u_shadow':(0.75,30,'<effect id="e" type="drop-shadow" radius="6" offsetX="10" offsetY="0" color="#000000FF"/>'),
 'u_dirblur':(0.75,40,'<effect id="e" type="directional-blur" radius="8" angle="0"/>'),
 'u_rgbsplit':(0.75,30,'<effect id="e" type="rgb-split" offsetX="6" offsetY="0"/>'),
 'u_longshadow':(0.75,30,'<effect id="e" type="long-shadow" size="20" angle="0" color="#000000FF"/>'),
 'u_bevel':(0.75,30,'<effect id="e" type="bevel" size="3" angle="20" intensity="1"/>'),
 'u_stroke':(0.75,30,'<effect id="e" type="stroke" size="4" color="#FF0000FF"/>'),
 'u_pix':(0.75,30,'<effect id="e" type="pixelate" size="8"/>'),
}
for k,(s,r,fx) in cases.items():
    cmp(k,H.format(s=s,r=r,fx=fx),0.5)
