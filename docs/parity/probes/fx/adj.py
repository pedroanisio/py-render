exec(open('gen2.py').read().split("if __name__")[0])
for t in ['film-grain','noise','glitch','vhs','pixelate','mosaic','scanlines','halftone','wave-warp','ripple','turbulent-displace','heat-haze','light-leak','fractal-noise','light-sweep','lens-flare','vignette','tile','mirror']:
    a,r=one((t,True,True)); print(t, r)
