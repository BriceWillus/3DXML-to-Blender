bl_info = {
    "name": "Import 3DXML",
    "author": "Adapted for Blender 4.x and 5.x",
    "version": (0, 4, 0),
    "blender": (4, 0, 0),
    "location": "File > Import > Import 3DXML",
    "description": "Import tessellated Dassault/SolidWorks 3DXML files",
    "category": "Import-Export",
}

import os
import math
import zipfile
import xml.etree.ElementTree as ET

import bpy
import bmesh

from mathutils import Matrix
from bpy.types import Operator
from bpy.props import StringProperty, FloatProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper


NS = {
    "x": "http://www.3ds.com/xsd/3DXML",
}


def local_name(tag):
    return tag.split("}", 1)[-1]


def parse_float_list(text):
    if not text:
        return []

    text = text.replace(",", " ")
    return [float(v) for v in text.split()]


def parse_positions(text):
    values = parse_float_list(text)

    return [
        (values[i], values[i + 1], values[i + 2])
        for i in range(0, len(values), 3)
    ]


def parse_index_groups(text):
    if not text:
        return []

    groups = []

    for group in text.split(","):
        indices = [int(v) for v in group.split()]

        if len(indices) >= 3:
            groups.append(indices)

    return groups


def triangle_strip_to_faces(indices):
    faces = []

    for i in range(len(indices) - 2):
        if i % 2 == 0:
            tri = (indices[i], indices[i + 1], indices[i + 2])
        else:
            tri = (indices[i + 1], indices[i], indices[i + 2])

        if len(set(tri)) == 3:
            faces.append(tri)

    return faces


def matrix_from_3dxml(text, scale):
    if not text:
        return Matrix.Identity(4)

    values = parse_float_list(text)

    if len(values) != 12:
        return Matrix.Identity(4)

    # 3DXML / SolidWorks stores the 3x3 rotation matrix by columns, then translation.
    r00, r10, r20 = values[0], values[1], values[2]
    r01, r11, r21 = values[3], values[4], values[5]
    r02, r12, r22 = values[6], values[7], values[8]

    tx, ty, tz = values[9], values[10], values[11]

    return Matrix((
        (r00, r01, r02, tx * scale),
        (r10, r11, r12, ty * scale),
        (r20, r21, r22, tz * scale),
        (0.0, 0.0, 0.0, 1.0),
    ))


def get_text_child(element, name):
    child = element.find(f"x:{name}", NS)
    return child.text if child is not None else None


def make_material(name, color):
    mat = bpy.data.materials.get(name)

    if mat:
        return mat

    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = color

    bsdf = mat.node_tree.nodes.get("Principled BSDF")

    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Alpha"].default_value = color[3]

    if color[3] < 1.0:
        mat.blend_method = "BLEND"
        mat.use_screen_refraction = True

    return mat


def extract_color_from_surface_attributes(face_element):
    color = face_element.find(".//x:Color", NS)

    if color is None:
        return None

    r = float(color.attrib.get("red", 0.8))
    g = float(color.attrib.get("green", 0.8))
    b = float(color.attrib.get("blue", 0.8))
    a = float(color.attrib.get("alpha", 1.0))

    return (r, g, b, a)


def parse_3drep_mesh(zip_file, rep_filename, scale):
    xml_data = zip_file.read(rep_filename)
    root = ET.fromstring(xml_data)

    all_vertices = []
    all_faces = []
    face_material_colors = []

    polygonal_reps = [
        elem for elem in root.iter()
        if elem.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}type") == "PolygonalRepType"
    ]

    for poly_rep in polygonal_reps:
        vertex_buffer = poly_rep.find("x:VertexBuffer", NS)

        if vertex_buffer is None:
            continue

        positions_node = vertex_buffer.find("x:Positions", NS)

        if positions_node is None or not positions_node.text:
            continue

        local_positions = parse_positions(positions_node.text)
        vertex_offset = len(all_vertices)

        for v in local_positions:
            all_vertices.append((
                v[0] * scale,
                v[1] * scale,
                v[2] * scale,
            ))

        faces_node = poly_rep.find("x:Faces", NS)

        if faces_node is None:
            continue

        for face_node in faces_node.findall("x:Face", NS):
            color = extract_color_from_surface_attributes(face_node)

            if color is None:
                color = (0.8, 0.8, 0.8, 1.0)

            strips = parse_index_groups(face_node.attrib.get("strips", ""))

            for strip in strips:
                triangles = triangle_strip_to_faces(strip)

                for tri in triangles:
                    all_faces.append(tuple(vertex_offset + i for i in tri))
                    face_material_colors.append(color)

            triangles = parse_index_groups(face_node.attrib.get("triangles", ""))

            for tri in triangles:
                if len(tri) == 3:
                    all_faces.append(tuple(vertex_offset + i for i in tri))
                    face_material_colors.append(color)

    return all_vertices, all_faces, face_material_colors


