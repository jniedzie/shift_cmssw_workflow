#!/usr/bin/env python3
# Code style: AI-code. AI-generated exact CSC layer-surface geometry diagnostic.
"""Export frozen CSC surfaces and intersect straight rays with their polygons.

These are layer-surface footprints, not measured reconstruction efficiencies.
Neither magnetic bending, material scattering nor electronics is predicted.
"""
import argparse
from array import array
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import runpy
import subprocess


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def local_to_global(layer,point,vector=False):
    """Use the stored ROOT-established local x/y/z basis, in centimetres."""
    if len(point)!=3 or not all(math.isfinite(x) for x in point):
        raise ValueError('Require a finite local three-vector')
    return [math.fsum(point[j]*layer['axes'][axis][i] for j,axis in enumerate(['x','y','z']))
            +(0. if vector else layer['center_cm'][i]) for i in range(3)]


def ray_intersections(geometry,origin_cm,momentum_GeV):
    """Count exact forward intersections, grouping distinct layers and stations."""
    if (len(origin_cm)!=3 or len(momentum_GeV)!=3
            or not all(math.isfinite(v) for v in list(origin_cm)+list(momentum_GeV))):
        raise ValueError('Require finite origin and momentum three-vectors')
    magnitude=math.hypot(*momentum_GeV)
    if magnitude<=0.:
        raise ValueError('Zero momentum has no defined ray')
    direction=[v/magnitude for v in momentum_GeV]
    dot=lambda a,b:math.fsum(x*y for x,y in zip(a,b))
    hits=[];best={}
    for layer in geometry['layers']:
        normal=layer['axes']['z'];denominator=dot(direction,normal)
        if denominator==0.:continue
        path=dot([a-b for a,b in zip(layer['center_cm'],origin_cm)],normal)/denominator
        if path<0.:continue
        point=[a+path*b for a,b in zip(origin_cm,direction)]
        delta=[a-b for a,b in zip(point,layer['center_cm'])]
        x=dot(delta,layer['axes']['x']);y=dot(delta,layer['axes']['y'])
        height=layer['half_length_cm'];bottom=layer['bottom_half_width_cm'];top=layer['top_half_width_cm']
        slope=(top-bottom)/(2.*height);width=bottom+slope*(y+height)
        clearance=min(height-y,height+y,(width-abs(x))/math.sqrt(1.+slope*slope))
        key=(layer['endcap'],layer['station'])
        if key not in best or clearance>best[key]['signed_inside_clearance_cm']:
            best[key]=dict(endcap=key[0],station=key[1],raw_id=layer['raw_id'],
                           signed_inside_clearance_cm=clearance)
        if not (-height<=y<=height and abs(x)<=width):continue
        hits.append(dict(raw_id=layer['raw_id'],endcap=layer['endcap'],station=layer['station'],
            ring=layer['ring'],chamber=layer['chamber'],layer=layer['layer'],
            path_cm=path,global_cm=point,local_cm=[x,y,0.],signed_inside_clearance_cm=clearance))
    groups={}
    for side,number in [('plus',1),('minus',2)]:
        selected=[h for h in hits if h['endcap']==number]
        ids=sorted({h['raw_id'] for h in selected});stations=sorted({h['station'] for h in selected})
        groups[side]=dict(layer_ids=ids,layer_count=len(ids),stations=stations,station_count=len(stations))
    return dict(hits=hits,by_endcap=groups,best_layer_clearance_by_station=[best[k] for k in sorted(best)],
        two_stations_each_endcap=all(g['station_count']>=2 for g in groups.values()),
        straight_rays_only=True,no_muon_pt_cut=True,measured_reco_efficiency=False,
        material_scattering_field_and_electronics_not_predicted=True)


def read_stock_geometry(path,source_pins):
    """Read every CSC layer from stock DumpFWRecoGeometry; ignore points[]."""
    import ROOT
    for library in ['libGeom','libDataFormatsDetId','libDataFormatsMuonDetId']:
        if ROOT.gSystem.Load(library)<0:
            raise RuntimeError('Cannot load stock geometry dependency '+library)
    file=ROOT.TFile.Open(str(path))
    if not file or file.IsZombie():raise ValueError('Unreadable stock geometry export')
    layers=[]
    try:
        tree=file.Get('idToGeo')
        if not tree:raise ValueError('Stock geometry has no idToGeo tree')
        for row in tree:
            raw=int(row.id)
            # DetId::Muon=2 and MuonSubdetId::CSC=2; decoding then uses CSCDetId.
            detector=ROOT.DetId(raw)
            if int(detector.det())!=2 or int(detector.subdetId())!=2:continue
            csc=ROOT.CSCDetId(raw)
            if int(csc.layer())==0:continue
            shape=[float(x) for x in row.shape]
            if len(shape)!=5 or shape[0]!=1. or any(not math.isfinite(v) or v<=0 for v in shape[1:]):
                raise ValueError('CSC layer is not a finite positive stock trapezoid')
            values=[float(x) for x in row.matrix];rotation=ROOT.TGeoRotation()
            rotation.SetMatrix(array('d',values));axes={}
            for name,basis in [('x',[1.,0.,0.]),('y',[0.,1.,0.]),('z',[0.,0.,1.])]:
                result=array('d',[0.,0.,0.]);rotation.LocalToMasterVect(array('d',basis),result)
                axes[name]=list(result)
            for first in axes:
                for second in axes:
                    value=math.fsum(x*y for x,y in zip(axes[first],axes[second]))
                    if not math.isclose(value,float(first==second),rel_tol=0.,abs_tol=2.e-6):
                        raise ValueError('Stock layer rotation is not orthonormal at Float_t precision')
            layers.append(dict(raw_id=raw,endcap=int(csc.endcap()),station=int(csc.station()),
                ring=int(csc.ring()),chamber=int(csc.chamber()),layer=int(csc.layer()),
                center_cm=[float(x) for x in row.translation],axes=axes,ROOT_matrix=values,
                bottom_half_width_cm=shape[1],top_half_width_cm=shape[2],
                half_thickness_cm=shape[3],half_length_cm=shape[4]))
    finally:
        file.Close()
    if not layers or len({r['raw_id'] for r in layers})!=len(layers):
        raise ValueError('Missing or repeated CSC layer identity')
    stations={(r['endcap'],r['station']) for r in layers}
    if stations!={(side,station) for side in [1,2] for station in [1,2,3,4]}:
        raise ValueError('Export omits a CSC endcap/station')
    chambers=defaultdict(set)
    for row in layers:chambers[(row['endcap'],row['station'],row['ring'],row['chamber'])].add(row['layer'])
    if any(value!=set(range(1,7)) for value in chambers.values()):
        raise ValueError('Stock chamber has an incomplete six-layer export')
    return dict(schema='shift-CSC-active-layer-surfaces-v1',layers=sorted(layers,key=lambda r:r['raw_id']),
        source_pins=source_pins,stock_geometry_root_sha256=sha(path),layer_count=len(layers),
        chamber_count=len(chambers),all_stock_export_CSC_layers_preserved=True,
        unfilled_points_branch_ignored=True,coordinates='CMS global centimetres; local orthonormal layer basis',
        geometry_storage_precision='Stock export uses Float_t transforms and shape parameters; edges are not widened',
        active_layer_surface_footprints_not_measured_reco_efficiency=True)


