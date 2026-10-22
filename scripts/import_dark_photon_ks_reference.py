#!/usr/bin/env python3
# Code style: AI-code. AI-generated importer of published kaon reference curves.
"""Recover the plotted KS complex amplitude from its original vector figure.

Input is Aggstar.pdf in arXiv:1609.03574v2's source archive, checked by SHA-256.
This reads the original drawing coordinates rather than fitting a raster image.
The primary publication is Colangelo, Stucki and Tunstall, EPJC76 (2016)604,
doi:10.1140/epjc/s10052-016-4449-2, distributed under CC-BY4.0. The resulting
table is an attributed numerical representation of Figure8, not a new solution
of the dispersion relations or a model-independent uncertainty bound.

The PDF's real/imaginary central curves and filled systematic bands are kept.
Independent analytic LO curves and published Dalitz integrals validate the
coordinate conversion. No external numerical fit or photon-rate rescaling is
used. pypdf is needed only by this import tool, not production kernels.
"""
import argparse
import bisect
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path


FIGURE_SHA256 = '3026ba71009e40a849fca5220f61a14656a47b82bc4bf1f2be3d57202fc2e6c0'


def vector_paths(path):
    from pypdf import PdfReader
    from pypdf.generic import ContentStream

    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != FIGURE_SHA256:
        raise ValueError('Require the exact published Aggstar.pdf source figure')
    reader = PdfReader(path)
    if len(reader.pages) != 1 or list(reader.pages[0].mediabox) != [0, 0, 360, 205]:
        raise ValueError('Published reference page geometry changed')
    contents = ContentStream(reader.pages[0].get_contents(), reader)
    state = dict(matrix=[1., 0., 0., 1., 0., 0.], stroke=[0., 0., 0.], fill=[0., 0., 0.])
    stack, result, drawing = [], [], []

    def point(x, y):
        a, b, c, d, e, f = state['matrix']
        return [a*float(x)+c*float(y)+e, b*float(x)+d*float(y)+f]

    for operands, operator in contents.operations:
        if operator == b'q':
            stack.append(deepcopy(state))
        elif operator == b'Q':
            state = stack.pop()
        elif operator == b'cm':
            a, b, c, d, e, f = state['matrix']
            aa, bb, cc, dd, ee, ff = map(float, operands)
            state['matrix'] = [a*aa+c*bb, b*aa+d*bb, a*cc+c*dd, b*cc+d*dd,
                               a*ee+c*ff+e, b*ee+d*ff+f]
        elif operator in (b'RG', b'rg'):
            state['stroke' if operator == b'RG' else 'fill'] = list(map(float, operands))
        elif operator in (b'G', b'g'):
            state['stroke' if operator == b'G' else 'fill'] = [float(operands[0])]*3
        elif operator in (b'm', b'l'):
            drawing.append([operator.decode(), *point(*operands)])
        elif operator == b'c':
            drawing.append(['c', *[value for index in range(0, 6, 2)
                                   for value in point(*operands[index:index+2])]])
        elif operator == b'h':
            drawing.append(['h'])
        elif operator in (b'S', b's', b'f', b'f*', b'B', b'B*', b'b', b'b*'):
            if drawing:
                result.append(dict(operator=operator.decode(), stroke=state['stroke'],
                                   fill=state['fill'], path=drawing))
            drawing = []
        elif operator == b'n':
            drawing = []
    return result


def interpolate(points, x):
    xs = [row[0] for row in points]
    right = min(max(bisect.bisect_right(xs, x), 1), len(xs)-1)
    first, second = points[right-1:right+1]
    return first[1]+(x-first[0])/(second[0]-first[0])*(second[1]-first[1])


def polygon_values(points, x):
    result = []
    for first, second in zip(points, points[1:]+points[:1]):
        if first[0] == second[0]:
            if first[0] == x:
                result.extend([first[1], second[1]])
        elif min(first[0], second[0]) <= x <= max(first[0], second[0]):
            result.append(first[1]+(x-first[0])/(second[0]-first[0])*(second[1]-first[1]))
    if not result:
        raise ValueError('Systematic polygon does not cover the published central curve')
    return result


