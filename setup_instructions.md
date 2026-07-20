# Setup instructions

## CMSSW

I'm picking a very high version of CMSSW to include Phase-II stuff:

```bash
source /cvmfs/cms.cern.ch/cmsset_default.sh
cmsrel CMSSW_20_1_0_pre1
cd CMSSW_20_1_0_pre1/src/
cmsenv
git config --global user.github jniedzie
git cms-init
```

Then, I create a branch for my modifications:

```bash
git checkout -b shift/fixed-target-gen
git checkout shift/fixed-target-gen 2>/dev/null || git checkout -b shift/fixed-target-gen from-CMSSW_20_1_0_pre1
```

I add the Configuration module:

```bash
git cms-addpkg Configuration/GenProduction

mkdir -p Configuration/GenProduction/python
touch Configuration/GenProduction/python/.gitkeep
git add Configuration/GenProduction/python/.gitkeep
git add Configuration/GenProduction/python/.gitkeep

git commit -m "test: scaffold GenProduction dir"
git push my-cmssw shift/fixed-target-gen
```

## Genproductions

In some other directory, I clone `genproductions`, which will contain gen fragments:

```bash
git clone https://github.com/jniedzie/genproductions.git
cd genproductions
```

Setting the remotes and branches:
 
```bash
git remote add upstream https://github.com/cms-sw/genproductions.git
git checkout -b shift/fixed-target-fragments
git remote set-url --push upstream disabled
```

## shift_cmssw_workflow

Finally, I create a repo to hold these instructions, commands, some validation scripts, etc.

```bash
init shift_cmssw_workflow
shift_cmssw_workflow/
git remote add origin git@github.com:jniedzie/shift_cmssw_workflow.git

git push --set-upstream origin main
 1047  cd ../CMSSW_20_1_0_pre1/src/

 1060  history