def dump_worker(request):
    """Use the exact retained process geometry, running no physics modules."""
    tools=Path(__file__).resolve().parents[1];pin=request['reference_config'];reference=Path(pin['path'])
    if not reference.is_absolute():reference=tools/reference
    if sha(reference)!=pin['sha256']:raise ValueError('Frozen reference Step4 configuration changed')
    if request.get('first_run',600500001)!=600500001:
        raise ValueError('Geometry qualification uses the exact reference source run600500001')
    import FWCore.ParameterSet.Config as cms
    process=runpy.run_path(str(reference))['process']
    global_tag=process.GlobalTag.dumpPython() if hasattr(process,'GlobalTag') else None
    process.source=cms.Source('EmptySource',firstRun=cms.untracked.uint32(600500001))
    process.maxEvents=cms.untracked.PSet(input=cms.untracked.int32(1))
    process.activeCscGeometry=cms.ESProducer('FWRecoGeometryESProducer',
        Tracker=cms.untracked.bool(False),Muon=cms.untracked.bool(True),
        Calo=cms.untracked.bool(False),Timing=cms.untracked.bool(False))
    process.dumpActiveCscGeometry=cms.EDAnalyzer('DumpFWRecoGeometry',level=cms.untracked.int32(1),
        tagInfo=cms.untracked.string('Frozen reference Step4 '+pin['sha256']),
        outputFileName=cms.untracked.string('csc_geometry.root'))
    process.cscGeometryExport=cms.Path(process.dumpActiveCscGeometry)
    process.schedule=cms.Schedule(process.cscGeometryExport)
    if global_tag!=(process.GlobalTag.dumpPython() if hasattr(process,'GlobalTag') else None):
        raise ValueError('Geometry export changed the frozen GlobalTag')
    config=Path('csc_geometry_export_cfg.py');config.write_text(process.dumpPython())
    with Path('csc_geometry_export.log').open('w') as log:
        subprocess.run(['cmsRun',str(config)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
    pins=dict(reference_config=pin,runtime_sha256=request['runtime_sha256'],
        export_config_sha256=sha(config),original_GlobalTag=global_tag,first_run=600500001,
        stock_producer='FWRecoGeometryESProducer',stock_analyzer='DumpFWRecoGeometry',
        physics_processing_performed=False,original_geometry_conditions_preserved=True)
    geometry=read_stock_geometry('csc_geometry.root',pins)
    output=Path(request.get('output_map','csc_active_layers.json.gz'))
    data=json.dumps(geometry,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    output.write_bytes(gzip.compress(data,mtime=0))
    if output.stat().st_size>request.get('maximum_map_bytes',2*1048576):
        raise ValueError('CSC map exceeds its registered compact output bound')
    return dict(complete=True,diagnostic_only=True,layer_count=geometry['layer_count'],
        chamber_count=geometry['chamber_count'],map_path=str(output),map_bytes=output.stat().st_size,
        map_sha256=sha(output),source_pins=pins,no_SIM_reconstruction_or_new_GEN=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='mode',required=True)
    dump=sub.add_parser('dump');dump.add_argument('request',type=Path)
    read=sub.add_parser('read');read.add_argument('root_file',type=Path);read.add_argument('output',type=Path)
    read.add_argument('--source-pins',type=Path,required=True)
    args=parser.parse_args()
    if args.mode=='read':
        value=read_stock_geometry(args.root_file,json.loads(args.source_pins.read_text()))
        args.output.write_bytes(gzip.compress(json.dumps(value,allow_nan=False).encode(),mtime=0))
        return
    report=dict(complete=False)
    try:
        report=dump_worker(json.loads(args.request.read_text()))
    except Exception as error:
        report['error']=repr(error);raise
    finally:
        save('report.json',report)


if __name__=='__main__':main()
