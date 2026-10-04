exec(open('mb.py').read().split("HDR=")[0])
H='<scene version="1.1"><project width="128" height="128" fps="10" duration="1" background="#303030"/><assets><image id="tc" src="testcard.png" width="96" height="96"/></assets><composition>{body}</composition><effects>{fx}</effects></scene>'
fx='<effect id="e" type="drop-shadow" radius="8" offsetX="10" offsetY="12" color="#FF0000FF"/>'
cmp('o3a',H.format(body='<shape id="m" shape="ellipse" x="30" y="30" width="60" height="60" fill="#FFFFFF" visible="false"/><layer id="n" asset="tc" x="16" y="16" effects="e" matte="m"/>',fx=fx),0.5)
cmp('o3b',H.format(body='<shape id="m" shape="ellipse" x="30" y="30" width="60" height="60" fill="#FFFFFF"/><layer id="n" asset="tc" x="16" y="16" effects="e" matte="m"/>',fx=fx),0.5)
cmp('o3c',H.format(body='<shape id="m" shape="ellipse" x="30" y="30" width="60" height="60" fill="#FFFFFF"/><layer id="n" asset="tc" x="16" y="16" matte="m"/>',fx=fx),0.5)
