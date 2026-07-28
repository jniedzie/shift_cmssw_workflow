# CMSSW 17 setup

This workflow is intended to run in a CMSSW 17 release with the generator and NanoAOD packages available. The workflow does not modify the CMSSW source copy of the Pythia fragment: `scripts/setup_cmssw.sh` creates a symbolic link from `Configuration/GenProduction/python` to the fragment tracked in this repository.

## Create the release

Choose the CMSSW 17 release required by the EXONanoAOD instructions. For a development release, use the currently recommended `CMSSW_17_0_X` patch release instead of copying the example literally:

```bash
cmsrel CMSSW_17_0_X_2026-07-26-0000
cd CMSSW_17_0_X_2026-07-26-0000/src
cmsenv
```

## Add the required CMSSW packages

From the release `src` directory, add the package areas used by the workflow:

```bash
git cms-addpkg PhysicsTools/NanoAOD
scram b -j 8
```

## Point the workflow at CMSSW 17

Edit `config/workflow.env`:

```bash
CMSSW_SRC="/absolute/path/to/CMSSW_17_0_XX/src"
PYTHIA_CONFIG="Configuration/GenProduction/Charmonium_FixedTarget_pThat_0to1GeV_13p6TeV_pythia8_cff.py"
```

Every workflow job sources `scripts/setup_cmssw.sh`. That script runs `cmsenv`, creates the local `Configuration/GenProduction/python` directory if needed, and links the tracked fragment from `shift_cmssw_workflow/fragments/` into it.

You can verify the link before production:

```bash
source scripts/setup_cmssw.sh
readlink -f "$CMSSW_SRC/Configuration/GenProduction/python/Charmonium_FixedTarget_pThat_0to1GeV_13p6TeV_pythia8_cff.py"
```

The target should be the fragment in this repository. The EXONanoAOD customization will be added separately once its exact CMSSW entry point is confirmed.
