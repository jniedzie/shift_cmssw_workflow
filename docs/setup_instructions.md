# Complete CMSSW and SHIFT workflow setup

This guide creates a self-contained checkout capable of running the SHIFT
production workflow on a new CMS-compatible computing platform. It assumes a
working CVMFS CMS installation (`/cvmfs/cms.cern.ch`) and Git access to the
two repositories below.

Use these branches together:

| Component | Repository | Branch |
| --- | --- | --- |
| Workflow scripts and generator fragment | `git@github.com:jniedzie/shift_cmssw_workflow.git` | `main` |
| CMSSW source modifications, including muon segments | `git@github.com:jniedzie/cmssw.git` | `shift-muon-segments` |

The CMSSW branch is based on the development-release tag
`CMSSW_17_0_X_2026-07-26-0000` and currently resolves to commit
`e1560397afd`. Do **not** use an unmodified CMSSW release or a generic
`CMSSW_17_0_X` branch: the `shift-muon-segments` branch contains the required
`PhysicsTools/ShiftMuonSegments` package and the local simulation changes.

## 1. Bootstrap CMSSW

Choose a writable work area and initialize the CMS environment:

```bash
source /cvmfs/cms.cern.ch/cmsset_default.sh
mkdir -p "$HOME/shift_cmssw"
cd "$HOME/shift_cmssw"
cmsrel CMSSW_17_0_X_2026-07-26-0000
cd CMSSW_17_0_X_2026-07-26-0000/src
cmsenv
```

## 2. Materialize the release package areas

CMSSW uses a sparse source checkout. First materialize the standard package
areas from the release you just created:

```bash
git cms-addpkg Configuration/GenProduction
git cms-addpkg PhysicsTools/NanoAOD
git cms-addpkg SimG4CMS/Muon
git cms-addpkg SimG4Core/Application
git cms-addpkg SimG4Core/Generators
```

This ordering is intentional: the fork branch in the next step modifies
`Configuration/GenProduction` and `SimG4Core`, so those source areas must
already exist when Git switches to the fork revision.

## 3. Switch to the SHIFT CMSSW fork

Add the personal fork as a remote, fetch it, and switch the release source
tree to the required branch:

```bash
git remote add my-cmssw https://github.com/jniedzie/cmssw.git
git fetch my-cmssw shift-muon-segments
git switch --create shift-muon-segments --track my-cmssw/shift-muon-segments
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
```

The first command must print `shift-muon-segments`. The second should print
`e1560397afd` (or a deliberately newer commit on that same branch).

The `PhysicsTools/ShiftMuonSegments` package exists only on this fork, so add
its source area only after switching branches:

```bash
git cms-addpkg PhysicsTools/ShiftMuonSegments
```

`PhysicsTools/ShiftMuonSegments` is taken from the checked-out
`my-cmssw/shift-muon-segments` branch; it is not an official CMSSW package.

`Configuration` needs special care. Do not add the entire top-level
`Configuration` area: `Configuration/GenProduction` is the required source
area. On its first run, `scripts/setup_cmssw.sh` creates the small local
`Configuration/BuildFile.xml` required to build the workflow's linked
generator fragment. This avoids the incomplete-Configuration problem caused
by manually copying only pieces of that package.

The fork already supplies every non-standard CMSSW source component needed by
this production:

- `PhysicsTools/ShiftMuonSegments`, including the `ShiftMuonSegmentsTableProducer`,
  counter, Python configuration, and `BuildFile.xml` files;
- modifications under `SimG4Core/Generators` and `SimG4Core/Application` used
  for the SHIFT Geant4 primary handling and optional muon-primary diagnostics;
- the local `Configuration/GenProduction` additions present on the branch.

Do not copy any of these directories by hand. The branch supplies the forked
files; the `git cms-addpkg` commands only make their package areas present in
the sparse CMSSW worktree.

Build the release after switching branches. Set the job count for the machine
you are using:

```bash
scram b -j 8
```

## 4. Clone the workflow repository

From the same parent directory as the CMSSW release:

```bash
cd "$HOME/shift_cmssw"
git clone git@github.com:jniedzie/shift_cmssw_workflow.git
cd shift_cmssw_workflow
git switch main
git pull --ff-only origin main
git rev-parse --abbrev-ref HEAD
```

The result must be `main`. If SSH access is unavailable, use the corresponding
`https://github.com/jniedzie/...git` URLs for both clones.

## 5. Configure the production

Copy nothing into CMSSW manually. Edit only `config/workflow.env` in the
workflow checkout. At minimum, set `CMSSW_SRC` to the absolute `src` path and
choose a writable output location:

```bash
CMSSW_SRC="/absolute/path/shift_cmssw/CMSSW_17_0_X_2026-07-26-0000/src"
SAMPLE_BASE="/path/writable/by/jobs/shift_cmssw"
SAMPLE_NAME="jpsi"
CAMPAIGN_NAME="Charmonium_FixedTarget_pThat_0to1GeV_13p6TeV_smallTest_beamB"
```

For grid or batch production, `SAMPLE_BASE` is normally a site-specific PNFS
or EOS user area. It must be writable by the process that runs the jobs. The
workflow creates `samples/`, `configs/`, and `logs/` underneath it.

Keep these workflow settings unless you intentionally need different content:

```bash
PYTHIA_CONFIG="Configuration/GenProduction/Charmonium_FixedTarget_pThat_0to1GeV_13p6TeV_pythia8_cff.py"
AOD_TO_EXONANO_CUSTOMISE="PhysicsTools/ShiftMuonSegments/shiftMuonSegments_customise.customise"
ENABLE_EXONANOAOD="${ENABLE_EXONANOAOD:-0}"
```

`ENABLE_EXONANOAOD=0` produces standard NanoAOD (`PAT,NANO`) plus the focused
SHIFT tables and required generator columns. Set it to `1` only for an
EXONanoAOD (`PAT,NANO:@EXO`) comparison. The Shift customization is applied in
either case.

## 6. Verify the integration before production

Run these commands from the workflow checkout:

```bash
source /cvmfs/cms.cern.ch/cmsset_default.sh
source scripts/setup_cmssw.sh
python3 -c 'from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customise; print("ShiftMuonSegments import OK")'
readlink -f "$CMSSW_SRC/$PYTHIA_CONFIG"
```

`setup_cmssw.sh` performs the required integration automatically: it runs
`cmsenv`, links the generator fragment tracked in
`fragments/` into `Configuration/GenProduction/python/`, and incrementally
builds CMSSW when needed. The final `readlink` must resolve to the workflow
repository's `fragments/Charmonium_FixedTarget_pThat_0to1GeV_13p6TeV_pythia8_cff.py`.

If you change any C++ file under `PhysicsTools/ShiftMuonSegments` or the
local simulation code, rebuild before submitting jobs:

```bash
cd "$CMSSW_SRC"
cmsenv
scram b -j 8 PhysicsTools/ShiftMuonSegments SimG4Core/Generators
```

## 7. Run the chain

Return to the workflow checkout and run the stages in order:

```bash
./run_step1_generation.sh 0 10
./run_step2_digi_raw.sh 0 10
./run_step3_aod.sh 0 10
./run_step4_exonanoAOD.sh 0 10
```

See [generation instructions](generation_instructions.md) for output names,
Condor execution, and NanoAOD-content options. Before a long submission,
always run one local chunk successfully on the target platform; this catches
site-specific CVMFS, storage, and runtime-library issues early.
