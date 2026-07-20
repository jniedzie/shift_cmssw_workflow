# Generation instructions

## Setup

Event generation should be done from CMSSW_X_Y_Z/src directory. Create symlinks to scripts from there and make sure they have the correct access set:

```bash
ln -s ../../shift_cmssw_workflow/*.sh .
chmod 777 *prepare_generation_config*.sh
```

Run scram to create all necessary links:

```bash
scram b -j
```

## Running

Run the steps one by one:

1. Generation

```bash
. run_step1_generation.sh
```

2. Digi to raw

```bash
. run_step2_digi_raw.sh
```

3. Reco and MiniAOD creation

```bash
. run_step3_reco_miniAOD.sh
```

4. NanoAOD creation

```bash
. run_step4_nanoAOD.sh
```
