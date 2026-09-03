#!/usr/bin/env python3
"""Render four views and portable 3D files from the selected geometry OBJ."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import sys
import zipfile
from xml.sax.saxutils import quoteattr


@dataclass
class Mesh:
    name: str
    category: str
    vertices: list
    triangles: list


COLORS = {
    "cms_muon": (0.05, 0.32, 0.78, 0.48),
    "cms_calo": (0.91, 0.53, 0.00, 0.62),
    "cms_tracker": (0.00, 0.55, 0.43, 0.82),
    "cms_forward": (0.43, 0.20, 0.72, 0.55),
    "cms_cavern": (0.42, 0.47, 0.54, 0.14),
    "lss_tunnel": (0.34, 0.38, 0.44, 0.24),
    "lss_magnet": (0.82, 0.12, 0.10, 0.86),
    "lss_beamline": (0.08, 0.09, 0.11, 0.92),
}

LABELS = {
    "cms_muon": "CMS muon system",
    "cms_calo": "CMS calorimeters",
    "cms_tracker": "CMS tracker",
    "cms_forward": "CMS forward detectors",
    "cms_cavern": "CMS cavern",
    "lss_tunnel": "tunnel and concrete",
    "lss_magnet": "magnets and shielding",
    "lss_beamline": "beam pipe and beam-line parts",
}


def sanitize(text):
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return result if result and not result[0].isdigit() else "node_" + result


def load_obj(path):
    vertices = []
    raw_meshes = []
    current = None

    def finish():
        nonlocal current
        if current and current["triangles"]:
            raw_meshes.append(current)
        current = None

    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            fields = line.split()
            if not fields or fields[0] == "#":
                continue
            if fields[0] == "v":
                if len(fields) != 4:
                    raise RuntimeError(f"{path}:{line_number}: invalid vertex")
                vertices.append(tuple(float(value) for value in fields[1:]))
            elif fields[0] == "o":
                finish()
                current = {"name": " ".join(fields[1:]), "category": "", "triangles": []}
            elif fields[0] in ("g", "usemtl"):
                if current is None:
                    raise RuntimeError(f"{path}:{line_number}: category before object")
                current["category"] = fields[1]
            elif fields[0] == "f":
                if current is None or len(fields) != 4:
                    raise RuntimeError(f"{path}:{line_number}: expected a triangular object face")
                current["triangles"].append(tuple(int(value.split("/")[0]) - 1 for value in fields[1:]))
    finish()

    meshes = []
    for raw in raw_meshes:
        if raw["category"] not in COLORS:
            raise RuntimeError(f"unknown visualization category {raw['category']!r}")
        used = sorted({index for triangle in raw["triangles"] for index in triangle})
        remap = {old: new for new, old in enumerate(used)}
        meshes.append(
            Mesh(
                name=raw["name"],
                category=raw["category"],
                vertices=[vertices[index] for index in used],
                triangles=[tuple(remap[index] for index in triangle) for triangle in raw["triangles"]],
            )
        )
    return meshes


def write_collada(path, meshes):
    effects = "\n".join(
        f'''    <effect id="{name}-effect"><profile_COMMON><technique sid="common"><phong>'''
        f'''<emission><color>0 0 0 1</color></emission>'''
        f'''<ambient><color>{0.2 * red:.5f} {0.2 * green:.5f} {0.2 * blue:.5f} 1</color></ambient>'''
        f'''<diffuse><color>{red:.5f} {green:.5f} {blue:.5f} 1</color></diffuse>'''
        f'''<specular><color>0.12 0.12 0.12 1</color></specular>'''
        f'''<shininess><float>12</float></shininess>'''
        f'''</phong></technique></profile_COMMON></effect>'''
        for name, (red, green, blue, alpha) in COLORS.items()
    )
    materials = "\n".join(
        f'''    <material id="{name}-material" name="{name}"><instance_effect url="#{name}-effect"/></material>'''
        for name in COLORS
    )
    with path.open("w", encoding="utf-8") as output:
        output.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        output.write('<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n')
        output.write('  <asset><contributor><authoring_tool>SHIFT TGeo Collada exporter</authoring_tool></contributor>')
        output.write('<created>2026-09-02T00:00:00Z</created><modified>2026-09-02T00:00:00Z</modified>')
        output.write('<unit name="meter" meter="1"/><up_axis>Y_UP</up_axis></asset>\n')
        output.write("  <library_effects>\n" + effects + "\n  </library_effects>\n")
        output.write("  <library_materials>\n" + materials + "\n  </library_materials>\n")
        output.write("  <library_geometries>\n")
        for index, mesh in enumerate(meshes):
            geometry_id = f"geometry-{index}-{sanitize(mesh.name)}"
            positions_id = geometry_id + "-positions"
            normals_id = geometry_id + "-normals"
            vertices_id = geometry_id + "-vertices"
            # Viewer coordinates: source z is horizontal, source y is up, and
            # negative source x is depth.  The sign keeps a right-handed frame.
            display_vertices = [(vertex[2], vertex[1], -vertex[0]) for vertex in mesh.vertices]
            coordinates = " ".join(f"{coordinate:.7g}" for vertex in display_vertices for coordinate in vertex)
            normals = []
            for triangle in mesh.triangles:
                one, two, three = (display_vertices[vertex] for vertex in triangle)
                edge_one = tuple(two[axis] - one[axis] for axis in range(3))
                edge_two = tuple(three[axis] - one[axis] for axis in range(3))
                normal = (
                    edge_one[1] * edge_two[2] - edge_one[2] * edge_two[1],
                    edge_one[2] * edge_two[0] - edge_one[0] * edge_two[2],
                    edge_one[0] * edge_two[1] - edge_one[1] * edge_two[0],
                )
                magnitude = math.sqrt(sum(component * component for component in normal))
                normals.append(tuple(component / magnitude for component in normal) if magnitude else (0.0, 1.0, 0.0))
            normal_values = " ".join(f"{coordinate:.7g}" for normal in normals for coordinate in normal)
            indices = " ".join(
                f"{vertex} {triangle_index}"
                for triangle_index, triangle in enumerate(mesh.triangles)
                for vertex in triangle
            )
            output.write(f'    <geometry id={quoteattr(geometry_id)} name={quoteattr(mesh.name)}><mesh>\n')
            output.write(f'      <source id={quoteattr(positions_id)}><float_array id={quoteattr(positions_id + "-array")} count="{3 * len(mesh.vertices)}">{coordinates}</float_array>')
            output.write(f'<technique_common><accessor source="#{positions_id}-array" count="{len(mesh.vertices)}" stride="3">')
            output.write('<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>')
            output.write('</accessor></technique_common></source>\n')
            output.write(f'      <source id={quoteattr(normals_id)}><float_array id={quoteattr(normals_id + "-array")} count="{3 * len(normals)}">{normal_values}</float_array>')
            output.write(f'<technique_common><accessor source="#{normals_id}-array" count="{len(normals)}" stride="3">')
            output.write('<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>')
            output.write('</accessor></technique_common></source>\n')
            output.write(f'      <vertices id={quoteattr(vertices_id)}><input semantic="POSITION" source="#{positions_id}"/></vertices>\n')
            output.write(f'      <triangles count="{len(mesh.triangles)}" material="{mesh.category}-symbol"><input semantic="VERTEX" source="#{vertices_id}" offset="0"/><input semantic="NORMAL" source="#{normals_id}" offset="1"/><p>{indices}</p></triangles>\n')
            output.write("    </mesh></geometry>\n")
        output.write("  </library_geometries>\n")
        output.write('  <library_visual_scenes><visual_scene id="SHIFT-geometry" name="SHIFT_geometry">\n')
        for group_name, prefix in (("CMS", "cms_"), ("IR1_ATLAS_LSS_PROXY", "lss_")):
            output.write(f'    <node id="{group_name}" name="{group_name}">\n')
            for index, mesh in enumerate(meshes):
                if not mesh.category.startswith(prefix):
                    continue
                geometry_id = f"geometry-{index}-{sanitize(mesh.name)}"
                node_id = f"node-{index}-{sanitize(mesh.name)}"
                output.write(f'      <node id={quoteattr(node_id)} name={quoteattr(mesh.name)}><instance_geometry url="#{geometry_id}"><bind_material><technique_common>')
                output.write(f'<instance_material symbol="{mesh.category}-symbol" target="#{mesh.category}-material"/>')
                output.write('</technique_common></bind_material></instance_geometry></node>\n')
            output.write("    </node>\n")
        output.write('  </visual_scene></library_visual_scenes><scene><instance_visual_scene url="#SHIFT-geometry"/></scene>\n')
        output.write("</COLLADA>\n")


def write_preview_collada(path, meshes):
    """Write a conservative Collada 1.4.1 file for macOS Preview.

    Preview is less forgiving than Quick Look when a mesh has no normal input.
    Use one flat geometry per populated category, triangle-only faces, and
    matching position/normal indices.  This avoids both separately indexed
    attributes and importer-side normal generation.
    """
    grouped = {category: {"positions": [], "normals": []} for category in COLORS}
    for mesh in meshes:
        positions, normals = display_triangle_data(mesh)
        grouped[mesh.category]["positions"].extend(positions)
        grouped[mesh.category]["normals"].extend(normals)
    populated = [category for category, data in grouped.items() if data["positions"]]
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        output.write('<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n')
        output.write('  <asset>\n')
        output.write('    <contributor><authoring_tool>SHIFT geometry visualizer</authoring_tool></contributor>\n')
        output.write('    <created>2026-09-03T00:00:00Z</created>\n')
        output.write('    <modified>2026-09-03T00:00:00Z</modified>\n')
        output.write('    <unit name="meter" meter="1"/>\n')
        output.write('    <up_axis>Y_UP</up_axis>\n')
        output.write('  </asset>\n')
        output.write('  <library_effects>\n')
        for category, (red, green, blue, _) in COLORS.items():
            output.write(f'    <effect id="{category}-effect"><profile_COMMON><technique sid="common"><lambert>\n')
            output.write(f'      <diffuse><color>{red:.6f} {green:.6f} {blue:.6f} 1</color></diffuse>\n')
            output.write('    </lambert></technique></profile_COMMON></effect>\n')
        output.write('  </library_effects>\n')
        output.write('  <library_materials>\n')
        for category in COLORS:
            output.write(f'    <material id="{category}-material" name="{sanitize(LABELS[category])}">')
            output.write(f'<instance_effect url="#{category}-effect"/></material>\n')
        output.write('  </library_materials>\n')
        output.write('  <library_geometries>\n')
        for category in populated:
            positions = grouped[category]["positions"]
            normals = grouped[category]["normals"]
            position_source_id = category + "-positions"
            normal_source_id = category + "-normals"
            output.write(f'    <geometry id="{category}-geometry" name="{sanitize(LABELS[category])}"><mesh>\n')
            output.write(f'      <source id="{position_source_id}">\n')
            output.write(f'        <float_array id="{position_source_id}-array" count="{3 * len(positions)}">\n')
            for offset in range(0, len(positions), 4):
                output.write('          ' + ' '.join(
                    f'{coordinate:.7g}' for point in positions[offset:offset + 4] for coordinate in point
                ) + '\n')
            output.write('        </float_array>\n')
            output.write(f'        <technique_common><accessor source="#{position_source_id}-array" count="{len(positions)}" stride="3">')
            output.write('<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>')
            output.write('</accessor></technique_common>\n')
            output.write('      </source>\n')
            output.write(f'      <source id="{normal_source_id}">\n')
            output.write(f'        <float_array id="{normal_source_id}-array" count="{3 * len(normals)}">\n')
            for offset in range(0, len(normals), 4):
                output.write('          ' + ' '.join(
                    f'{coordinate:.7g}' for normal in normals[offset:offset + 4] for coordinate in normal
                ) + '\n')
            output.write('        </float_array>\n')
            output.write(f'        <technique_common><accessor source="#{normal_source_id}-array" count="{len(normals)}" stride="3">')
            output.write('<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>')
            output.write('</accessor></technique_common>\n')
            output.write('      </source>\n')
            output.write(f'      <vertices id="{category}-vertices"><input semantic="POSITION" source="#{position_source_id}"/></vertices>\n')
            output.write(f'      <triangles material="{category}-material-symbol" count="{len(positions) // 3}">\n')
            output.write(f'        <input semantic="VERTEX" source="#{category}-vertices" offset="0"/>\n')
            output.write(f'        <input semantic="NORMAL" source="#{normal_source_id}" offset="1"/>\n')
            output.write('        <p>\n')
            for offset in range(0, len(positions), 18):
                output.write('          ' + ' '.join(
                    f'{index} {index}' for index in range(offset, min(offset + 18, len(positions)))
                ) + '\n')
            output.write('        </p>\n')
            output.write('      </triangles>\n')
            output.write('    </mesh></geometry>\n')
        output.write('  </library_geometries>\n')
        output.write('  <library_visual_scenes><visual_scene id="Scene" name="CMS_and_LSS_test_geometry">\n')
        for category in populated:
            output.write(f'    <node id="{category}-node" name="{sanitize(LABELS[category])}">\n')
            output.write(f'      <instance_geometry url="#{category}-geometry"><bind_material><technique_common>\n')
            output.write(f'        <instance_material symbol="{category}-material-symbol" target="#{category}-material"/>\n')
            output.write('      </technique_common></bind_material></instance_geometry>\n')
            output.write('    </node>\n')
        output.write('  </visual_scene></library_visual_scenes>\n')
        output.write('  <scene><instance_visual_scene url="#Scene"/></scene>\n')
        output.write('</COLLADA>\n')


def display_triangle_data(mesh):
    """Return unindexed, viewer-oriented triangles with flat normals."""
    positions = []
    normals = []
    for triangle in mesh.triangles:
        points = [(mesh.vertices[index][2], mesh.vertices[index][1], -mesh.vertices[index][0]) for index in triangle]
        one, two, three = points
        edge_one = tuple(two[axis] - one[axis] for axis in range(3))
        edge_two = tuple(three[axis] - one[axis] for axis in range(3))
        normal = (
            edge_one[1] * edge_two[2] - edge_one[2] * edge_two[1],
            edge_one[2] * edge_two[0] - edge_one[0] * edge_two[2],
            edge_one[0] * edge_two[1] - edge_one[1] * edge_two[0],
        )
        magnitude = math.sqrt(sum(component * component for component in normal))
        normal = tuple(component / magnitude for component in normal) if magnitude else (0.0, 1.0, 0.0)
        positions.extend(points)
        normals.extend([normal] * 3)
    return positions, normals


def write_glb(path, meshes):
    """Write a dependency-free glTF 2.0 binary for VS Code and web viewers."""
    document = {
        "asset": {"version": "2.0", "generator": "SHIFT geometry visualizer"},
        "scene": 0,
        "scenes": [{"name": "SHIFT geometry", "nodes": list(range(len(meshes)))}],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "buffers": [{"byteLength": 0}],
        "bufferViews": [],
        "accessors": [],
    }
    material_indices = {}
    for category, (red, green, blue, alpha) in COLORS.items():
        material_indices[category] = len(document["materials"])
        material = {
            "name": category,
            "pbrMetallicRoughness": {
                "baseColorFactor": [red, green, blue, alpha],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.72,
            },
            "doubleSided": True,
        }
        if alpha < 1.0:
            material["alphaMode"] = "BLEND"
        document["materials"].append(material)

    binary = bytearray()

    def add_vectors(vectors, include_bounds):
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        for vector in vectors:
            binary.extend(struct.pack("<3f", *vector))
        view = len(document["bufferViews"])
        document["bufferViews"].append(
            {"buffer": 0, "byteOffset": offset, "byteLength": len(binary) - offset, "target": 34962}
        )
        accessor = {"bufferView": view, "componentType": 5126, "count": len(vectors), "type": "VEC3"}
        if include_bounds:
            accessor["min"] = [min(vector[axis] for vector in vectors) for axis in range(3)]
            accessor["max"] = [max(vector[axis] for vector in vectors) for axis in range(3)]
        document["accessors"].append(accessor)
        return len(document["accessors"]) - 1

    for mesh in meshes:
        positions, normals = display_triangle_data(mesh)
        position_accessor = add_vectors(positions, True)
        normal_accessor = add_vectors(normals, False)
        mesh_index = len(document["meshes"])
        document["meshes"].append(
            {
                "name": mesh.name,
                "primitives": [
                    {
                        "attributes": {"POSITION": position_accessor, "NORMAL": normal_accessor},
                        "material": material_indices[mesh.category],
                        "mode": 4,
                    }
                ],
            }
        )
        document["nodes"].append({"name": mesh.name, "mesh": mesh_index})

    document["buffers"][0]["byteLength"] = len(binary)
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    total_length = 12 + 8 + len(json_chunk) + 8 + len(binary)
    with path.open("wb") as output:
        output.write(struct.pack("<4sII", b"glTF", 2, total_length))
        output.write(struct.pack("<I4s", len(json_chunk), b"JSON"))
        output.write(json_chunk)
        output.write(struct.pack("<I4s", len(binary), b"BIN\0"))
        output.write(binary)


def write_usdz(path, meshes):
    """Write an uncompressed, 64-byte-aligned USDA package for macOS Preview."""
    lines = [
        "#usda 1.0",
        "(",
        '    defaultPrim = "SHIFTGeometry"',
        "    metersPerUnit = 1",
        '    upAxis = "Y"',
        ")",
        'def Xform "SHIFTGeometry" {',
        '    def Scope "Materials" {',
    ]
    for category, (red, green, blue, alpha) in COLORS.items():
        material = sanitize(category)
        lines.extend(
            [
                f'        def Material "{material}" {{',
                f'            token outputs:surface.connect = </SHIFTGeometry/Materials/{material}/PreviewSurface.outputs:surface>',
                '            def Shader "PreviewSurface" {',
                '                uniform token info:id = "UsdPreviewSurface"',
                f'                color3f inputs:diffuseColor = ({red:.6g}, {green:.6g}, {blue:.6g})',
                f'                float inputs:opacity = {alpha:.6g}',
                '                float inputs:roughness = 0.72',
                '                token outputs:surface',
                "            }",
                "        }",
            ]
        )
    lines.extend(["    }", '    def Xform "Geometry" {'])
    for index, mesh in enumerate(meshes):
        positions, normals = display_triangle_data(mesh)
        name = f"mesh_{index}_{sanitize(mesh.name)}"
        points = ", ".join(f"({x:.7g}, {y:.7g}, {z:.7g})" for x, y, z in positions)
        normal_values = ", ".join(f"({x:.7g}, {y:.7g}, {z:.7g})" for x, y, z in normals)
        indices = ", ".join(str(value) for value in range(len(positions)))
        counts = ", ".join("3" for _ in mesh.triangles)
        lines.extend(
            [
                f'        def Mesh "{name}" {{',
                f'            rel material:binding = </SHIFTGeometry/Materials/{sanitize(mesh.category)}>',
                '            uniform bool doubleSided = true',
                f'            int[] faceVertexCounts = [{counts}]',
                f'            int[] faceVertexIndices = [{indices}]',
                f'            normal3f[] normals = [{normal_values}] (interpolation = "vertex")',
                f'            point3f[] points = [{points}]',
                '            uniform token subdivisionScheme = "none"',
                "        }",
            ]
        )
    lines.extend(["    }", "}", ""])
    payload = "\n".join(lines).encode("utf-8")
    member_name = "cms_lss_geometry.usda"
    info = zipfile.ZipInfo(member_name)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    # The file payload in USDZ must start on a 64-byte boundary.  ZipInfo.extra
    # contains a valid private extra-field header followed by zero padding.
    base_offset = 30 + len(member_name.encode("utf-8"))
    extra_length = (-base_offset) % 64
    if extra_length and extra_length < 4:
        extra_length += 64
    if extra_length:
        info.extra = struct.pack("<HH", 0xFFFF, extra_length - 4) + bytes(extra_length - 4)
    with zipfile.ZipFile(path, "w", allowZip64=False) as archive:
        archive.writestr(info, payload)


def render(path, meshes):
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from matplotlib.patches import Patch
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    figure = plt.figure(figsize=(18, 22), facecolor="white")
    layout = figure.add_gridspec(
        3, 2, height_ratios=(1.0, 1.0, 1.35), left=0.055, right=0.98,
        bottom=0.055, top=0.925, wspace=0.14, hspace=0.24
    )
    projections = (
        (layout[0, :], "Full geometry: side view", (2, 1), "z [m]", "y [m]", None),
        (layout[1, 0], "CMS: view along the beam line", (0, 1), "x [m]", "y [m]", ((-18, 18), (-18, 18))),
        (layout[1, 1], "CMS: side view", (2, 1), "z [m]", "y [m]", ((-18, 18), (-18, 18))),
    )
    for slot, title, axes, xlabel, ylabel, limits in projections:
        axis = figure.add_subplot(slot)
        axis.set_facecolor("white")
        for category in COLORS:
            polygons = []
            for mesh in meshes:
                if mesh.category != category:
                    continue
                polygons.extend(
                    [[(mesh.vertices[index][axes[0]], mesh.vertices[index][axes[1]]) for index in triangle]
                     for triangle in mesh.triangles]
                )
            red, green, blue, alpha = COLORS[category]
            axis.add_collection(PolyCollection(
                polygons, facecolors=[(red, green, blue, alpha)],
                edgecolors=[(red, green, blue, min(1.0, alpha + 0.18))],
                linewidths=0.15, rasterized=True
            ))
        axis.autoscale_view()
        if limits:
            axis.set_xlim(*limits[0])
            axis.set_ylim(*limits[1])
        axis.set_aspect("equal", adjustable="box")
        axis.set_title(title, color="#172033", fontsize=15)
        axis.set_xlabel(xlabel, color="#263244")
        axis.set_ylabel(ylabel, color="#263244")
        axis.tick_params(colors="#374151")
        axis.grid(color="#aeb8c6", alpha=0.38, linewidth=0.5)
        for spine in axis.spines.values():
            spine.set_color("#9aa5b4")

    axis3d = figure.add_subplot(layout[2, :], projection="3d")
    axis3d.set_facecolor("white")
    for category in COLORS:
        faces = []
        for mesh in meshes:
            if mesh.category == category:
                faces.extend(
                    [[(mesh.vertices[index][2], -mesh.vertices[index][0], mesh.vertices[index][1])
                      for index in triangle]
                    for triangle in mesh.triangles]
                )
        red, green, blue, alpha = COLORS[category]
        axis3d.add_collection3d(Poly3DCollection(faces, facecolors=[(red, green, blue, alpha)], edgecolors="none", rasterized=True))
    all_vertices = [vertex for mesh in meshes for vertex in mesh.vertices]
    bounds = [(min(vertex[axis] for vertex in all_vertices), max(vertex[axis] for vertex in all_vertices)) for axis in range(3)]
    display_vertices = [(vertex[2], -vertex[0], vertex[1]) for vertex in all_vertices]
    display_bounds = [
        (min(vertex[axis] for vertex in display_vertices), max(vertex[axis] for vertex in display_vertices))
        for axis in range(3)
    ]
    for setter, limits in zip((axis3d.set_xlim, axis3d.set_ylim, axis3d.set_zlim), display_bounds):
        setter(*limits)
    # Preserve coordinates and tick values while compressing the long z span
    # on screen; otherwise the 270 m model becomes an unreadably thin needle.
    axis3d.set_box_aspect((3, 1, 1))
    axis3d.view_init(elev=22, azim=-56)
    axis3d.set_title("Angled 3D view (the long z direction is shortened on screen)", color="#172033", fontsize=15)
    axis3d.set_xlabel("z [m]", color="#263244")
    axis3d.set_ylabel("−x [m]", color="#263244")
    axis3d.set_zlabel("y [m]", color="#263244")
    axis3d.tick_params(colors="#374151")
    for pane in (axis3d.xaxis.pane, axis3d.yaxis.pane, axis3d.zaxis.pane):
        pane.set_facecolor((0.96, 0.97, 0.98, 1.0))

    figure.suptitle("CMS and the current LSS test geometry", color="#111827", fontsize=22, fontweight="bold", y=0.974)
    figure.text(
        0.5, 0.948,
        "This is the temporary ATLAS-side test model. It is not yet the final CMS-side LSS geometry.",
        ha="center", color="#9a3412", fontsize=13
    )
    handles = [Patch(facecolor=COLORS[name], edgecolor=COLORS[name][:3], label=LABELS[name]) for name in COLORS]
    figure.legend(handles=handles, loc="lower center", ncol=4, frameon=False, labelcolor="#263244", fontsize=10)
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)
    return bounds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="selected Wavefront OBJ from extract_lss_visualization_meshes")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    meshes = load_obj(args.input)
    if not meshes:
        raise RuntimeError("input selection contains no meshes")

    png = args.output_dir / "cms_lss_geometry_overview.png"
    dae = args.output_dir / "cms_lss_geometry.dae"
    glb = args.output_dir / "cms_lss_geometry.glb"
    usdz = args.output_dir / "cms_lss_geometry.usdz"
    obj = args.output_dir / "cms_lss_geometry_overview.obj"
    if args.input.resolve() != obj.resolve():
        shutil.copyfile(args.input, obj)
    bounds = render(png, meshes)
    write_preview_collada(dae, meshes)
    write_glb(glb, meshes)
    write_usdz(usdz, meshes)
    manifest = {
        "input": obj.name,
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "coordinate_unit": "meter",
        "status": "temporary ATLAS-side test model; not the final CMS-side LSS geometry",
        "representation": "simplified overview; small parts and repeated low-level parts are omitted",
        "omissions": [
            "EARTHBOH rock is present in the simulation GDML but omitted because its Boolean solids do not have reliable overview bounds"
        ],
        "mesh_count": len(meshes),
        "vertex_count": sum(len(mesh.vertices) for mesh in meshes),
        "triangle_count": sum(len(mesh.triangles) for mesh in meshes),
        "bounds_m": {axis: list(limits) for axis, limits in zip("xyz", bounds)},
        "category_mesh_counts": {category: sum(mesh.category == category for mesh in meshes) for category in COLORS},
        "outputs": {
            "overview_png": png.name,
            "gltf_binary": glb.name,
            "apple_usdz": usdz.name,
            "collada_legacy": dae.name,
            "wavefront_obj": obj.name,
        },
    }
    (args.output_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
