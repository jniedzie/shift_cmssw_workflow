# Generation instructions

## Setup

Event generation should be done from CMSSW_X_Y_Z/src directory. Create a symlinks to the generation and running scripts from there and make sure they have the correct access set:

```bash
ln -s ../../shift_cmssw_workflow/prepare_generation_config.sh .
ln -s ../../shift_cmssw_workflow/run_generation.sh .
chmod 777 prepare_generation_config.sh
chmod 777 run_generation.sh
```

Run scram to create all necessary links:

```bash
scram b -j
```

## Running

Source the script to generate the config:

```bash
. prepare_generation_config.sh
```

Then, run this config (it's the simple `cmsRun` command, wrapped in a bash script for convenience):

```bash
. run_generation.sh
```

This will create a ROOT file that you can look at to verify everything was generated properly.

