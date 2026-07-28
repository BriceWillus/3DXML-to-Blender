bl_info = {
    "name": "Import 3DXML",
    "author": "Neil Hilgenga",
    "version": (0, 7, 1),
    "blender": (4, 0, 0),
    "location": "File > Import > Import 3DXML",
    "description": "Import tessellated Dassault/SolidWorks 3DXML files",
    "category": "Import-Export",
}

import os
import math
import json
import zipfile
import xml.etree.ElementTree as ET

import bpy
import bmesh

from mathutils import Matrix, Vector
from bpy.types import Operator
from bpy.props import StringProperty, FloatProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper

try:
    from bpy_extras.io_utils import poll_file_object_drop
except ImportError:
    poll_file_object_drop = None

FileHandler = getattr(bpy.types, "FileHandler", None)


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


def apply_merge_vertices(mesh, distance=0.0):
    bm = bmesh.new()
    bm.from_mesh(mesh)

    bmesh.ops.remove_doubles(
        bm,
        verts=bm.verts,
        dist=distance
    )

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def apply_auto_smooth_by_angle(obj, angle_degrees=30.0, apply_modifier=False):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    modifiers_before = set(obj.modifiers)

    try:
        bpy.ops.object.shade_auto_smooth(
            angle=math.radians(angle_degrees)
        )

        if apply_modifier:
            new_modifiers = [
                modifier for modifier in obj.modifiers
                if modifier not in modifiers_before
            ]

            for modifier in new_modifiers:
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.modifier_apply(modifier=modifier.name)

    except Exception:
        # Fallback for Blender versions where the operator is unavailable.
        for polygon in obj.data.polygons:
            polygon.use_smooth = True


def get_mesh_objects(parent_empty):
    return [
        obj for obj in parent_empty.children_recursive
        if obj.type == "MESH"
    ]


def material_signature(obj):
    return tuple(slot.material for slot in obj.material_slots)


def merge_objects_by_materials(context, objects):
    groups = {}

    for obj in objects:
        groups.setdefault(material_signature(obj), []).append(obj)

    merged_objects = []

    for group in groups.values():
        if len(group) == 1:
            merged_objects.append(group[0])
            continue

        bpy.ops.object.select_all(action="DESELECT")

        active = group[0]
        for obj in group:
            obj.select_set(True)

        context.view_layer.objects.active = active
        bpy.ops.object.join()
        merged_objects.append(active)

    return merged_objects


def center_origins_to_geometry(context, objects):
    for obj in objects:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")


def align_objects_to_world(context, objects):
    if not objects:
        return

    context.view_layer.update()

    world_points = []
    for obj in objects:
        world_points.extend(obj.matrix_world @ vertex.co for vertex in obj.data.vertices)

    if not world_points:
        return

    min_x = min(point.x for point in world_points)
    max_x = max(point.x for point in world_points)
    min_y = min(point.y for point in world_points)
    max_y = max(point.y for point in world_points)
    min_z = min(point.z for point in world_points)

    offset = Vector((
        -(min_x + max_x) * 0.5,
        -(min_y + max_y) * 0.5,
        -min_z,
    ))
    translation = Matrix.Translation(offset)

    # Move every mesh by the same world-space offset. Their relative layout is
    # preserved and the global parent Empty remains untouched.
    for obj in objects:
        obj.matrix_world = translation @ obj.matrix_world


def create_mesh_object(
    name,
    vertices,
    faces,
    colors,
    matrix,
    parent_empty=None,
    merge_vertices=False,
    merge_distance=0.0,
    auto_smooth=False,
    smooth_angle=30.0,
    apply_smooth_modifier=False
):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()

    if merge_vertices:
        apply_merge_vertices(mesh, merge_distance)

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
        apply_auto_smooth_by_angle(obj, smooth_angle, apply_smooth_modifier)

    return obj


