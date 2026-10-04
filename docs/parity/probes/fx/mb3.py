exec(open('mb.py').read().split("HDR=")[0])
HDR='<scene version="1.1"><project width="160" height="96" fps="10" duration="2" background="#303030" {pa}/><assets><image id="tc" src="testcard.png" width="96" height="96"/></assets>'
pa='motionBlur="true" shutterAngle="180" shutterPhase="-90" motionBlurSamples="16"'
mvl='<animate property="x"><key time="0" value="0"/><key time="1" value="64"/></animate>'
cmp('i1',HDR.format(pa=pa)+f'<composition><layer id="a" asset="tc" x="0" y="0" scaleX="0.8" scaleY="0.8">{mvl}</layer></composition></scene>',0.5)
cmp('i2',HDR.format(pa=pa.replace('motionBlur="true"','motionBlur="false"'))+f'<composition><layer id="a" asset="tc" x="0" y="0" scaleX="0.8" scaleY="0.8">{mvl}</layer></composition></scene>',0.5)
cmp('i3',HDR.format(pa=pa)+f'<composition><layer id="bg" asset="tc" x="30" y="0"/><layer id="a" asset="tc" x="0" y="0" scaleX="0.8" scaleY="0.8" blend="multiply">{mvl}</layer></composition></scene>',0.5)
cmp('i4',HDR.format(pa=pa)+f'<composition><layer id="bg" asset="tc" x="0" y="0"/><adjustment id="adj" effects="inv"><mask type="rect" x="0" y="0" width="40" height="96"/><animate property="x"><key time="0" value="0"/><key time="1" value="80"/></animate></adjustment></composition><effects><effect id="inv" type="invert"/></effects></scene>',0.5)
cmp('i5',HDR.format(pa=pa.replace('motionBlur="true"','motionBlur="false"'))+f'<composition><layer id="bg" asset="tc" x="0" y="0"/><adjustment id="adj" effects="inv"><mask type="rect" x="0" y="0" width="40" height="96"/><animate property="x"><key time="0" value="0"/><key time="1" value="80"/></animate></adjustment></composition><effects><effect id="inv" type="invert"/></effects></scene>',0.5)
