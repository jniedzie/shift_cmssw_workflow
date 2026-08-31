# SHIFT CMSSW workflow

This repository contains documentation and workflow scripts for running the SHIFT CMSSW production chain. The CMSSW fork lives [here](https://github.com/jniedzie/cmssw.git), and the generator fragments are in the [genproductions fork](https://github.com/jniedzie/genproductions.git).

All work in this repository is governed by [the project instructions](AGENTS.md).
In particular, Run 3 electronics timing and trigger rules are fixed constraints
to model faithfully, never parameters to change for improved SHIFT acceptance.

See [setup instructions](docs/setup_instructions.md) for configuration and environment setup, and [generation instructions](docs/generation_instructions.md) for local and Condor execution.

The provenance requirements and proposed architecture for adding the Run-3
LSS5 FLUKA model are tracked in [the living LHC geometry document](../SHIFT_LHC_GEOMETRY.md).
The initial interface-record prototype is `scripts/convert_fluka_crossings.py`.
The frozen IR1/ATLAS proxy and guarded FLUKA-to-GDML tooling live under
`models/lss5_ir1_atlas_proxy` and `scripts/convert_ir1_fluka_geometry.py`;
`scripts/extract_ir1_fluka_fields.py` extracts the separate field contract.
None of these claims that Run-3 IR5 geometry or magnetic fields are validated.