def import_3dxml(
    context,
    filepath,
    scale=0.001,
    merge_vertices=True,
    merge_distance=0.0,
    auto_smooth=True,
    smooth_angle=30.0,
    apply_smooth_modifier=False,
    align_to_world=False,
    merge_by_materials=False,
    center_origins=False,
    parent_name=""
):
    if not zipfile.is_zipfile(filepath):
        raise Exception("This .3DXML file is not a valid ZIP archive.")

    file_name = os.path.splitext(os.path.basename(filepath))[0]
    empty_name = parent_name.strip() or file_name

    parent_empty = bpy.data.objects.new(empty_name, None)
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
                        merge_distance=merge_distance,
                        auto_smooth=auto_smooth,
                        smooth_angle=smooth_angle,
                        apply_smooth_modifier=apply_smooth_modifier
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
        context.view_layer.update()

        imported_objects = get_mesh_objects(parent_empty)

        if merge_by_materials:
            imported_objects = merge_objects_by_materials(context, imported_objects)

        if center_origins:
            center_origins_to_geometry(context, imported_objects)

        if align_to_world:
            align_objects_to_world(context, imported_objects)

        bpy.ops.object.select_all(action="DESELECT")
        parent_empty.select_set(True)
        context.view_layer.objects.active = parent_empty

        return {"FINISHED"}


SETTINGS_DIRECTORY_NAME = "io_import_3dxml"
SETTINGS_FILE_NAME = "import_settings.json"
SETTINGS_PROPERTIES = (
    "scale",
    "merge_vertices",
    "merge_distance",
    "auto_smooth",
    "smooth_angle",
    "apply_smooth_modifier",
    "align_to_world",
    "merge_by_materials",
    "center_origins",
)


def get_settings_filepath():
    config_directory = bpy.utils.user_resource(
        "CONFIG",
        path=SETTINGS_DIRECTORY_NAME,
        create=True,
    )

    if not config_directory:
        return None

    return os.path.join(config_directory, SETTINGS_FILE_NAME)


