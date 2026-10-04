import json,subprocess,sys
B='/home/admin/src/rs-scene-render/target/release/scene-render'
def rs(f,t,*a):
    p=subprocess.run([B,'eval',f,'--time',str(t),'--format','json','--no-assets',*a],capture_output=True,text=True)
    out=p.stdout; i=out.find('\n{\n'); i = 0 if out.startswith('{\n') else i+1
    if i<0 or p.returncode: return None,out+p.stderr
    return json.loads(out[i:]),out[:i]
if __name__=='__main__':
    d,diag=rs(sys.argv[1],sys.argv[2],*sys.argv[3:])
    if d is None: print('ERR',diag[:600]); sys.exit()
    for n in d['nodes']:
        w=n['world']
        print(n['id'],n['kind'],'tl',round(n['timeline_time'],4),'loc',round(n.get('local_time',0),4),'src',n.get('source_time'),'op',n['opacity'],n['world_opacity'],'xy',w[4],w[5],'abcd',[round(x,4) for x in w[:4]],'text',n.get('text'),'props',n.get('props'))
    if d['transitions']: print('transitions',d['transitions'])
    if diag.strip(): print('DIAG',diag.strip().splitlines()[0][:150])
