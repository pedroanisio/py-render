"""Correspondence and film geometry semantics for the upgraded transitions."""
import numpy as np
import pytest
from lxml import etree

from test_transitions import rig, run
from scenerender.evaluator import Ctx
from scenerender.raster import Buf
from scenerender.registry import TRANSITIONS
from scenerender.effects.fields import optical_flow


def test_flow_recovers_translation():
    rng=np.random.default_rng(7)
    A=np.zeros((64,96,4),np.float32)
    A[16:48,16:40]=rng.uniform(.1,1,(32,24,4));A[16:48,16:40,3]=1
    B=np.zeros_like(A);B[:,12:]=A[:,:-12]
    flow=optical_flow(A,B,iterations=5)
    np.testing.assert_allclose(np.median(flow[20:44,20:36],axis=(0,1)),[12,0],atol=.7)


def test_morph_moves_corresponding_features(rig):
    rc,tr,_,_=rig
    A=np.zeros((rc.height,rc.width,4),np.float32);A[30:60,35:65]=1
    B=np.zeros_like(A);B[30:60,65:95]=1
    out=TRANSITIONS.get('morph')(rc,tr,Buf(A),Buf(B),.5,Ctx(t=2,comp_t=2)).px
    # Midpoint must occupy the intermediate location, not two translucent copies.
    assert out[42:48,63:68,3].mean()>.8
    y,x=np.indices(out.shape[:2]);alpha=out[...,3]
    assert (x*alpha).sum()/alpha.sum()==pytest.approx(64.5,abs=2)
    assert TRANSITIONS.level('morph')=='full'


@pytest.mark.parametrize('kind',['morph','film-roll'])
def test_endpoints_and_missing_sides(rig,kind):
    rc,_,A,B=rig
    np.testing.assert_array_equal(run(rig,kind,0,a=False).px,np.zeros_like(A.px))
    np.testing.assert_array_equal(run(rig,kind,1,b=False).px,np.zeros_like(B.px))
    np.testing.assert_array_equal(run(rig,kind,0).px,A.px)
    np.testing.assert_array_equal(run(rig,kind,1).px,B.px)


def test_film_strip_holes_frame_lines_direction_and_curvature(rig):
    rc,tr,A,B=rig
    a=run(rig,'film-roll',.5,direction='left',color='#FF0000FF',motionBlur='false').px
    assert np.any(a[...,3]==0)  # sprocket holes
    assert np.any((a[...,0]>.9)&(a[...,1]<.01)&(a[...,2]<.01))
    vertical=run(rig,'film-roll',.5,direction='up',color='#FF0000FF',motionBlur='false').px
    assert not np.allclose(a,vertical)
    tr.append(etree.Element('param',name='curvature',value='0'))
    flat=run(rig,'film-roll',.5,direction='left',color='#FF0000FF',motionBlur='false').px
    tr.remove(tr[-1])
    assert not np.allclose(a,flat)
    assert TRANSITIONS.level('film-roll')=='full'


def test_film_strip_shutter_blurs_motion(rig):
    sharp=run(rig,'film-roll',.5,t=2,motionBlur='false').px
    blurred=run(rig,'film-roll',.5,t=2,motionBlur='true').px
    assert not np.allclose(sharp,blurred)
    assert np.any((blurred[...,3]>0)&(blurred[...,3]<1))
