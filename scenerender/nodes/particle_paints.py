"""Spatial particle paints sampled in device pixels, with independent shape masks."""
import cairo
import numpy as np

from .. import paint


def _pixels(surface):
    surface.flush()
    h, w = surface.get_height(), surface.get_width()
    return np.frombuffer(surface.get_data(), np.float32).reshape(h, surface.get_stride()//4)[:, :w*4].reshape(h, w, 4)


def _device_surface(cr, rect, canvas_rect):
    surface = cairo.ImageSurface(cairo.FORMAT_RGBA128F, rect[2]-rect[0], rect[3]-rect[1])
    draw = cairo.Context(surface)
    matrix = cr.get_matrix()
    matrix.x0 += canvas_rect[0]-rect[0]
    matrix.y0 += canvas_rect[1]-rect[1]
    draw.set_matrix(matrix)
    draw.set_antialias(cr.get_antialias())
    return surface, draw


def mask_surface(cr, surface, rect, canvas_rect):
    matrix = cr.get_matrix()
    cr.identity_matrix()
    cr.mask_surface(surface, rect[0]-canvas_rect[0], rect[1]-canvas_rect[1])
    cr.set_matrix(matrix)


def trail_mask(cr, rect, canvas_rect, points, times, duration, width):
    """Coverage of one whole trail, retaining float opacity across segment joins."""
    surface, draw = _device_surface(cr, rect, canvas_rect)
    draw.set_line_cap(cairo.LINE_CAP_ROUND)
    draw.set_line_width(width)
    for i in range(len(points)-1):
        (x1, y1), (x2, y2) = points[i:i+2]
        if abs(x1-x2)+abs(y1-y2) < 1e-9:
            continue
        gradient = cairo.LinearGradient(x2, y2, x1, y1)
        gradient.add_color_stop_rgba(0, 1, 1, 1, 1-times[i+1]/duration)
        gradient.add_color_stop_rgba(1, 1, 1, 1, 1-times[i]/duration)
        draw.set_source(gradient)
        draw.move_to(x2, y2)
        draw.line_to(x1, y1)
        draw.stroke()
    return surface


class ParticlePaint:
    def __init__(self, rc, ctx, rect, canvas_rect, matrix, width, height, start, end, fraction, opacity):
        self.rect, self.canvas_rect = rect, canvas_rect
        self.width, self.height = rect[2]-rect[0], rect[3]-rect[1]
        self.surfaces = {}

        def sample(spec):
            if isinstance(spec, tuple):
                return np.broadcast_to(np.array(spec, np.float32), (self.height, self.width, 4))
            surface = cairo.ImageSurface(cairo.FORMAT_RGBA128F, self.width, self.height)
            cr = cairo.Context(surface)
            cr.translate(-rect[0], -rect[1])
            cr.transform(cairo.Matrix(matrix[0, 0], matrix[1, 0], matrix[0, 1], matrix[1, 1], matrix[0, 2], matrix[1, 2]))
            if paint.set_source(rc, cr, spec, width, height, ctx):
                cr.paint()
            result = _pixels(surface).copy()
            alpha = result[..., 3:4]
            result[..., :3] = np.divide(result[..., :3], alpha, out=np.zeros_like(result[..., :3]), where=alpha > 0)
            return result

        first = sample(start)
        self.rgba = first.copy() if start == end else first+(sample(end)-first)*fraction
        self.rgba = np.clip(self.rgba, 0, 1)
        self.rgba[..., 3] *= opacity

    def _surface(self, gain=1., white=False):
        key = (gain, white)
        if key not in self.surfaces:
            rgba = self.rgba.copy()
            rgba[..., 3] = np.clip(rgba[..., 3]*gain, 0, 1)
            rgba[..., :3] = rgba[..., 3:4] if white else rgba[..., :3]*rgba[..., 3:4]
            surface = cairo.ImageSurface(cairo.FORMAT_RGBA128F, self.width, self.height)
            _pixels(surface)[:] = rgba
            surface.mark_dirty()
            self.surfaces[key] = surface
        return self.surfaces[key]

    def _source_surface(self, cr, surface):
        # Lock the source to device pixels even inside a rotated sprite or square.
        matrix = cr.get_matrix()
        cr.identity_matrix()
        cr.set_source_surface(surface, self.rect[0]-self.canvas_rect[0], self.rect[1]-self.canvas_rect[1])
        cr.set_matrix(matrix)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)

    def source(self, cr, gain=1., white=False):
        self._source_surface(cr, self._surface(gain, white))

    def stroke_mask(self, cr, opacity):
        mask, draw = self._device_surface(cr)
        draw.append_path(cr.copy_path())
        cr.new_path()
        draw.set_line_width(cr.get_line_width())
        draw.set_line_cap(cr.get_line_cap())
        draw.set_line_join(cr.get_line_join())
        draw.set_antialias(cr.get_antialias())
        draw.set_source(opacity)
        draw.stroke()
        self.source(cr)
        mask_surface(cr, mask, self.rect, self.canvas_rect)

    def _device_surface(self, cr):
        return _device_surface(cr, self.rect, self.canvas_rect)

    def sprite(self, cr, surface, x, y):
        result, draw = self._device_surface(cr)
        draw.set_source_surface(surface, x, y)
        draw.paint()
        _pixels(result)[:] *= _pixels(self._surface())
        result.mark_dirty()
        self._source_surface(cr, result)
        cr.paint()
