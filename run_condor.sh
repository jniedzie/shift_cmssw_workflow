#!/bin/bash

./scripts/prepare_condor.sh
condor_submit condor/shift_cmssw.sub