def load_import_settings(operator):
    settings_filepath = get_settings_filepath()

    if not settings_filepath or not os.path.isfile(settings_filepath):
        return

    try:
        with open(settings_filepath, "r", encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except (OSError, ValueError, TypeError) as error:
        print("3DXML settings load error:", error)
        return

    if not settings.get("remember_settings", False):
        return

    operator.remember_settings = True

    for property_name in SETTINGS_PROPERTIES:
        if property_name in settings:
            try:
                setattr(operator, property_name, settings[property_name])
            except (TypeError, ValueError):
                pass


def save_import_settings(operator):
    settings_filepath = get_settings_filepath()

    if not settings_filepath:
        return

    if not operator.remember_settings:
        try:
            if os.path.isfile(settings_filepath):
                os.remove(settings_filepath)
        except OSError as error:
            print("3DXML settings removal error:", error)
        return

    settings = {
        "remember_settings": True,
    }

    for property_name in SETTINGS_PROPERTIES:
        settings[property_name] = getattr(operator, property_name)

    temporary_filepath = settings_filepath + ".tmp"

    try:
        with open(temporary_filepath, "w", encoding="utf-8") as settings_file:
            json.dump(settings, settings_file, indent=2)

        os.replace(temporary_filepath, settings_filepath)
    except OSError as error:
        print("3DXML settings save error:", error)

        try:
            if os.path.isfile(temporary_filepath):
                os.remove(temporary_filepath)
        except OSError:
            pass


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

    # This is enabled only by the File > Import menu entry. FileHandler keeps
    # the default False value so a dropped file opens the settings dialog.
    use_file_browser: BoolProperty(
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
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

    merge_distance: FloatProperty(
        name="Merge Distance",
        description="Maximum distance between vertices to merge",
        default=0.0,
        min=0.0,
        soft_max=0.01,
        precision=6,
        subtype="DISTANCE",
    )

    auto_smooth: BoolProperty(
        name="Auto Smooth by Angle",
        description="Apply Smooth by Angle at 30 degrees",
        default=True,
    )

    smooth_angle: FloatProperty(
        name="Smooth Angle",
        description="Maximum angle between faces that will be smoothed",
        default=math.radians(30.0),
        min=0.0,
        max=math.radians(180.0),
        subtype="ANGLE",
    )

    apply_smooth_modifier: BoolProperty(
        name="Apply Modifier",
        description="Apply the Smooth by Angle modifier to each imported mesh",
        default=False,
    )

    align_to_world: BoolProperty(
        name="Align to World",
        description="Center the complete imported model on X and Y and place its lowest vertex at Z = 0 without moving the global parent Empty",
        default=False,
    )

    merge_by_materials: BoolProperty(
        name="Merge Objects by Materials",
        description="Join objects that use exactly the same material slots",
        default=False,
    )

    center_origins: BoolProperty(
        name="Center Origins to Geometry",
        description="Move each imported object's origin to the center of its geometry",
        default=False,
    )

    parent_name: StringProperty(
        name="Parent Empty Name",
        description="Name of the global parent Empty. Leave empty to use the 3DXML file name",
        default="",
    )

    remember_settings: BoolProperty(
        name="Remember Import Settings",
        description="Save these import settings and restore them for future 3DXML imports",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scale")
        layout.prop(self, "parent_name")

        layout.prop(self, "merge_vertices")
        if self.merge_vertices:
            layout.prop(self, "merge_distance")

        layout.prop(self, "auto_smooth")
        if self.auto_smooth:
            layout.prop(self, "smooth_angle")
            layout.prop(self, "apply_smooth_modifier")

        layout.separator()
        layout.prop(self, "align_to_world")
        layout.prop(self, "merge_by_materials")
        layout.prop(self, "center_origins")

        layout.separator()
        layout.prop(self, "remember_settings", icon="FILE_TICK")

        if self.remember_settings:
            info_row = layout.row()
            info_row.enabled = False
            info_row.label(text="Settings are saved after a successful import")

    def invoke(self, context, event):
        # The custom parent name is intentionally per-import. Never restore it
        # from saved settings or a previous invocation; an empty value makes
        # import_3dxml() fall back to the current 3DXML file name.
        self.parent_name = ""
        load_import_settings(self)

        if self.use_file_browser:
            # Always clear a path possibly retained by Blender's last-operator
            # properties, then open the regular file browser.
            self.filepath = ""
            return ImportHelper.invoke(self, context, event)

        # FileHandler supplies filepath for drag and drop. Only the settings
        # dialog is needed in that workflow.
        if not self.filepath:
            self.report({"ERROR"}, "No 3DXML file was provided.")
            return {"CANCELLED"}

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            result = import_3dxml(
                context,
                self.filepath,
                self.scale,
                self.merge_vertices,
                self.merge_distance,
                self.auto_smooth,
                math.degrees(self.smooth_angle),
                self.apply_smooth_modifier,
                self.align_to_world,
                self.merge_by_materials,
                self.center_origins,
                self.parent_name
            )

            if result == {"FINISHED"}:
                save_import_settings(self)

            return result
        except Exception as e:
            self.report({"ERROR"}, str(e))
            print("3DXML import error:", e)
            return {"CANCELLED"}


def menu_func_import(self, context):
    operator = self.layout.operator(
        Import3DXML.bl_idname,
        text="Import 3DXML (.3DXML)"
    )
    operator.use_file_browser = True


if FileHandler is not None and poll_file_object_drop is not None:
    class IO_FH_3DXML(FileHandler):
        bl_idname = "IO_FH_3dxml"
        bl_label = "3DXML"
        bl_import_operator = Import3DXML.bl_idname
        bl_file_extensions = ".3DXML;.3dxml"

        @classmethod
        def poll_drop(cls, context):
            return poll_file_object_drop(context)
else:
    IO_FH_3DXML = None


classes = (
    Import3DXML,
)

if IO_FH_3DXML is not None:
    classes += (IO_FH_3DXML,)


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