def apply_merge_vertices(mesh):
    bm = bmesh.new()
    bm.from_mesh(mesh)

    bmesh.ops.remove_doubles(
        bm,
        verts=bm.verts,
        dist=0.0
    )

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def apply_auto_smooth_by_angle(obj, angle_degrees=30.0):
    bpy.ops.object.select_all(action="DESELECT")

    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    try:
        bpy.ops.object.shade_auto_smooth(
            angle=math.radians(angle_degrees)
        )
    except Exception:
        for polygon in obj.data.polygons:
            polygon.use_smooth = True


def create_mesh_object(
    name,
    vertices,
    faces,
    colors,
    matrix,
    parent_empty=None,
    merge_vertices=False,
    auto_smooth=False,
    smooth_angle=30.0
):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()

    if merge_vertices:
        apply_merge_vertices(mesh)

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.matrix_world = matrix

    if parent_empty:
        obj.parent = parent_empty

    material_slots = {}

    for color in colors:
        key = tuple(round(c, 4) for c in color)

        if key not in material_slots:
            mat_name = f"3DXML_Mat_{key[0]}_{key[1]}_{key[2]}_{key[3]}"
            mat = make_material(mat_name, color)
            mesh.materials.append(mat)
            material_slots[key] = len(mesh.materials) - 1

    for poly, color in zip(mesh.polygons, colors):
        key = tuple(round(c, 4) for c in color)
        poly.material_index = material_slots.get(key, 0)

    if auto_smooth:
        apply_auto_smooth_by_angle(obj, smooth_angle)

    return obj


