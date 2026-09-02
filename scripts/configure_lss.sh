#!/usr/bin/env bash

# Validate one common LSS geometry/field contract and prepare the Python
# customisations consumed by Step 1 and Step 4. This file is sourced; it does
# not alter the caller's shell options.
configure_shift_lss() {
	SHIFT_LSS_MATERIAL_MODE="${SHIFT_LSS_MATERIAL_MODE:-none}"
	SHIFT_LSS_FIELD_MODE="${SHIFT_LSS_FIELD_MODE:-none}"
	SHIFT_LSS_GEOMETRY_PYTHON=""
	SHIFT_LSS_FIELD_IMPORT_PYTHON=""
	SHIFT_LSS_FIELD_ELEMENTS_PYTHON="None"
	SHIFT_LSS_SIMULATION_PYTHON=""
	SHIFT_LSS_RECONSTRUCTION_PYTHON=""
	SHIFT_LSS_AUDIT_PYTHON=""
	SHIFT_LSS_CONTRACT_SHA256=""
	SHIFT_LSS_DETAILED_TARGET_PROPAGATION_CMSSW=False
	lss_audit_gdml_sha256=""
	lss_audit_field_scale=""

	case "$SHIFT_LSS_MATERIAL_MODE" in
		none|external) ;;
		*) echo "ERROR: SHIFT_LSS_MATERIAL_MODE must be none or external" >&2; return 1 ;;
	esac
	case "$SHIFT_LSS_FIELD_MODE" in
		none|ir1_atlas_proxy) ;;
		*) echo "ERROR: SHIFT_LSS_FIELD_MODE must be none or ir1_atlas_proxy" >&2; return 1 ;;
	esac
	if [[ "$SHIFT_LSS_MATERIAL_MODE" == none && "$SHIFT_LSS_FIELD_MODE" == none ]]; then
		return 0
	fi

	for required_name in SHIFT_LSS_MODEL_ORIGIN_CM SHIFT_LSS_MODEL_TO_CMS; do
		if [[ -z "${!required_name:-}" ]]; then
			echo "ERROR: $required_name is required whenever LSS material or field is enabled" >&2
			return 1
		fi
	done
	IFS=',' read -r -a lss_origin_values <<< "$SHIFT_LSS_MODEL_ORIGIN_CM"
	IFS=',' read -r -a lss_rotation_values <<< "$SHIFT_LSS_MODEL_TO_CMS"
	if (( ${#lss_origin_values[@]} != 3 || ${#lss_rotation_values[@]} != 9 )); then
		echo "ERROR: SHIFT_LSS_MODEL_ORIGIN_CM and SHIFT_LSS_MODEL_TO_CMS require 3 and 9 comma-separated values" >&2
		return 1
	fi
	if ! python3 - "${lss_origin_values[@]}" "${lss_rotation_values[@]}" <<'PY'
import math
import sys

values = [float(value) for value in sys.argv[1:]]
if len(values) != 12 or not all(math.isfinite(value) for value in values):
    raise SystemExit("LSS transform values must be finite")
rotation = values[3:]
for row in range(3):
    for other in range(3):
        dot = sum(rotation[3 * row + column] * rotation[3 * other + column] for column in range(3))
        if abs(dot - (1.0 if row == other else 0.0)) > 1.0e-9:
            raise SystemExit("SHIFT_LSS_MODEL_TO_CMS must be orthonormal")
determinant = (
    rotation[0] * (rotation[4] * rotation[8] - rotation[5] * rotation[7])
    - rotation[1] * (rotation[3] * rotation[8] - rotation[5] * rotation[6])
    + rotation[2] * (rotation[3] * rotation[7] - rotation[4] * rotation[6])
)
if abs(determinant - 1.0) > 1.0e-9:
    raise SystemExit("SHIFT_LSS_MODEL_TO_CMS must have determinant +1")
PY
	then
		return 1
	fi

	if [[ "$SHIFT_LSS_MATERIAL_MODE" == external ]]; then
		for required_name in SHIFT_LSS_GDML_FILE SHIFT_LSS_GDML_SHA256 SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM SHIFT_LSS_MINIMUM_ABS_Z_CM \
			SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM; do
			if [[ -z "${!required_name:-}" ]]; then
				echo "ERROR: $required_name is required when SHIFT_LSS_MATERIAL_MODE=external" >&2
				return 1
			fi
		done
		if [[ "$SHIFT_LSS_GDML_FILE" == /* || "$SHIFT_LSS_GDML_FILE" == *..* ||
			! "$SHIFT_LSS_GDML_FILE" =~ ^[A-Za-z0-9_./+-]+$ ]]; then
			echo "ERROR: SHIFT_LSS_GDML_FILE must be a safe CMSSW FileInPath" >&2
			return 1
		fi
		if [[ -z "${CMSSW_SRC:-}" || ! -f "$CMSSW_SRC/$SHIFT_LSS_GDML_FILE" ]]; then
			echo "ERROR: SHIFT_LSS_GDML_FILE is not installed under CMSSW src: ${CMSSW_SRC:-unset}/$SHIFT_LSS_GDML_FILE" >&2
			return 1
		fi
		if [[ ! "$SHIFT_LSS_GDML_SHA256" =~ ^[0-9a-fA-F]{64}$ ]]; then
			echo "ERROR: SHIFT_LSS_GDML_SHA256 must be a 64-character SHA-256 digest" >&2
			return 1
		fi
		lss_gdml_checksum="$(sha256sum -- "$CMSSW_SRC/$SHIFT_LSS_GDML_FILE")"
		lss_gdml_checksum="${lss_gdml_checksum%% *}"
		if [[ "${lss_gdml_checksum,,}" != "${SHIFT_LSS_GDML_SHA256,,}" ]]; then
			echo "ERROR: installed LSS GDML checksum does not match SHIFT_LSS_GDML_SHA256" >&2
			return 1
		fi
		lss_audit_gdml_sha256="${SHIFT_LSS_GDML_SHA256,,}"
		IFS=',' read -r -a lss_artifact_origin_values <<< "$SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM"
		if (( ${#lss_artifact_origin_values[@]} != 3 )) || ! python3 - "${lss_artifact_origin_values[@]}" <<'PY'
import math
import sys

values = [float(value) for value in sys.argv[1:]]
if len(values) != 3 or not all(math.isfinite(value) for value in values):
    raise SystemExit("SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM must contain three finite values")
PY
		then
			return 1
		fi
		SHIFT_LSS_DETECTOR_ELEMENT_NAME="${SHIFT_LSS_DETECTOR_ELEMENT_NAME:-shiftLssExternal}"
		SHIFT_LSS_OVERLAP_TOLERANCE_CM="${SHIFT_LSS_OVERLAP_TOLERANCE_CM:-0.001}"
		if [[ ! "$SHIFT_LSS_DETECTOR_ELEMENT_NAME" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]]; then
			echo "ERROR: SHIFT_LSS_DETECTOR_ELEMENT_NAME must be a safe identifier" >&2
			return 1
		fi
		# This is Python source passed as one quoted cmsDriver argument.
		# shellcheck disable=SC2089
		SHIFT_LSS_GEOMETRY_PYTHON="; from PhysicsTools.ShiftLssGeometry.shiftLssExternalGeometry_cff import customiseShiftLssExternalGeometry; process = customiseShiftLssExternalGeometry(process, gdmlFile='$SHIFT_LSS_GDML_FILE', artifactOriginInModelCm=($SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM), modelOriginCm=($SHIFT_LSS_MODEL_ORIGIN_CM), modelToCms=($SHIFT_LSS_MODEL_TO_CMS), minimumAbsZCm=$SHIFT_LSS_MINIMUM_ABS_Z_CM, detectorElementName='$SHIFT_LSS_DETECTOR_ELEMENT_NAME', overlapToleranceCm=$SHIFT_LSS_OVERLAP_TOLERANCE_CM, checkOverlaps=True)"
		SHIFT_LSS_DETAILED_TARGET_PROPAGATION_CMSSW=True
	fi

	SHIFT_LSS_GEANT4E_MOMENTUM_LIMIT_GEV="${SHIFT_LSS_GEANT4E_MOMENTUM_LIMIT_GEV:-0.05}"
	SHIFT_LSS_GEANT4E_MAXIMUM_STEP_LENGTH_MM="${SHIFT_LSS_GEANT4E_MAXIMUM_STEP_LENGTH_MM:-2.0}"
	SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM="${SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM:-1100.0}"
	SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM="${SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM:-2500.0}"
	SHIFT_LSS_OVERLAP_TOLERANCE_CM="${SHIFT_LSS_OVERLAP_TOLERANCE_CM:-0.001}"
	if ! python3 - "$SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM" "$SHIFT_LSS_GEANT4E_MOMENTUM_LIMIT_GEV" \
		"$SHIFT_LSS_GEANT4E_MAXIMUM_STEP_LENGTH_MM" "$SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM" \
		"$SHIFT_LSS_OVERLAP_TOLERANCE_CM" <<'PY'
import math
import sys

values = [float(value) for value in sys.argv[1:]]
if not all(math.isfinite(value) and value > 0.0 for value in values):
    raise SystemExit("LSS material boundary, Geant4e limits, and overlap tolerance must be finite and positive")
PY
	then
		return 1
	fi

	if [[ "$SHIFT_LSS_FIELD_MODE" == ir1_atlas_proxy ]]; then
		if [[ -z "${SHIFT_LSS_FIELD_SCALE:-}" ]]; then
			echo "ERROR: SHIFT_LSS_FIELD_SCALE is required for the provisional IR1/ATLAS field; its sign records the reviewed polarity" >&2
			return 1
		fi
		if ! python3 - "$SHIFT_LSS_FIELD_SCALE" <<'PY'
import math
import sys

value = float(sys.argv[1])
if not math.isfinite(value) or value == 0.0:
    raise SystemExit("SHIFT_LSS_FIELD_SCALE must be finite and nonzero")
PY
		then
			return 1
		fi
		SHIFT_LSS_FIELD_IMPORT_PYTHON="; from PhysicsTools.ShiftMuonSegments.shiftLssIr1AtlasProxy_cff import shiftLssIr1AtlasProxyFieldElements"
		SHIFT_LSS_FIELD_ELEMENTS_PYTHON="shiftLssIr1AtlasProxyFieldElements(modelOriginCm=($SHIFT_LSS_MODEL_ORIGIN_CM), modelToCms=($SHIFT_LSS_MODEL_TO_CMS), fieldScale=$SHIFT_LSS_FIELD_SCALE)"
		lss_audit_field_scale="$SHIFT_LSS_FIELD_SCALE"
	fi

	if [[ "$SHIFT_LSS_FIELD_MODE" != none ]]; then
		SHIFT_LSS_SIMULATION_PYTHON="$SHIFT_LSS_FIELD_IMPORT_PYTHON; from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseShiftLssMagneticField; process = customiseShiftLssMagneticField(process, fieldElements=$SHIFT_LSS_FIELD_ELEMENTS_PYTHON)"
	fi
	SHIFT_LSS_CONTRACT_SHA256="$(python3 - "$SHIFT_LSS_MATERIAL_MODE" "$SHIFT_LSS_FIELD_MODE" \
		"$lss_audit_gdml_sha256" "$lss_audit_field_scale" "${SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM:-}" "$SHIFT_LSS_MODEL_ORIGIN_CM" \
		"$SHIFT_LSS_MODEL_TO_CMS" "${SHIFT_LSS_MINIMUM_ABS_Z_CM:-}" \
		"$SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM" "$SHIFT_LSS_GEANT4E_MOMENTUM_LIMIT_GEV" \
		"$SHIFT_LSS_GEANT4E_MAXIMUM_STEP_LENGTH_MM" "$SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM" <<'PY'
import hashlib
import json
import sys

contract = dict(zip((
    "material_mode", "field_mode", "gdml_sha256", "field_scale", "artifact_origin_in_model_cm", "model_origin_cm",
    "model_to_cms", "minimum_abs_z_cm", "material_boundary_abs_z_cm",
    "geant4e_momentum_limit_gev", "geant4e_maximum_step_length_mm",
    "geant4e_maximum_path_length_cm",
), sys.argv[1:]))
payload = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("ascii")
print(hashlib.sha256(payload).hexdigest())
PY
)"
	SHIFT_LSS_AUDIT_PYTHON="; process.shiftLssWorkflowContract = cms.PSet(contractVersion=cms.uint32(2), contractSha256=cms.string('$SHIFT_LSS_CONTRACT_SHA256'), materialMode=cms.string('$SHIFT_LSS_MATERIAL_MODE'), fieldMode=cms.string('$SHIFT_LSS_FIELD_MODE'), gdmlSha256=cms.string('$lss_audit_gdml_sha256'), fieldScale=cms.string('$lss_audit_field_scale'), artifactOriginInModelCm=cms.string('${SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM:-}'), modelOriginCm=cms.vdouble($SHIFT_LSS_MODEL_ORIGIN_CM), modelToCms=cms.vdouble($SHIFT_LSS_MODEL_TO_CMS))"
	SHIFT_LSS_SIMULATION_PYTHON="$SHIFT_LSS_GEOMETRY_PYTHON$SHIFT_LSS_SIMULATION_PYTHON"
	SHIFT_LSS_RECONSTRUCTION_PYTHON="$SHIFT_LSS_GEOMETRY_PYTHON$SHIFT_LSS_FIELD_IMPORT_PYTHON; from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseShiftLssTransport; process = customiseShiftLssTransport(process, fieldElements=$SHIFT_LSS_FIELD_ELEMENTS_PYTHON, materialBoundaryAbsZCm=$SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM, geant4eMomentumLimitGeV=$SHIFT_LSS_GEANT4E_MOMENTUM_LIMIT_GEV, geant4eMaximumStepLengthMm=$SHIFT_LSS_GEANT4E_MAXIMUM_STEP_LENGTH_MM, geant4eMaximumPathLengthCm=$SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM)"
	SHIFT_LSS_SIMULATION_PYTHON="$SHIFT_LSS_SIMULATION_PYTHON$SHIFT_LSS_AUDIT_PYTHON"
	SHIFT_LSS_RECONSTRUCTION_PYTHON="$SHIFT_LSS_RECONSTRUCTION_PYTHON$SHIFT_LSS_AUDIT_PYTHON"
	# The Python strings are data consumed inside quoted cmsDriver arguments.
	# shellcheck disable=SC2090
	export SHIFT_LSS_MATERIAL_MODE SHIFT_LSS_FIELD_MODE SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM SHIFT_LSS_MODEL_ORIGIN_CM SHIFT_LSS_MODEL_TO_CMS \
		SHIFT_LSS_DETAILED_TARGET_PROPAGATION_CMSSW SHIFT_LSS_SIMULATION_PYTHON \
		SHIFT_LSS_RECONSTRUCTION_PYTHON SHIFT_LSS_CONTRACT_SHA256
}
