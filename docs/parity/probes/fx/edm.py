exec(open('mb.py').read().split("HDR=")[0])
H='<scene version="1.1"><project width="128" height="128" fps="10" duration="1" background="#303030"/><assets><image id="tc" src="testcard.png" width="96" height="96"/></assets><composition><layer id="bg" asset="tc" x="0" y="0" scaleX="1.33333" scaleY="1.33333"/><adjustment id="n" effects="e"/></composition><effects>{fx}</effects></scene>'
for t,a in [('chromatic-aberration','amount="8"'),('rgb-split','offsetX="12" offsetY="6"'),('wave-warp','amount="8" size="32"'),('lens-distortion','amount="2"'),('twirl','angle="60" radius="60"'),('pixelate','size="12"'),('vhs','amount="1" seed="3"')]:
    for m in (1,2):
        cmp(f'emf_{t}_{m}',H.format(fx=f'<effect id="e" type="{t}" {a}><param name="edgeMode" value="{m}"/></effect>'),0.5)
