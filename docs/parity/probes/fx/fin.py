exec(open('mb.py').read().split("HDR=")[0])
H='<scene version="1.1"><project width="128" height="128" fps="10" duration="1" background="#303030"/>{cm}<assets><image id="tc" src="testcard.png" width="96" height="96"/></assets><composition><layer id="n" asset="tc" x="16" y="16"/></composition></scene>'
cases={
 'f_none_exp1':'<colorManagement exposure="1"/>',
 'f_reinhard':'<colorManagement toneMapping="reinhard"/>',
 'f_aces':'<colorManagement toneMapping="aces" exposure="1"/>',
 'f_agx':'<colorManagement toneMapping="agx" exposure="1"/>',
 'f_filmic':'<colorManagement toneMapping="filmic" exposure="1"/>',
 'f_aces2':'<colorManagement toneMapping="aces2" exposure="1"/>',
 'f_cdl':'<colorManagement looks="lk"><look id="lk" slope="1.2,1,0.8" offset="0.02,0,0" power="1.1" saturation="0.8"/></colorManagement>',
 'f_cdl_exp_aces':'<colorManagement looks="lk" exposure="1" toneMapping="reinhard"><look id="lk" slope="1.2,1,0.8" offset="0.02,0,0" power="1.1" saturation="0.8"/></colorManagement>',
 'f_cdl_mix':'<colorManagement looks="lk"><look id="lk" slope="1.5,1,1" mix="0.5"/></colorManagement>',
}
for k,cm in cases.items(): cmp(k,H.format(cm=cm),0.5)