def import_3dxml(
    context,
    filepath,
    scale=0.001,
    merge_vertices=True,
    auto_smooth=True
):
    if not zipfile.is_zipfile(filepath):
        raise Exception("This .3DXML file is not a valid ZIP archive.")

    file_name = os.path.splitext(os.path.basename(filepath))[0]

    parent_empty = bpy.data.objects.new(file_name, None)
    parent_empty.empty_display_type = "PLAIN_AXES"
    parent_empty.empty_display_size = 1.0
    context.collection.objects.link(parent_empty)

    with zipfile.ZipFile(filepath, "r") as z:
        names = z.namelist()

        if "Manifest.xml" not in names:
            raise Exception("Manifest.xml was not found in the 3DXML file.")

        manifest = ET.fromstring(z.read("Manifest.xml"))
        root_file_node = manifest.find("Root")

        if root_file_node is None:
            raise Exception("Root entry was not found in Manifest.xml.")

        root_filename = root_file_node.text

        if root_filename not in names:
            raise Exception(f"{root_filename} was not found in the 3DXML file.")

        product_tree = ET.fromstring(z.read(root_filename))
        product_structure = product_tree.find("x:ProductStructure", NS)

        if product_structure is None:
            raise Exception("ProductStructure was not found.")

        root_ref_id = product_structure.attrib.get("root")

        reference_names = {}
        reference_reps = {}
        instance3d_children = {}
        instance_rep_links = {}

        for elem in product_structure:
            tag = local_name(elem.tag)
            elem_id = elem.attrib.get("id")
            elem_name = elem.attrib.get("name", elem_id or "Unnamed")

            if tag == "Reference3D":
                reference_names[elem_id] = elem_name

            elif tag == "ReferenceRep":
                associated = elem.attrib.get("associatedFile", "")

                if associated.startswith("urn:3DXML:"):
                    associated = associated.replace("urn:3DXML:", "")

                reference_reps[elem_id] = associated

            elif tag == "Instance3D":
                parent = get_text_child(elem, "IsAggregatedBy")
                child = get_text_child(elem, "IsInstanceOf")
                matrix_text = get_text_child(elem, "RelativeMatrix")

                matrix = matrix_from_3dxml(matrix_text, scale)

                instance3d_children.setdefault(parent, []).append({
                    "id": elem_id,
                    "name": elem_name,
                    "child_ref": child,
                    "matrix": matrix,
                })

            elif tag == "InstanceRep":
                parent = get_text_child(elem, "IsAggregatedBy")
                rep = get_text_child(elem, "IsInstanceOf")

                if parent and rep:
                    instance_rep_links.setdefault(parent, []).append(rep)

        imported_count = 0
        mesh_cache = {}

        def import_reference(ref_id, parent_matrix):
            nonlocal imported_count

            ref_name = reference_names.get(ref_id, f"Reference_{ref_id}")

            for rep_id in instance_rep_links.get(ref_id, []):
                rep_filename = reference_reps.get(rep_id)

                if not rep_filename or rep_filename not in names:
                    continue

                object_name = f"{ref_name}_{rep_id}"

                if rep_filename in mesh_cache:
                    vertices, faces, colors = mesh_cache[rep_filename]
                else:
                    vertices, faces, colors = parse_3drep_mesh(
                        z,
                        rep_filename,
                        scale
                    )
                    mesh_cache[rep_filename] = (vertices, faces, colors)

                if vertices and faces:
                    create_mesh_object(
                        object_name,
                        vertices,
                        faces,
                        colors,
                        parent_matrix,
                        parent_empty=parent_empty,
                        merge_vertices=merge_vertices,
                        auto_smooth=auto_smooth,
                        smooth_angle=30.0
                    )
                    imported_count += 1

            for child in instance3d_children.get(ref_id, []):
                child_matrix = parent_matrix @ child["matrix"]
                import_reference(child["child_ref"], child_matrix)

        import_reference(root_ref_id, Matrix.Identity(4))

        if imported_count == 0:
            bpy.data.objects.remove(parent_empty, do_unlink=True)
            raise Exception("No tessellated geometry was imported.")

        # Convert from SolidWorks / 3DXML Y-Up orientation to Blender Z-Up orientation.
        # The Empty is rotated after all mesh objects have been parented,
        # so every child object follows the global correction.
        parent_empty.rotation_euler[0] = math.radians(90.0)

        bpy.ops.object.select_all(action="DESELECT")
        parent_empty.select_set(True)
        context.view_layer.objects.active = parent_empty

        return {"FINISHED"}


class Import3DXML(Operator, ImportHelper):
    bl_idname = "import_scene.3dxml"
    bl_label = "Import 3DXML"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".3DXML"

    filter_glob: StringProperty(
        default="*.3DXML;*.3dxml",
        options={"HIDDEN"},
    )

    filepath: StringProperty(
        name="File Path",
        subtype="FILE_PATH",
    )

    scale: FloatProperty(
        name="Scale",
        description="Scale factor. 0.001 converts millimeters to meters.",
        default=0.001,
        min=0.000001,
        max=100.0,
    )

    merge_vertices: BoolProperty(
        name="Merge Vertices",
        description="Merge perfectly overlapping vertices",
        default=True,
    )

    auto_smooth: BoolProperty(
        name="Auto Smooth by Angle",
        description="Apply Smooth by Angle at 30 degrees",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scale")
        layout.prop(self, "merge_vertices")
        layout.prop(self, "auto_smooth")

    def execute(self, context):
        try:
            return import_3dxml(
                context,
                self.filepath,
                self.scale,
                self.merge_vertices,
                self.auto_smooth
            )
        except Exception as e:
            self.report({"ERROR"}, str(e))
            print("3DXML import error:", e)
            return {"CANCELLED"}


def menu_func_import(self, context):
    self.layout.operator(
        Import3DXML.bl_idname,
        text="Import 3DXML (.3DXML)"
    )


classes = (
    Import3DXML,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()