def reference_table(path):
    paths = vector_paths(path)
    # Axis coordinates come from the major zero tick and numerical grid lines.
    xzero, yzero = paths[34]['path'][0][1], paths[8]['path'][0][2]
    virtualities = [.05, .1, .15, .2]
    amplitudes = [-2.e-8, -1.5e-8, -1.e-8, -.5e-8, 0., .5e-8]
    xscale = math.fsum(q*(paths[i]['path'][0][1]-xzero) for i, q in enumerate(virtualities))/math.fsum(q*q for q in virtualities)
    yscale = math.fsum(a*(paths[i+4]['path'][0][2]-yzero) for i, a in enumerate(amplitudes))/math.fsum(a*a for a in amplitudes)

    def points(index):
        result = []
        for row in paths[index]['path']:
            if row[0] == 'h':
                break
            if row[0] not in ('m', 'l'):
                raise ValueError('Reference curve is no longer a piecewise-linear vector path')
            result.append(((row[1]-xzero)/xscale, (row[2]-yzero)/yscale))
        return result

    blue = [.215683, .49411, .721558]
    if any(paths[index]['stroke'] != blue for index in (13, 18)):
        raise ValueError('Published dispersive central-curve identity changed')
    if any(paths[index]['fill'] != blue for index in (10, 11, 12)):
        raise ValueError('Published systematic-band identity changed')
    real, imaginary = points(13), points(18)
    real_bands, imaginary_band = [points(10), points(11)], points(12)
    grid = sorted({row[0] for curve in [real, imaginary, *real_bands, imaginary_band] for row in curve})
    rows = []
    for q2 in grid:
        re, im = interpolate(real, q2), interpolate(imaginary, q2)
        re_band = [value for polygon in real_bands for value in polygon_values(polygon, q2)]
        im_band = polygon_values(imaginary_band, q2)
        rows.append([q2, re, im, min(re_band), max(re_band), min(im_band), max(im_band)])
    return dict(code_style='AI-code. AI-generated attributed numerical extraction of published vector data.',
        schema='shift-ks-dispersive-figure-reference-v1',
        publication=dict(title='Dispersive treatment of KS to gamma gamma and KS to gamma l+l-',
            authors=['Gilberto Colangelo', 'Ramon Stucki', 'Lewis C. Tunstall'],
            doi='10.1140/epjc/s10052-016-4449-2', arxiv='1609.03574v2',
            url='https://arxiv.org/pdf/1609.03574', figure=8,
            license='https://creativecommons.org/licenses/by/4.0/'),
        source=dict(url='https://arxiv.org/src/1609.03574v2', member='Aggstar.pdf',
            figure_sha256=FIGURE_SHA256, method='Original PDF vector paths; no raster digitization or fitted amplitude.'),
        axes=dict(virtuality_pdf_zero=xzero, virtuality_pdf_scale=xscale,
            amplitude_pdf_zero=yzero, amplitude_pdf_scale=yscale),
        units=dict(q2='GeV^2', amplitude='GeV^-1'),
        domain_q2_gev2=[rows[0][0], rows[-1][0]],
        columns=['q2', 'real', 'imaginary', 'real_low', 'real_high', 'imaginary_low', 'imaginary_high'],
        uncertainty_scope='Pointwise published systematic bands. Their correlations and a statistical confidence level are unavailable. The rectangle envelope is not a model-independent bound.',
        interpolation='Piecewise linear in q2 through the original vector vertices; no endpoint extrapolation.',
        rows=rows,
        independent_validation_curves=dict(
            real=sorted(points(14)+points(15)), imaginary=sorted(points(19)+points(20)),
            meaning='Published LO chiral reference, independently calculable from Eqs22-25.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--figure', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    table = reference_table(args.figure)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        json.dump(table, output, indent=2, allow_nan=False)
        output.write('\n')
    print(json.dumps(dict(output=str(args.output), rows=len(table['rows']),
        sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))


if __name__ == '__main__':
    main()
