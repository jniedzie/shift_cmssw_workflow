#!/usr/bin/env python3
"""AFS-only scientific gate and fixed production budget from independent pilots."""
import argparse,hashlib,json,math,sys
from pathlib import Path


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def aggregate(records):
    ledgers=[r['ledger'] for r in records];n=sum(x['tried'] for x in ledgers);w=math.fsum(x['sumw'] for x in ledgers);w2=math.fsum(x['sumw2'] for x in ledgers);nd=ledgers[0]['sigma_nd_mb']
    if any(not math.isclose(x['sigma_nd_mb'],nd,rel_tol=1e-9) for x in ledgers):raise ValueError('Source sigmaND changed across seeds')
    return dict(trials=n,accepted=sum(x['accepted'] for x in ledgers),sumw=w,sumw2=w2,sigma_mb=nd*w/n,error_mb=nd*math.sqrt(max(0.,w2-w*w/n)/(n*(n-1))),ess=w*w/w2 if w2 else 0.,seconds=sum(r['generation_seconds'] for r in records),bytes=sum(r['artifacts']['gen.root']['bytes'] for r in records))
def cdf_distance(a,aw,b,bw):
    combined=[(v,w,0) for v,w in zip(a,aw)]+[(v,w,1) for v,w in zip(b,bw)];combined.sort();totals=[math.fsum(aw),math.fsum(bw)];cdf=[0.,0.];maximum=0.;i=0
    while i<len(combined):
        value=combined[i][0]
        while i<len(combined) and combined[i][0]==value:
            _,w,which=combined[i];cdf[which]+=w/totals[which];i+=1
        maximum=max(maximum,abs(cdf[0]-cdf[1]))
    return maximum


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory');args=parser.parse_args();base=Path(args.directory);manifest=json.loads((base/'manifest.json').read_text());expected=sha(base/'manifest.json')
    result=dict(schema='shift-weighted-high-bin-scientific-gate-v2',manifest_sha256=expected,pass_=False,algorithm=manifest['algorithm'],physics_valid=False,normalization_ready=False,closure={},pilot_summary={},production_plan={},failures=[])
    try:
        grouped={}
        if sha(base/'ordinary_reference.json')!=manifest['ordinary_reference_sha256']:raise ValueError('Frozen ordinary reference changed')
        for row in manifest['jobs']:
            if row['stage']=='production':continue
            path=base/'results'/f"{row['id']}.json";r=json.loads(path.read_text())
            if r.get('schema')!='shift-weighted-gen-receipt-v3' or not r.get('complete') or not r.get('generated_edm') or not r.get('publication_complete') or r.get('manifest_sha256')!=expected or r.get('request')!=row or r.get('algorithm')!=manifest['algorithm'] or r.get('source_inputs')!=manifest['inputs']:raise ValueError('Invalid frozen validation receipt '+row['id'])
            if not r['semantic_audit'].get('complete') or not r['semantic_audit'].get('healthy') or r['semantic_audit'].get('algorithm')!=manifest['algorithm'] or r['semantic_audit'].get('ledger')!=r['ledger'] or r['semantic_audit'].get('source_model_settings_sha256')!=manifest['source_model_settings_sha256'] or r['semantic_audit'].get('events')!=r['ledger']['accepted']:raise ValueError('Semantic output not bound to trial ledger')
            grouped.setdefault(row['stratum'],[]).append(r)
        reference=json.loads((base/'ordinary_reference.json').read_text());reference_samples={x['stratum']:x for x in reference['samples']}
        for stratum in manifest['closure_strata']:
            cohort=grouped[stratum];sample=aggregate(cohort);old=reference_samples[stratum];pull=abs(sample['sigma_mb']-old['sigma_mb'])/math.hypot(sample['error_mb'],old['error_poisson_mb'])
            old_shapes=[x['result'] for x in reference['shape_records'] if x['request']['sample']==cohort[0]['request']['sample'] and x['request']['bounds']==cohort[0]['request']['bounds']]
            shape={key:[v for x in old_shapes for v in x[key]] for key in ('weights','scale','charged')}
            weights=[w for r in cohort for w in r['semantic_audit']['weights']]
            distances={key:cdf_distance([v for r in cohort for v in r['semantic_audit'][key]],weights,shape[key],shape['weights']) for key in ('scale','charged')}
            threshold=1.95*math.sqrt(1/sample['ess']+1/len(shape['weights']))
            compatible=pull<=4 and sample['ess']>=500 and all(x<threshold for x in distances.values())
            result['closure'][stratum]=dict(sample,reference_sigma_mb=old['sigma_mb'],cross_section_pull=pull,weighted_cdf_distances=distances,compatibility_threshold=threshold,weighted_cdf_is_heuristic=True,pass_=compatible)
            if not compatible:raise ValueError('Cross-section or shape compatibility gate failed: '+stratum)
        projected=0
        for stratum,target in manifest['effective_event_targets'].items():
            sample=aggregate(grouped[stratum]);result['pilot_summary'][stratum]=sample
            if sample['ess']<50 or sample['accepted']<100 or sample['seconds']<=0:raise ValueError('Insufficient high-bin pilot precision: '+stratum)
            efficiency=sample['ess']/sample['trials'];chunks=math.ceil(manifest['budget_safety_factor']*target/efficiency/manifest['chunk_trials'])
            if not 1<=chunks<=manifest['max_chunks_per_stratum']:raise ValueError('Pilot requires budget beyond bounded maximum: '+stratum)
            trials=chunks*manifest['chunk_trials'];size=math.ceil(1.3*sample['bytes']/sample['trials']*trials);projected+=size
            hours=sample['seconds']/sample['trials']*trials/3600
            if sample['bytes']/sample['trials']*manifest['chunk_trials']>manifest['max_root_bytes_per_chunk']:raise ValueError('Chunk ROOT size projection exceeds bound: '+stratum)
            if hours>manifest['max_worker_hours_per_stratum']:raise ValueError('High-bin performance gate failed: '+stratum)
            result['production_plan'][stratum]=dict(chunks=chunks,trials=trials,effective_event_target=target,expected_effective_events=trials*efficiency,projected_bytes=size,projected_worker_hours=hours,budget_derived_from_independent_pilots=True,pilots_excluded_from_production_normalization=True)
        if projected>manifest['max_projected_production_bytes']:raise ValueError('Production storage projection exceeds frozen gate')
        result['projected_production_bytes']=projected;result['pass_']=True
    except Exception as error:result['failures'].append(repr(error))
    result['pass']=result.pop('pass_')
    for closure in result['closure'].values():closure['pass']=closure.pop('pass_')
    temporary=base/'scientific_gate.json.tmp';temporary.write_text(json.dumps(result,indent=2)+'\n');temporary.replace(base/'scientific_gate.json')
    print(json.dumps({k:v for k,v in result.items() if k not in ('closure','pilot_summary')},indent=2));return 0 if result['pass'] else 1


if __name__=='__main__':sys.exit(main())
