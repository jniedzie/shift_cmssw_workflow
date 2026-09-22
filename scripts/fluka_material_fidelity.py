"""Scoped diagnostic corrections for pyg4ometry FLUKA element conversion.

No installed package, input deck, or production artifact is modified.  This
preserves registry Z, density and non-integer molar masses instead of the
upstream CARBON->boron typo and integer truncation.  It does NOT establish
native FLUKA defaults, resolve repeated MATERIAL cards, or certify physics.

Use the context around BOTH Reader construction and fluka2Geant4 conversion.
All monkeypatches are restored on success or exception.  Like the upstream
converter this context is single-threaded; it is not a process-global service.
"""

from contextlib import contextmanager
import importlib
import inspect
import math
from types import SimpleNamespace

from fluka_material_reachability import dependency_closure


class MaterialFidelityError(ValueError):
    """Source information is insufficient for a faithful element conversion."""


def _positive(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise MaterialFidelityError(f"{label} must be a positive finite number") from error
    if not math.isfinite(value) or value <= 0:
        raise MaterialFidelityError(f"{label} must be a positive finite number")
    return value


def _integer(value, label):
    value = _positive(value, label)
    if not value.is_integer():
        raise MaterialFidelityError(f"{label} must be an integer")
    return int(value)


def audit_material_cards(cards):
    """Report ambiguities without choosing native duplicate/override semantics."""
    definitions = {}
    unsupported = []
    for index, card in enumerate(cards):
        if card.keyword != "MATERIAL":
            continue
        definitions.setdefault(card.sdum, []).append({
            "card_index": index,
            "what": [getattr(card, f"what{i}") for i in range(1, 7)],
        })
        alternate = card.what5
        if alternate not in (None, 0, 0.0) and (not isinstance(alternate, (int, float)) or alternate > 2):
            unsupported.append({"name": card.sdum, "what5": alternate,
                                "reason": "alternate ionisation material not converted"})
    duplicates = {name: values for name, values in definitions.items() if len(values) > 1}
    return {"duplicate_material_definitions": duplicates,
            "unsupported_material_options": unsupported,
            "native_material_semantics_validated": False,
            "production_ready": False}


@contextmanager
def material_fidelity_guard(ledger, *, required_materials=None):
    """Use source registry element properties, with explicit unresolved gates.

    Natural-element defaults outside pyg4ometry's FLUKA builtin table retain
    the upstream periodic table values and are labelled unverified.  Builtin
    compounds retain the upstream NIST mapping, explicitly unverified against
    FLUKA.  A selected isotope without an explicit molar mass is rejected:
    nucleon count is NOT the molar mass.  Explicit WHAT(2) is preserved, but
    native FLUKA's effective value still requires validation.

    When explicit required material names are supplied, export only their
    complete dependency closure. Unused definitions stay in the source and
    are inventoried; unsupported USED isotopes still fail. None audits all
    definitions as before. No registry is mutated by this selection.
    """
    upstream = importlib.import_module("pyg4ometry.convert.fluka2g4materials")
    conversion = importlib.import_module("pyg4ometry.convert.fluka2Geant4")
    flu = importlib.import_module("pyg4ometry.fluka.material")
    g4 = importlib.import_module("pyg4ometry.geant4")
    original_from_card = inspect.getattr_static(flu.Material, "fromCard")
    original_map = upstream.makeFlukaToG4MaterialsMap
    original_bound_map = conversion._makeFlukaToG4MaterialsMap
    ledger.setdefault("elements", [])
    ledger.setdefault("unverified_defaults", [])
    ledger["native_material_semantics_validated"] = False
    ledger["production_ready"] = False
    defaults_by_z = {int(z): mass for _, mass, z, _ in flu._PREDEFINED_ELEMENTS if z > 0}

    class Converter(upstream._FlukaToG4MaterialConverter):
        def addPredefinedElements(self):
            # Resolve the ACTUAL registry object, including user redefinitions.
            # Pre-seeding by a builtin name silently reused the wrong Z/A.
            pass

        def _properties(self, material):
            z = _integer(material.atomicNumber, f"{material.name}: Z")
            isotope = getattr(material, "massNumber", None)
            isotope = None if isotope in (None, 0, 0.0) else _integer(isotope, f"{material.name}: isotope")
            explicit = getattr(material, "atomicMass", None)
            if explicit not in (None, 0, 0.0):
                mass = _positive(explicit, f"{material.name}: molar mass")
                source = "FLUKA builtin registry" if isinstance(material, flu.BuiltIn) else "explicit source WHAT(2)"
            elif isotope is not None:
                raise MaterialFidelityError(
                    f"{material.name}: isotope {isotope} requires authoritative molar mass; "
                    "will not substitute nucleon count or natural-element mass")
            elif z in defaults_by_z:
                mass, source = defaults_by_z[z], "pyg4ometry FLUKA builtin defaults by Z"
            else:
                mass = _positive(self.periodicTable.atomicMassFromZ(z), f"{material.name}: default molar mass")
                source = "upstream periodic table; native FLUKA default unverified"
                ledger["unverified_defaults"].append({"name": material.name, "Z": z, "molar_mass": mass,
                                                       "reason": source})
            rows = self.periodicTable.table
            symbols = rows["Symbol"][rows["AtomicNumber"] == z]
            if len(symbols) != 1:
                raise MaterialFidelityError(f"{material.name}: no unique chemical symbol for Z={z}")
            return z, mass, isotope, str(symbols.iloc[0]), source

        def _convert_element(self, name, material):
            if name != material.name:
                raise MaterialFidelityError("registry key does not match material name")
            z, mass, isotope, symbol, source = self._properties(material)
            density = _positive(material.density, f"{name}: density")
            element_name = self._mangleElementName(name)
            if isotope is None:
                element = g4.ElementSimple(element_name, symbol, z, mass, registry=self.greg)
                converted = g4.MaterialSingleElement(name, z, mass, density, registry=self.greg)
            else:
                isotopic = g4.Isotope(f"{element_name}_isotope_{isotope}", z, isotope, mass, self.greg)
                element = g4.ElementIsotopeMixture(element_name, symbol, 1, registry=self.greg)
                element.add_isotope(isotopic, 1.0)
                converted = g4.MaterialCompound(name, density, 1, registry=self.greg)
                converted.add_element_massfraction(element, 1.0)
            self.g4elements[name] = element
            self.g4elementKeysMangled[element_name] = element
            self.g4materials[name] = converted
            ledger["elements"].append({"name": name, "symbol": symbol, "Z": z,
                "molar_mass_g_mole": mass, "mass_number": isotope,
                "density_g_cm3": density, "property_source": source})

        def convertBuiltin(self, name, material):
            if material.atomicNumber is not None and material.atomicNumber > 0:
                self._convert_element(name, material)
            else:
                super().convertBuiltin(name, material)
                ledger["unverified_defaults"].append({"name": name,
                    "reason": "upstream NIST builtin compound or vacuum mapping; native equivalence unverified"})

        def convertElement(self, name, material):
            self._convert_element(name, material)

        def convertCompound(self, compound):
            _positive(compound.density, f"{compound.name}: density")
            if not compound.fractions:
                raise MaterialFidelityError(f"{compound.name}: empty composition")
            weighted = []
            for part, weight in compound.fractions:
                weight = _positive(weight, f"{compound.name}: fraction for {part.name}")
                if compound.fractionType == "atomic" and isinstance(part, flu.Compound):
                    raise MaterialFidelityError(
                        f"{compound.name}: molecular atomic-fraction nesting is not implemented")
                if compound.fractionType == "volume":
                    weight *= _positive(part.density, f"{part.name}: constituent density")
                weighted.append(_positive(weight, f"{compound.name}: weighted fraction"))
            try:
                total = math.fsum(weighted)
            except OverflowError as error:
                raise MaterialFidelityError(f"{compound.name}: total weighting overflow") from error
            _positive(total, f"{compound.name}: total weighting")
            if compound.fractionType not in ("atomic", "mass", "volume"):
                raise MaterialFidelityError(f"{compound.name}: unsupported fraction type")
            super().convertCompound(compound)

        def convertAtomicFractionCompound(self, name, compound):
            # Atom counts become mass fractions using the SAME masses as the
            # serialized elements, including explicit source isotope masses.
            weighted = [(part, _positive(float(count) * self._properties(part)[1],
                                        f"{name}: atomic mass weighting"))
                        for part, count in compound.fractions]
            try:
                total = math.fsum(weight for _, weight in weighted)
            except OverflowError as error:
                raise MaterialFidelityError(f"{name}: atomic total weighting overflow") from error
            _positive(total, f"{name}: atomic total weighting")
            material = self._makeBaseCompoundMaterial(name, compound)
            for part, weight in weighted:
                material.add_element_massfraction(self.g4elements[part.name], weight / total)
            self.g4materials[name] = material

    @classmethod
    def from_card(cls, card, registry):
        return cls(card.sdum, card.what1, card.what3,
                   massNumber=card.what6, atomicMass=card.what2, flukaregistry=registry)

    def make_map(freg, greg):
        if required_materials is None:
            selected = freg
        else:
            signatures, visiting = {}, set()

            def signature(material):
                identity = id(material)
                if identity in visiting:
                    raise MaterialFidelityError(
                        f"{material.name}: cyclic constituent object binding; native material semantics must be resolved")
                if identity not in signatures:
                    visiting.add(identity)
                    try:
                        signatures[identity] = (
                            type(material).__name__,
                            tuple(getattr(material, key, None) for key in
                                  ("atomicNumber", "atomicMass", "massNumber", "density", "fractionType")),
                            tuple((part.name, float(weight), signature(part))
                                  for part, weight in getattr(material, "fractions", [])))
                    finally:
                        visiting.remove(identity)
                return signatures[identity]

            try:
                root_names = list(required_materials)
            except TypeError as error:
                raise MaterialFidelityError("explicit material roots must be a collection of names") from error
            if not root_names or not all(isinstance(name, str) and name for name in root_names):
                raise MaterialFidelityError("explicit material roots must be nonempty valid names")
            roots = sorted(set(root_names))
            graph = {name: {"components": [{"material": part.name} for part, _ in
                                          getattr(material, "fractions", [])]}
                     for name, material in freg.materials.items()}
            ordered, paths = dependency_closure(graph, roots)
            ledger["dependency_selection"] = {
                "required_materials": roots,
                "dependency_order": ordered,
                "dependency_witness": paths,
                "unused_definitions_not_exported": sorted(set(graph) - set(ordered)),
                "source_definitions_modified": False,
                "native_material_properties_validated": False,
            }
            for name in ordered:
                for part, _ in getattr(freg.materials[name], "fractions", []):
                    if signature(part) != signature(freg.materials[part.name]):
                        raise MaterialFidelityError(
                            f"{name}: constituent {part.name} differs from the current registry definition; "
                            "native repeated-definition semantics must be resolved")
            selected = SimpleNamespace(materials={name: freg.materials[name] for name in ordered})
        return Converter(selected, greg).g4materials

    flu.Material.fromCard = from_card
    upstream.makeFlukaToG4MaterialsMap = make_map
    conversion._makeFlukaToG4MaterialsMap = make_map
    try:
        yield
    finally:
        flu.Material.fromCard = original_from_card
        upstream.makeFlukaToG4MaterialsMap = original_map
        conversion._makeFlukaToG4MaterialsMap = original_bound_map
