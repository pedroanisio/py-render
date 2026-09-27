"""Semantic regressions for the FULL effects; tests exercise XML and the evaluator."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from test_effects import make_renderer, tile, solid, effect, apply, CTX
from scenerender.effects import premul, straight
from scenerender.effects.color_science import bradford, pq_encode, bt2390, white_xyz
from scenerender.evaluator import Ctx
from scenerender.raster import Buf, srgb_to_linear
from scenerender.registry import EFFECTS, load_plugins

UPGRADED = '''radial-blur zoom-blur lens-blur tilt-shift pixel-motion-blur lut
white-balance tonemap gradient-map gradient-overlay selective-color displacement-map
turbulent-displace heat-haze lens-distortion fractal-noise difference-key matte-choke
halation lighting lens-flare light-leak god-rays bevel long-shadow film-grain
chromatic-aberration glitch vhs halftone echo posterize-time'''.split()


def fx(kind, params=None, **attrs):
    text=effect(kind,**attrs)
    if not params:
        return text
    return text[:-2]+'>'+''.join(f'<param name="{k}" value="{v}"/>' for k,v in params.items())+'</effect>'


def at(t):
    return Ctx(t=t,comp_t=t,frame=round(t*24))


def centroid(b):
    y,x=np.indices(b.px.shape[:2]);a=b.px[...,3]
    return np.array([np.sum((x+b.x0)*a),np.sum((y+b.y0)*a)])/max(a.sum(),1e-7)


@pytest.mark.parametrize('name',UPGRADED)
def test_upgraded_registrations(name):
    load_plugins()
    assert EFFECTS.level(name)=='full'


def test_radial_arc_and_zoom_trajectory(make_renderer):
    b=Buf.empty(0,0,65,65);b.px[30:35,48:53]=1
    radial=apply(make_renderer(effect('radial-blur',angle=60,centerX=32,centerY=32,samples=61)),b)
    y,x=np.where(radial.px[...,3]>.05)
    assert np.ptp(y)>12 and np.ptp(x)<9
    zoom=apply(make_renderer(effect('zoom-blur',amount=60,centerX=32,centerY=32,samples=61)),b)
    assert centroid(zoom)[0]>centroid(b)[0]+3
    for kind in ('radial-blur','zoom-blur'):
        out=apply(make_renderer(effect(kind,amount=0,angle=0)),b)
        np.testing.assert_allclose(out.px,b.px,atol=1e-6)


def test_lens_polygon_and_highlight_energy(make_renderer):
    b=Buf.empty(0,0,31,31);b.px[15,15]=1
    circle=apply(make_renderer(fx('lens-blur',radius=8,intensity=0)),b)
    polygon=apply(make_renderer(fx('lens-blur',{'blades':4},radius=8,intensity=0)),b)
    assert not np.allclose(circle.px,polygon.px)
    np.testing.assert_allclose(centroid(circle),centroid(polygon),atol=1e-5)
    bloom=apply(make_renderer(fx('lens-blur',radius=8,intensity=2,threshold=.4)),b)
    assert bloom.px[...,:3].sum()>circle.px[...,:3].sum()*1.5
    assert bloom.px[...,3].sum()==pytest.approx(circle.px[...,3].sum(),rel=1e-6)


def test_tilt_shift_uses_intermediate_radius(make_renderer):
    from scenerender.effects import gaussian
    b=solid((0,0,0),w=65,h=65);b.px[::2,::2,:3]=1
    r=make_renderer(effect('tilt-shift',radius=7,size=4,centerY=32,softness=.4,samples=20))
    out=apply(r,b).region(b.rect)
    sharp=b.px;maxblur=gaussian(b.px,7)
    # In the gradient, high-frequency content disappears sooner than with a
    # sharp/max-blur crossfade; the focus stripe still retains its texture.
    assert np.std(out[31:34,:,0])>np.std(out[5:9,:,0])*2
    assert np.mean(abs(out[20,:,0]-sharp[20,:,0]))>.2
    assert np.mean(abs(out[20,:,0]-maxblur[20,:,0]))<.16


def test_motion_blur_rotation_and_parent_scale(make_renderer):
    composition='''<group id="parent" x="25" y="24"><animate property="scaleX"><key time="0" value="1"/><key time="1" value="2"/></animate>
    <shape id="moving" shape="rect" x="10" width="12" height="3" fill="#FFFFFFFF">
    <animate property="rotation"><key time="0" value="-45"/><key time="1" value="45"/></animate></shape></group>'''
    r=make_renderer(effect('pixel-motion-blur',amount=12,samples=25),composition=composition)
    node=r.doc.ids['moving'];c=at(.5)
    b=r.rc.render_node_at(node,.5,c).buf
    out=apply(r,b,c,node)
    assert out.h>b.h+5
    assert np.count_nonzero(out.px[...,3]>.01)>np.count_nonzero(b.px[...,3]>.01)*2


def test_bradford_reference_and_kelvin_neutrality(make_renderer):
    gray=solid((.18,.18,.18),alpha=.6)
    for t in (0,6504):
        np.testing.assert_allclose(apply(make_renderer(effect('white-balance',temperature=t)),gray).px,gray.px,atol=1e-7)
    from scenerender.color import _rgb_to_xyz
    warm=np.linalg.solve(_rgb_to_xyz('srgb'),white_xyz(3200))*.18
    balanced=apply(make_renderer(effect('white-balance',temperature=3200)),solid(warm))
    np.testing.assert_allclose(straight(balanced.px)[0],.18,atol=1e-6)
    assert not np.allclose(bradford(6504,10),np.eye(3))


@pytest.mark.parametrize('space',['srgb','linear-srgb','rec709','display-p3','dci-p3','rec2020','acescg','aces2065-1','acescct','xyz-d65','raw'])
def test_ocio_lut_spaces_roundtrip(make_renderer,tmp_path,space):
    # CLF identity matrix has no LUT-domain clamp, including ACES and XYZ values.
    (tmp_path/'identity.clf').write_text('<ProcessList compCLFversion="3" id="identity"><Matrix inBitDepth="32f" outBitDepth="32f"><Array dim="3 3">1 0 0 0 1 0 0 0 1</Array></Matrix></ProcessList>')
    b=solid((.18,.22,.14),alpha=.6)
    np.testing.assert_allclose(apply(make_renderer(effect('lut',src='identity.clf',space=space)),b).px,b.px,atol=3e-6)


def test_ocio_shaper_plus_cube(make_renderer,tmp_path):
    import PyOpenColorIO as ocio
    rows='\n'.join(f'{r} {g} {b}' for b in (0,1) for g in (0,1) for r in (0,1))
    path=tmp_path/'shaper.cube'
    path.write_text('LUT_1D_SIZE 3\nLUT_3D_SIZE 2\n0 0 0\n.25 .25 .25\n1 1 1\n'+rows+'\n')
    cpu=ocio.Config.CreateRaw().getProcessor(ocio.FileTransform(src=str(path),interpolation=ocio.INTERP_LINEAR)).getDefaultCPUProcessor()
    rgb=np.array([.3,.5,.7],np.float32)
    expected=cpu.applyRGB(rgb.tolist())
    out=apply(make_renderer(effect('lut',src='shaper.cube',space='linear-srgb')),solid(rgb))
    np.testing.assert_allclose(straight(out.px)[0],np.broadcast_to(expected,out.px[...,:3].shape),atol=1e-6)


@pytest.mark.parametrize('mode',['aces','agx','hable','filmic','reinhard','pq-to-sdr'])
def test_tonemappers_monotonic_hdr_and_alpha(make_renderer,mode):
    x=np.linspace(0,1,128) if mode=='pq-to-sdr' else np.geomspace(.0001,100,128)
    rgb=np.repeat(x[None,:,None],3,-1)
    b=Buf(premul(rgb,np.full((1,128,1),.7)))
    out=apply(make_renderer(effect('tonemap',tonemapper=mode)),b)
    y=straight(out.px)[0]
    assert np.min(np.diff(y[0,:,0]))>=-2e-6
    assert y.max()<=1 and y.min()>=0
    np.testing.assert_allclose(out.px[...,3],.7)
    assert np.ptp(y)>.5


def test_aces_and_agx_reference_processors(make_renderer):
    import PyOpenColorIO as ocio
    from scenerender.effects.color_science import ocio_config, apply_cpu, agx_processor
    rgb=np.array([[[.18,.18,.18],[4,.3,.1],[.1,2,.05]]],np.float32)
    b=Buf(premul(rgb,np.ones((1,3,1))))
    t=ocio.DisplayViewTransform(src='Linear Rec.709 (sRGB)',display='sRGB - Display',view='ACES 2.0 - SDR 100 nits (Rec.709)')
    expected=np.clip(srgb_to_linear(apply_cpu(ocio_config().getProcessor(t).getDefaultCPUProcessor(),rgb)),0,1)
    np.testing.assert_allclose(apply(make_renderer(effect('tonemap',tonemapper='aces')),b).px[...,:3],expected,atol=2e-6)
    # Published neutral pivot of Blender's base formation is 0.18 linear.
    neutral=apply(make_renderer(effect('tonemap',tonemapper='agx')),solid((.18,.18,.18)))
    np.testing.assert_allclose(neutral.px[...,:3],.18,atol=.001)
    assert agx_processor() is agx_processor()


def test_bt2390_preserves_low_light_and_maps_peak():
    np.testing.assert_allclose(bt2390(pq_encode(np.array([.1,1,1000]))),[.001,.01,1],atol=1e-6)
    values=bt2390(pq_encode(np.array([10,100,300,1000])))
    assert np.all(np.diff(values)>0)


def test_gradient_overlay_angle_and_alpha(make_renderer):
    paint='<paints><linearGradient id="ramp"><stop offset="0" color="#FF0000FF"/><stop offset="1" color="#0000FFFF"/></linearGradient></paints>'
    b=solid(alpha=.4,w=20,h=20)
    out=apply(make_renderer(effect('gradient-overlay',paint='url(#ramp)',angle=90),paints=paint),b)
    assert out.px[0,10,0]>out.px[-1,10,0]
    assert out.px[0,10,2]<out.px[-1,10,2]
    np.testing.assert_allclose(out.px[...,3],.4)


def test_selective_cmyk_families(make_renderer):
    b=Buf(np.array([[[1,0,0,1],[0,1,0,1],[0,0,1,1],[1,1,1,1]]],np.float32))
    out=apply(make_renderer(fx('selective-color',{'family':'reds','cyan':.5})),b)
    assert out.px[0,0,0]<.5
    np.testing.assert_array_equal(out.px[0,1:],b.px[0,1:])
    whites=apply(make_renderer(fx('selective-color',{'family':'whites','black':.5})),b)
    assert whites.px[0,3,0]<.5
    np.testing.assert_array_equal(whites.px[0,:3],b.px[0,:3])


def test_displacement_neutral_and_nested_plate(make_renderer):
    comp='<group id="parent" x="20" y="10"><shape id="plate" shape="rect" width="12" height="10" fill="#FF0000FF"/></group>'
    r=make_renderer(effect('difference-key',source='plate',softness=0),composition=comp)
    assert apply(r,solid((1,0,0),x0=20,y0=10)).px[...,3].max()==0
    np.testing.assert_array_equal(apply(r,solid((1,0,0))).px,solid((1,0,0)).px)
    # An alpha of .5 is exactly representable through opacity (unlike hex RGB).
    comp='<shape id="plate" shape="rect" width="64" height="48" fill="#FFFFFFFF" opacity="0.5"/>'
    b=solid((.2,.3,.4),x0=20,y0=15)
    out=apply(make_renderer(effect('displacement-map',source='plate',channel='alpha',amount=7),composition=comp),b)
    np.testing.assert_array_equal(out.region(b.rect),b.px)
    outside=solid((.2,.3,.4),x0=90,y0=60)
    out=apply(make_renderer(effect('displacement-map',source='plate',channel='alpha',amount=7),composition=comp),outside)
    np.testing.assert_array_equal(out.region(outside.rect),outside.px)


@pytest.mark.parametrize('kind',['fractal-noise','turbulent-displace','heat-haze'])
def test_fractals_evolve_and_types_differ(make_renderer,tile,kind):
    attrs=dict(amount=8,size=15,speed=.3)
    r=make_renderer(fx(kind,{'noiseType':'basic','octaves':4},**attrs))
    a=apply(r,tile,at(.2));b=apply(r,tile,at(.8))
    assert not np.allclose(a.px,b.px)
    np.testing.assert_array_equal(a.px,apply(r,tile,at(.2)).px)
    turbulent=apply(make_renderer(fx(kind,{'noiseType':'turbulent'},**attrs)),tile,at(.2))
    assert not np.allclose(a.px,turbulent.px)


def test_distortion_coefficients_and_cylindrical(make_renderer,tile):
    np.testing.assert_array_equal(apply(make_renderer(fx('lens-distortion',{'k1':0,'k2':0})),tile).px,tile.px)
    a=apply(make_renderer(fx('lens-distortion',{'k1':0,'k2':.3})),tile)
    b=apply(make_renderer(fx('lens-distortion',{'k1':0,'k2':.3,'cylindrical':1})),tile)
    assert not np.allclose(a.px,b.px) and not np.allclose(a.px,tile.px)


def test_matte_choke_iterations_and_gray_softness(make_renderer):
    b=Buf.empty(0,0,31,31);b.px[8:23,8:23]=1
    hard=apply(make_renderer(fx('matte-choke',amount=2,softness=0)),b)
    repeated=apply(make_renderer(fx('matte-choke',{'iterations':3},amount=2,softness=0)),b)
    soft=apply(make_renderer(fx('matte-choke',amount=2,softness=1)),b)
    assert repeated.px[...,3].sum()<hard.px[...,3].sum()
    assert np.any((soft.px[...,3]>0)&(soft.px[...,3]<.99))


def test_halation_works_on_opaque_photographs(make_renderer):
    b=solid((0,0,0),w=51,h=51);b.px[23:28,23:28,:3]=1
    out=apply(make_renderer(effect('halation',radius=4,threshold=.6)),b).region(b.rect)
    assert out[25,30,0]>0
    assert out[25,30,0]>out[25,30,1]*2
    np.testing.assert_array_equal(out[...,3],b.px[...,3])


@pytest.mark.parametrize('kind',['ambient','point','spot','directional','rect-area','disk-area','sphere-area','dome'])
def test_every_light_type_kelvin_intensity(make_renderer,kind):
    lights=f'<lights><light id="lamp" type="{kind}" x="16" y="16" z="30" range="80" colorTemperature="3200"/></lights>'
    out=apply(make_renderer(effect('lighting',lights='lamp'),lights=lights),solid(w=32,h=32))
    assert out.px[16,16,0]>out.px[16,16,2]>0


def test_lighting_cast_shadow_and_spot_cone(make_renderer):
    b=solid(w=48,h=24,alpha=.1);b.px[:,20:23]=1
    def lit(shadow):
        lights=f'<lights><light id="lamp" type="point" x="-10" y="12" z="10" range="100" castShadow="{shadow}"/></lights>'
        return apply(make_renderer(effect('lighting',lights='lamp',relief=20,samples=32),lights=lights),b)
    unshadowed,shadowed=lit('false'),lit('true')
    assert shadowed.px[:,26:35,:3].sum()<unshadowed.px[:,26:35,:3].sum()*.5


@pytest.mark.parametrize('kind',['lens-flare','light-leak'])
def test_optical_artifacts_scale_color_time(make_renderer,kind):
    b=solid((0,0,0),w=40,h=30)
    r=make_renderer(effect(kind,radius=4,centerX=10,centerY=15,color='#FF0000FF',speed=2))
    a=apply(r,b,at(.1));c=apply(r,b,at(.7))
    assert a.px[...,0].sum()>0 and a.px[...,1:3].sum()==0
    if kind=='light-leak':
        assert not np.allclose(a.px,c.px)
    np.testing.assert_array_equal(a.px,apply(r,b,at(.1)).px)


def test_god_rays_blocker_occludes(make_renderer):
    b=solid((0,0,0),alpha=0,w=64,h=40);b.px[18:23,3:8]=1
    clear=apply(make_renderer(effect('god-rays',radius=20,centerX=5,centerY=20,samples=64)),b).region(b.rect)
    block=b.copy();block.px[5:36,24:29]=[0,0,0,1]
    occluded=apply(make_renderer(effect('god-rays',radius=20,centerX=5,centerY=20,samples=64)),block).region(b.rect)
    assert clear[18:23,40:50,0].sum()>0
    assert occluded[18:23,40:50,0].sum()<clear[18:23,40:50,0].sum()*.2


def test_bevel_pillow_relief_and_soften(make_renderer,tile):
    a=apply(make_renderer(fx('bevel',{'style':'emboss'},radius=3,relief=2,compositeOriginal='none')),tile)
    b=apply(make_renderer(fx('bevel',{'style':'pillow'},radius=3,relief=2,compositeOriginal='none')),tile)
    c=apply(make_renderer(fx('bevel',{'style':'emboss'},radius=3,relief=4,compositeOriginal='none')),tile)
    assert not np.allclose(a.px,b.px)
    assert c.px[...,3].sum()>a.px[...,3].sum()


def test_long_shadow_no_gaps_and_fade(make_renderer):
    b=Buf.empty(0,0,3,3);b.px[1,1]=1
    a=apply(make_renderer(fx('long-shadow',{'length':30},angle=0,compositeOriginal='none',color='#FFFFFFFF')),b)
    line=a.region((1,1,32,2))[0,:,3]
    np.testing.assert_array_equal(line,1)
    fade=apply(make_renderer(fx('long-shadow',{'length':30,'fade':2},angle=0,compositeOriginal='none',color='#FFFFFFFF')),b)
    assert fade.region((1,1,32,2))[0,-1,3]<.2


def test_grain_linear_channel_response_seed_and_size(make_renderer):
    b=solid((.2,.5,.8),w=64,h=48)
    r=make_renderer(fx('film-grain',{'red':0,'blue':0},amount=1,size=2))
    first,later=apply(r,b,at(0)),apply(r,b,at(1))
    np.testing.assert_array_equal(first.px[...,[0,2,3]],b.px[...,[0,2,3]])
    assert not np.array_equal(first.px,later.px)
    np.testing.assert_array_equal(first.px,apply(r,b,at(0)).px)
    from scenerender.raster import linear_to_srgb
    encoded=Buf(premul(linear_to_srgb(straight(b.px)[0]),b.px[...,3:4]))
    other=apply(make_renderer(fx('film-grain',{'red':0,'blue':0},amount=1,size=2),linear=False),encoded)
    np.testing.assert_allclose(first.px[...,:3],srgb_to_linear(straight(other.px)[0]),atol=1e-6)


def test_chromatic_lateral_and_longitudinal(make_renderer):
    b=Buf.empty(0,0,51,51);b.px[24:27,35:38]=1
    a=apply(make_renderer(fx('chromatic-aberration',amount=4,centerX=25,centerY=25)),b)
    y,x=np.indices(a.px.shape[:2])
    centers=[(x*a.px[...,i]).sum()/a.px[...,i].sum() for i in (0,1,2)]
    assert centers[0]<centers[1]<centers[2]
    b=apply(make_renderer(fx('chromatic-aberration',{'longitudinal':3},amount=0)),b)
    assert np.count_nonzero(b.px[...,0]>.01)>np.count_nonzero(b.px[...,1]>.01)


@pytest.mark.parametrize('kind',['glitch','vhs'])
def test_video_damage_seed_time_and_zero(make_renderer,tile,kind):
    np.testing.assert_array_equal(apply(make_renderer(effect(kind,amount=0)),tile).px,tile.px)
    r=make_renderer(effect(kind,amount=4,size=4,radius=3))
    a,b=apply(r,tile,at(0)),apply(r,tile,at(.5))
    assert not np.array_equal(a.px,b.px)
    np.testing.assert_array_equal(a.px,apply(r,tile,at(0)).px)


@pytest.mark.parametrize('shape',['round','line','square'])
def test_halftone_cmyk_preserves_primary_and_screen_shape(make_renderer,shape):
    out=apply(make_renderer(fx('halftone',{'screen':'cmyk','shape':shape},size=8)),solid((1,0,0),w=48,h=48))
    np.testing.assert_allclose(out.px[...,:3],np.broadcast_to([1,0,0],out.px[...,:3].shape),atol=.05)
    gray=apply(make_renderer(fx('halftone',{'shape':shape},size=8)),solid((.2,.2,.2),w=48,h=48))
    assert np.ptp(gray.px[...,0])>.7


MOVING='''<shape id="moving" shape="rect" width="6" height="8" fill="#FF0000FF">
<animate property="x"><key time="0" value="0"/><key time="1" value="40"/></animate>
<animate property="fill"><key time="0" value="#FF0000FF"/><key time="1" value="#0000FFFF"/></animate>
<animate property="rotation"><key time="0" value="0"/><key time="1" value="60"/></animate></shape>'''


def test_echo_true_earlier_positions_and_decay(make_renderer):
    r=make_renderer(fx('echo',{'interval':.5,'decay':.5},samples=2),composition=MOVING)
    node=r.doc.ids['moving'];c=at(1)
    b=r.rc.render_node_at(node,1,c).buf
    out=apply(r,b,c,node)
    early=r.rc.render_node_at(node,.5,c).buf
    np.testing.assert_allclose(out.region(early.rect),early.px*.5,atol=1e-6)
    np.testing.assert_allclose(out.region(b.rect),b.px,atol=1e-6)


def test_posterize_holds_pixels_rotation_and_color(make_renderer):
    r=make_renderer(effect('posterize-time',frequency=2),composition=MOVING)
    node=r.doc.ids['moving']
    def render(t):
        c=at(t);b=r.rc.render_node_at(node,t,c).buf
        return apply(r,b,c,node)
    a,b=render(.51),render(.99)
    assert a.rect==b.rect
    np.testing.assert_array_equal(a.px,b.px)
    expected=r.rc.render_node_at(node,.5,at(.5)).buf
    np.testing.assert_array_equal(a.px,expected.px)
    assert render(1).rect!=a.rect


def test_param_animation_uses_evaluator(make_renderer,tile):
    xml=fx('lens-distortion',{'k1':0})[:-9]+'''<animate property="k1"><key time="0" value="0"/><key time="1" value="0.3"/></animate></effect>'''
    r=make_renderer(xml)
    np.testing.assert_array_equal(apply(r,tile,at(0)).px,tile.px)
    assert not np.allclose(apply(r,tile,at(1)).px,tile.px)


@pytest.mark.parametrize('fmt,ext',[('flame','3dl'),('cinespace','csp')])
def test_ocio_3dl_and_csp_known_transform(make_renderer,tmp_path,fmt,ext):
    import PyOpenColorIO as ocio
    config=ocio.Config.CreateRaw()
    source=ocio.ColorSpace(name='input')
    target=ocio.ColorSpace(name='output')
    target.setTransform(ocio.ExponentTransform(value=[2,2,2,1]),ocio.COLORSPACE_DIR_FROM_REFERENCE)
    config.addColorSpace(source);config.addColorSpace(target)
    baker=ocio.Baker();baker.setConfig(config);baker.setFormat(fmt)
    baker.setInputSpace('input');baker.setTargetSpace('output');baker.setCubeSize(17)
    path=tmp_path/f'square.{ext}';path.write_text(baker.bake())
    b=solid((.5,.5,.5),alpha=.6)
    out=apply(make_renderer(effect('lut',src=path.name,space='linear-srgb')),b)
    np.testing.assert_allclose(straight(out.px)[0],.25,atol=.001)
    np.testing.assert_array_equal(out.px[...,3],b.px[...,3])


def test_agx_published_blender_4_reference_values(make_renderer):
    # Reference: Blender v4.0.0 config.ocio, Linear Rec.709 -> AgX Base sRGB,
    # converted from its display encoding back to linear sRGB.
    rgb=np.array([[[.18,.18,.18],[1,0,0],[.1,2,.4],[3,2,1]]],np.float32)
    expected=np.array([[[.179967910,.179968163,.179967418],
                        [.706819594,.041111249,.015163927],
                        [.390902698,.694870412,.411796600],
                        [.779359698,.684703529,.582458377]]],np.float32)
    out=apply(make_renderer(effect('tonemap',tonemapper='agx')),Buf(premul(rgb,np.ones((1,4,1)))))
    np.testing.assert_allclose(out.px[...,:3],expected,atol=2e-5)


def test_posterize_time_holds_child_temporal_randomness(make_renderer):
    effects=effect('posterize-time',frequency=2)+'<effect id="grain" type="film-grain" amount="2"/>'
    composition='''<group id="held" width="24" height="20"><shape id="noisy" shape="rect" width="24" height="20" fill="#808080FF" effects="grain"/></group>'''
    r=make_renderer(effects,composition=composition);node=r.doc.ids['held']
    outputs=[]
    for t in (.51,.99):
        c=at(t);buf=r.rc.render_node_at(node,t,c).buf
        outputs.append(apply(r,buf,c,node))
    np.testing.assert_array_equal(outputs[0].px,outputs[1].px)
