exec(open('mb.py').read().split("HDR=")[0])
HDR='<scene version="1.1"><project width="160" height="96" fps="10" duration="2" background="#303030" {pa}/><assets><image id="tc" src="testcard.png" width="96" height="96"/></assets>'
mv='<animate property="x"><key time="0" value="0"/><key time="1" value="80"/></animate>'
sq=lambda id,extra='',kids=mv:f'<shape id="{id}" shape="rect" x="8" y="20" width="24" height="24" fill="#FFCC00" {extra}>{kids}</shape>'
pa0='motionBlur="true" shutterAngle="0" shutterPhase="-90" motionBlurSamples="16"'
cmp('s0',HDR.format(pa=pa0)+f'<composition>{sq("a")}</composition></scene>',0.5)
pa1='motionBlur="true" shutterAngle="360" shutterPhase="-180" motionBlurSamples="16"'
cmp('s360',HDR.format(pa=pa1)+f'<composition>{sq("a")}</composition></scene>',0.5)
pa2='motionBlur="true" shutterAngle="180" shutterPhase="-90" motionBlurSamples="16" adaptiveMotionBlur="false"'
cmp('sna',HDR.format(pa=pa2)+f'<composition>{sq("a")}<layer id="s" asset="tc" x="60" y="0"/></composition></scene>',0.5)
pa3='motionBlur="true" shutterAngle="180" shutterPhase="-90" motionBlurSamples="16"'
cmp('sla',HDR.format(pa=pa3)+f'<composition>{sq("a")}</composition></scene>',1.9)
cmp('sfade',HDR.format(pa=pa3)+f'<composition><shape id="a" shape="rect" x="8" y="20" width="24" height="24" fill="#FFCC00" end="1.0">{mv}</shape></composition></scene>',0.95)
