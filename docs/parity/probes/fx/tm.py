exec(open('mb.py').read().split("HDR=")[0])
H='<scene version="1.1"><project width="128" height="128" fps="10" duration="1" background="#303030"/><assets><image id="tc" src="testcard.png" width="96" height="96"/></assets><composition><layer id="n" asset="tc" x="16" y="16" effects="e"/></composition><effects>{fx}</effects></scene>'
for m in ['aces','agx','filmic','reinhard','hable','pq-to-sdr']:
    cmp(f'tm_{m}',H.format(fx=f'<effect id="e" type="tonemap" tonemapper="{m}" exposure="0.5"/>'),0.5)
cmp('tm_aces_noexp',H.format(fx='<effect id="e" type="tonemap" tonemapper="aces"/>'),0.5)
cmp('tm_reinhard_noexp',H.format(fx='<effect id="e" type="tonemap" tonemapper="reinhard"/>'),0.5)
for k,fx in [('hdr_exp3','<effect id="x" type="exposure" exposure="3"/><effect id="e" type="tonemap" tonemapper="aces"/>')]:
    cmp(k,H.replace('effects="e"','effects="x e"').format(fx=fx),0.5)
