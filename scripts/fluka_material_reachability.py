"""Audit material dependencies without discarding or modifying definitions."""


def material_reachability(inventory):
    """Resolve the full transitive material closure of parsed assignments.

    The input is the serializable registry inventory, not a meshed model.
    Every ordinary region must have an assignment. Lattice-cell placeholder
    material is outside this inventory; instantiated prototypes retain their
    ordinary-region materials. This is a dependency audit, not validation of
    duplicate definitions or of native FLUKA material properties.
    """
    definitions = inventory["parsed_materials"]
    assignments = inventory["material_assignments"]
    regions = inventory["regionDict"]["names"]
    if len(regions) != len(set(regions)):
        raise ValueError("duplicate ordinary region names")
    missing = sorted(set(regions) - set(assignments))
    if missing:
        raise ValueError("regions missing material assignment: " + ", ".join(missing))
    roots = {}
    for region in regions:
        assignment = assignments[region]
        material = assignment[0] if isinstance(assignment, (list, tuple)) else assignment
        if not isinstance(material, str) or not material:
            raise ValueError(f"invalid material assignment for {region}")
        roots.setdefault(material, []).append(region)
    ordered, ancestors = dependency_closure(definitions, sorted(roots))
    reachable = set(ordered)
    isotopes = [name for name, properties in definitions.items()
                if properties.get("massNumber") not in (None, 0, 0.0)]
    return {
        "scope": "all ordinary-region assignments and recursive compound constituents",
        "ordinary_region_count": len(regions),
        "direct_material_regions": roots,
        "dependency_order": ordered,
        "reachable_materials": sorted(reachable),
        "reachable_material_count": len(reachable),
        "unused_definitions": sorted(set(definitions) - reachable),
        "unused_definition_count": len(set(definitions) - reachable),
        "reachable_isotopes": sorted(set(isotopes) & reachable),
        "unused_isotopes": sorted(set(isotopes) - reachable),
        "dependency_witness": ancestors,
        "lattice_cell_assignment_semantics_validated": False,
        "native_material_properties_validated": False,
        "production_ready": False,
    }


def dependency_closure(definitions, roots):
    """Return dependency-first names and witness paths; reject missing/cyclic references."""
    state, ordered, ancestors = {}, [], {}

    def visit(name, chain):
        if name not in definitions:
            raise ValueError("undefined material dependency: " + " -> ".join(chain + [name]))
        if state.get(name) == "visiting":
            raise ValueError("cyclic material dependency: " + " -> ".join(chain + [name]))
        if state.get(name) == "done":
            return
        state[name] = "visiting"
        ancestors[name] = chain + [name]
        for component in definitions[name]["components"]:
            visit(component["material"], chain + [name])
        state[name] = "done"
        ordered.append(name)

    for root in roots:
        visit(root, [])
    return ordered, ancestors
