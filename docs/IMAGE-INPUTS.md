# Image inputs

`image` and `imageSequence` decode source samples into floating-point working
pixels before spatial filtering or frame mixing. `colorSpace` defaults to `srgb`;
`transfer="auto"` uses that space's transfer curve, consistently with video inputs.
Declare `colorSpace="linear-srgb"` (or the appropriate linear working space) for
linear HDR files. File extensions do not override these schema defaults.

`alpha="straight"` multiplies transformed RGB by alpha. `premultiplied` removes
alpha before a nonlinear transfer conversion and reapplies it afterward. Linear
premultiplied input also preserves RGB at zero alpha, which can represent EXR
emission. `none` ignores the stored alpha. `auto` uses straight alpha for ordinary
images and PSD, associated alpha for EXR, and the TIFF ExtraSamples declaration.

PNG and TIFF integer samples retain 16-bit precision. Floating-point TIFF,
Radiance RGBE/XYZE and flat OpenEXR inputs retain HDR samples. Negative working
components are preserved. Filtering operates on premultiplied working samples;
when the project uses linear light, black/white averaging therefore produces
linear 0.5. A bounded image cache includes file metadata, selected layer, input
declarations and working space. Generated image caches use the same float path
after mandatory cache hash verification.

The `image/@layer` selector supports:

| File | Selection |
|---|---|
| PSD/PSB | Unique layer name, slash-separated group/layer path, or zero-based depth-first layer index. A selected group includes its visible children. The selected layer itself may be hidden. Placement remains relative to the document canvas. |
| OpenEXR | Part name or zero-based part index; channel prefix such as `diffuse`; individual channel such as `depth.Z`; or `part:prefix` to disambiguate parts. A part with one RGB prefix selects that set automatically. Data-window pixels are positioned within the display window. |
| TIFF | Page description or zero-based page index. |

Missing or ambiguous selections produce a diagnostic and no image. With no PSD
selection, the saved merged image is preferred; otherwise `psd-tools` composites
the selected content. OpenEXR requires the `exr` or `3d` package extra.

A named representation takes precedence over the asset source and can override
its input color declarations. Its stored width/height do not change the asset's
declared drawing size. The `proxy` preference uses `@proxy` when no named proxy
representation exists. Image sequences accept a frame pattern in `@proxy`.
Asset and representation SHA-256 mismatches follow the shared media policy:
warn and render the selected file; generated cache mismatches fail rendering.

External-format and downstream limits remain. PSD compositing inherits
[`psd-tools`' documented adjustment, effect and text limitations](https://psd-tools.readthedocs.io/en/latest/reference/psd_tools.composite.html).
PSD CMYK uses an arithmetic RGB conversion, without an embedded ICC transform;
other non-RGB/grayscale PSD modes and non-RGB/grayscale/palette TIFF modes need
further work. Deep EXR samples currently require flattening before import.
The [OpenEXR Python API](https://openexr.com/en/latest/python.html) supplies the
part/channel samples used by the flat-image decoder. Pattern paint still passes
through an eight-bit Cairo surface, although it now applies the image's declared
input conversion. Sequence-wide provenance hashing needs a defined aggregate
file contract. These limits are part of the outstanding conformance work.

Rendered regression evidence is in `tests/test_image_inputs.py`.
