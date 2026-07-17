bl_info = {
    "name": "SBOL MMDL Importer",
    "author": "tofuman0 / OpenAI",
    "version": (0, 3, 1),
    "blender": (3, 6, 0),
    "location": "File > Import > SBOL MMDL (.mmdl)",
    "description": "Import MMDL models used by SB Online",
    "category": "Import-Export",
}

import os
import struct
from dataclasses import dataclass, field

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper


def _read_u(f, size=4):
    data = f.read(size)
    if len(data) != size:
        raise EOFError("Unexpected end of MMDL file")
    return int.from_bytes(data, "little", signed=False)


def _read_f(f):
    data = f.read(4)
    if len(data) != 4:
        raise EOFError("Unexpected end of MMDL file")
    return struct.unpack("<f", data)[0]


def _read_s(f, size=32):
    data = f.read(size)
    return data.split(b"\0", 1)[0].decode("ascii", errors="replace")


@dataclass
class MMDLMaterial:
    unknown1: int
    vertex_offset: int
    vertex_count: int
    face_offset: int
    face_count: int
    ka: tuple
    opacity: float
    kd: tuple
    ks: tuple
    ni: float
    unknown_floats: tuple
    ns: float
    material_name: str
    unknown7: float
    texture_name: str
    ident: int


@dataclass
class MMDLObject:
    name: str
    ident: int
    unknown_values: tuple
    materials: list = field(default_factory=list)


@dataclass
class MMDLData:
    path: str
    vertex_entry_size: int
    vertices: list
    faces: list
    objects: list
    header: dict


def read_mmdl(path):
    with open(path, "rb") as f:
        if f.read(4) != b"LDMM":
            raise ValueError("Not an MMDL file (missing LDMM signature)")

        object_table_size = _read_u(f)
        vertex_table_size = _read_u(f)
        face_table_size = _read_u(f)
        unknown1 = _read_u(f)
        vertex_entry_size = _read_u(f)
        vertex_count = _read_u(f)
        face_entry_size = _read_u(f)
        face_count = _read_u(f)

        if vertex_entry_size not in (24, 36):
            raise ValueError(f"Unsupported MMDL vertex size: {vertex_entry_size}")
        if face_entry_size not in (1, 2, 4):
            raise ValueError(f"Unsupported MMDL index size: {face_entry_size}")

        # Structure 0
        base_floats = tuple(_read_f(f) for _ in range(7))
        base_ints = tuple(_read_u(f) for _ in range(4))
        object1_count = _read_u(f)
        base_id = _read_u(f)

        # Structure 1
        object1_records = []
        for _ in range(object1_count):
            vals = tuple(_read_f(f) for _ in range(8))
            child_count = _read_u(f)
            ident = _read_u(f)
            object1_records.append((vals, child_count, ident))

        objects = []
        for object1_index, (object1_vals, child_count, object1_id) in enumerate(object1_records):
            children = []
            for _ in range(child_count):
                first = _read_f(f)
                name = _read_s(f, 32)
                # The original parser reads fields labelled 2..22, 24 and 25.
                remaining = tuple(_read_f(f) for _ in range(23))
                material_count = _read_u(f)
                ident = _read_u(f)
                obj = MMDLObject(name, ident, (first,) + remaining)
                obj._material_count = material_count
                children.append(obj)
                objects.append(obj)

            # Structure 3 records immediately follow each Structure 1's children.
            for obj in children:
                for _ in range(obj._material_count):
                    u1 = _read_u(f)
                    vo = _read_u(f)
                    vc = _read_u(f)
                    fo = _read_u(f)
                    fc = _read_u(f)
                    ka = tuple(_read_f(f) for _ in range(3))
                    opacity = _read_f(f)
                    kd = tuple(_read_f(f) for _ in range(3))
                    ks = tuple(_read_f(f) for _ in range(3))
                    ni = _read_f(f)
                    unknown_floats = tuple(_read_f(f) for _ in range(5))
                    ns = _read_f(f)
                    material_name = _read_s(f, 32)
                    unknown7 = _read_f(f)
                    texture_name = _read_s(f, 32)
                    ident3 = _read_u(f)
                    obj.materials.append(MMDLMaterial(
                        u1, vo, vc, fo, fc, ka, opacity, kd, ks, ni,
                        unknown_floats, ns, material_name, unknown7,
                        texture_name, ident3,
                    ))
                del obj._material_count

        vertices = []
        f.seek(16 + object_table_size)
        for _ in range(vertex_count):
            x, y, z = _read_f(f), _read_f(f), _read_f(f)
            if vertex_entry_size == 36:
                nx, ny, nz = _read_f(f), _read_f(f), _read_f(f)
            else:
                nx = ny = nz = None
            colour = _read_u(f)
            u, v = _read_f(f), _read_f(f)
            vertices.append((x, y, z, nx, ny, nz, colour, u, v))

        faces = []
        f.seek(16 + object_table_size + vertex_table_size)
        for _ in range(face_count):
            faces.append(_read_u(f, face_entry_size))

    return MMDLData(
        path, vertex_entry_size, vertices, faces, objects,
        {
            "object_table_size": object_table_size,
            "vertex_table_size": vertex_table_size,
            "face_table_size": face_table_size,
            "unknown1": unknown1,
            "vertex_count": vertex_count,
            "face_entry_size": face_entry_size,
            "face_count": face_count,
            "base_id": base_id,
            "base_floats": base_floats,
            "base_ints": base_ints,
        },
    )




@dataclass
class MIATexture:
    name: str
    width: int
    height: int
    ident: int
    format: int
    pixels: list
    archive_path: str


class MIAArchive:
    """Reader for SBOL MIA texture cabinets (raw BGRA or RLE BGRA)."""

    def __init__(self, path):
        self.path = path
        self.mia_type = 0
        self.textures = {}
        self._read()

    @staticmethod
    def _normalise_name(name):
        base = os.path.basename(name.strip().replace("\\", "/"))
        return os.path.splitext(base)[0].casefold()

    def _read(self):
        with open(self.path, "rb") as f:
            data = f.read()
        if len(data) < 4:
            raise ValueError(f"MIA file is too small: {self.path}")

        texture_count, self.mia_type = struct.unpack_from("<HH", data, 0)
        if self.mia_type not in (0, 1):
            raise ValueError(f"Unsupported MIA type {self.mia_type}: {self.path}")

        table_end = 4 + texture_count * 4
        if table_end > len(data):
            raise ValueError(f"Invalid MIA pointer table: {self.path}")

        pointers = list(struct.unpack_from(f"<{texture_count}I", data, 4))
        pointers.append(len(data))
        entry_size = 4 if self.mia_type == 0 else 5

        for index in range(texture_count):
            start = pointers[index]
            end = pointers[index + 1]
            if start < table_end or end > len(data) or end < start + 0x30:
                raise ValueError(f"Invalid MIA texture pointer {index}: {self.path}")

            ident = struct.unpack_from("<I", data, start)[0]
            raw_name = data[start + 4:start + 0x24]
            name = raw_name.split(b"\0", 1)[0].decode("utf-8", errors="replace")
            texture_format = struct.unpack_from("<I", data, start + 0x28)[0]
            width, height = struct.unpack_from("<HH", data, start + 0x2C)
            if width == 0 or height == 0:
                continue

            payload = data[start + 0x30:end]
            entry_count = len(payload) // entry_size
            pixels = [0.0] * (width * height * 4)
            pixel_offset = 0

            for entry_index in range(entry_count):
                offset = entry_index * entry_size
                if self.mia_type == 0:
                    repeat = 1
                    b, g, r, a = payload[offset:offset + 4]
                else:
                    repeat = payload[offset] + 1
                    b, g, r, a = payload[offset + 1:offset + 5]

                for _ in range(repeat):
                    if pixel_offset >= width * height:
                        break
                    x = pixel_offset % width
                    source_y = pixel_offset // width
                    # Match MIA Manager's vertical flip. Blender pixel rows start at the bottom.
                    dest_y = height - source_y - 1
                    dest = (dest_y * width + x) * 4
                    pixels[dest:dest + 4] = (r / 255.0, g / 255.0, b / 255.0, a / 255.0)
                    pixel_offset += 1

            if pixel_offset < width * height:
                raise ValueError(
                    f"Texture {name!r} in {os.path.basename(self.path)} decodes to "
                    f"{pixel_offset} of {width * height} pixels"
                )

            texture = MIATexture(
                name=name,
                width=width,
                height=height,
                ident=ident,
                format=texture_format,
                pixels=pixels,
                archive_path=self.path,
            )
            key = self._normalise_name(name)
            if key and key not in self.textures:
                self.textures[key] = texture

    def get(self, texture_name):
        return self.textures.get(self._normalise_name(texture_name))


class MIATextureLibrary:
    def __init__(self, model_path, scan_all=True):
        self.model_path = os.path.abspath(model_path)
        self.archives = []
        self.errors = []
        self.image_cache = {}
        self.search_locations = []
        self._load_archives(scan_all)

    @staticmethod
    def _mia_files(folder):
        if not folder or not os.path.isdir(folder):
            return []
        try:
            return [
                os.path.join(folder, name)
                for name in os.listdir(folder)
                if name.casefold().endswith(".mia")
            ]
        except OSError:
            return []

    def _game_data_root(self):
        """Find the enclosing data directory for paths such as data/car/*.mmdl."""
        current = os.path.dirname(self.model_path)
        while True:
            if os.path.basename(current).casefold() == "data":
                return current
            parent = os.path.dirname(current)
            if parent == current:
                return None
            current = parent

    def _candidate_archives(self, scan_all):
        model_folder = os.path.dirname(self.model_path)
        model_stem = os.path.splitext(os.path.basename(self.model_path))[0]
        stem_cf = model_stem.casefold()
        data_root = self._game_data_root()

        candidates = []
        seen = set()

        def add(path, priority):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen and os.path.isfile(path):
                seen.add(key)
                candidates.append((priority, os.path.basename(path).casefold(), path))

        # Development/export convenience: still support cabinets beside the MMDL.
        for path in self._mia_files(model_folder):
            archive_stem = os.path.splitext(os.path.basename(path))[0].casefold()
            if archive_stem == stem_cf:
                add(path, 0)
            elif scan_all:
                add(path, 90)
        self.search_locations.append(model_folder)

        if data_root:
            tex_root = os.path.join(data_root, "tex")
            car_root = os.path.join(tex_root, "car")
            cn_root = os.path.join(tex_root, "cn")
            self.search_locations.extend([car_root, cn_root, tex_root])

            # 1. Car-specific cabinet: data/tex/car/AE86T3.mia
            for path in self._mia_files(car_root):
                archive_stem = os.path.splitext(os.path.basename(path))[0].casefold()
                if archive_stem == stem_cf:
                    add(path, 10)

            # 2. Matching CN cabinet, commonly CN_AE86T3.mia.
            for path in self._mia_files(cn_root):
                archive_stem = os.path.splitext(os.path.basename(path))[0].casefold()
                if archive_stem in {stem_cf, f"cn_{stem_cf}"}:
                    add(path, 20)
                elif scan_all:
                    add(path, 40)

            # 3. General/shared cabinets directly in data/tex/*.mia.
            for path in self._mia_files(tex_root):
                if os.path.dirname(path) == tex_root and scan_all:
                    add(path, 30)

        candidates.sort(key=lambda item: (item[0], item[1]))
        if not scan_all:
            candidates = [item for item in candidates if item[0] in (0, 10, 20)]
        return [item[2] for item in candidates]

    def _load_archives(self, scan_all):
        candidates = self._candidate_archives(scan_all)
        if not candidates:
            self.errors.append("No MIA archives found in the model or game texture locations")
            return

        for path in candidates:
            try:
                self.archives.append(MIAArchive(path))
            except Exception as exc:
                self.errors.append(f"{path}: {exc}")

    def find(self, texture_name):
        for archive in self.archives:
            texture = archive.get(texture_name)
            if texture is not None:
                return texture
        return None

    def blender_image(self, texture_name):
        texture = self.find(texture_name)
        if texture is None:
            return None
        cache_key = (texture.archive_path.casefold(), texture.name.casefold())
        image = self.image_cache.get(cache_key)
        if image is not None:
            return image

        image_name = f"{os.path.basename(texture.archive_path)}::{texture.name}"
        image = bpy.data.images.get(image_name)
        if image is None:
            image = bpy.data.images.new(
                image_name,
                width=texture.width,
                height=texture.height,
                alpha=True,
            )
            image.pixels.foreach_set(texture.pixels)
            image.pack()
            image.update()
        image["mia_archive"] = os.path.basename(texture.archive_path)
        image["mia_archive_path"] = texture.archive_path
        image["mia_texture_name"] = texture.name
        image["mia_texture_id"] = texture.ident
        image["mia_texture_format"] = texture.format
        self.image_cache[cache_key] = image
        return image


def unpack_colour(value):
    # Mirrors the converter's practical RGB output. Packed byte order is BGRA/ARGB-like.
    b0 = (value >> 0) & 0xFF
    b1 = (value >> 8) & 0xFF
    b2 = (value >> 16) & 0xFF
    b3 = (value >> 24) & 0xFF
    return (b1 / 255.0, b2 / 255.0, b3 / 255.0, b0 / 255.0)


def find_texture(model_path, texture_name):
    if not texture_name:
        return None
    base = os.path.dirname(model_path)
    roots = [base, os.path.join(base, "textures"), os.path.join(os.path.dirname(base), "textures")]
    names = [texture_name]
    if not os.path.splitext(texture_name)[1]:
        names = [texture_name + ext for ext in (".png", ".dds", ".tga", ".bmp", ".jpg", ".jpeg")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        lower_map = {n.lower(): n for n in os.listdir(root)}
        for name in names:
            actual = lower_map.get(name.lower())
            if actual:
                return os.path.join(root, actual)
    return None


def make_material(src, model_path, load_textures=True, mia_library=None):
    name = src.material_name or "MMDL_Material"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*src.kd, max(0.0, min(1.0, src.opacity)))
    mat["mmdl_texture"] = src.texture_name
    mat["mmdl_id"] = src.ident
    mat["mmdl_vertex_offset"] = src.vertex_offset
    mat["mmdl_vertex_count"] = src.vertex_count
    mat["mmdl_face_offset"] = src.face_offset
    mat["mmdl_face_count"] = src.face_count

    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        if "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = (*src.kd, 1.0)
        if "Roughness" in bsdf.inputs:
            # Preserve a useful visual approximation without claiming exact shader equivalence.
            bsdf.inputs["Roughness"].default_value = 1.0 - max(0.0, min(1.0, src.ns / 1000.0))
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = max(0.0, min(1.0, src.opacity))

    if src.opacity < 0.999:
        try:
            mat.surface_render_method = 'DITHERED'  # Blender 4.2+
        except Exception:
            try:
                mat.blend_method = 'BLEND'
            except Exception:
                pass

    if load_textures and src.texture_name and bsdf:
        image = None
        # Prefer MIA cabinets because these are the game's authoritative texture libraries.
        if mia_library is not None:
            try:
                image = mia_library.blender_image(src.texture_name)
            except Exception:
                image = None
        if image is None:
            tex_path = find_texture(model_path, src.texture_name)
            if tex_path:
                try:
                    image = bpy.data.images.load(tex_path, check_existing=True)
                except Exception:
                    image = None
        if image is not None:
            tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex.image = image
            tex.label = src.texture_name
            mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if "Alpha" in tex.outputs and "Alpha" in bsdf.inputs:
                mat.node_tree.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
    return mat


def classify_name(name):
    upper = name.upper()
    if "REFLECT" in upper or "HEADLIGHT" in upper:
        return "Headlight Projection"
    if "COL" in upper or "HIT" in upper:
        return "Collision"
    if upper.endswith("_A") or upper.endswith("-A"):
        return "LOD A"
    if upper.endswith("_B") or upper.endswith("-B"):
        return "LOD B"
    if upper.endswith("_C") or upper.endswith("-C"):
        return "LOD C"
    if upper.endswith("_D") or upper.endswith("-D"):
        return "Auxiliary D"
    return "Unclassified"


def ensure_collection(parent, name):
    child = bpy.data.collections.get(name)
    if child is None:
        child = bpy.data.collections.new(name)
        parent.children.link(child)
    elif child.name not in parent.children:
        try:
            parent.children.link(child)
        except RuntimeError:
            pass
    return child


def import_mmdl(context, path, load_textures=True, load_mia=True, scan_all_mia=True, use_vertex_colours=True, hide_auxiliary=False):
    data = read_mmdl(path)
    root_name = os.path.splitext(os.path.basename(path))[0]
    root = bpy.data.collections.new(root_name)
    context.scene.collection.children.link(root)
    subcollections = {}
    material_cache = {}
    mia_library = MIATextureLibrary(path, scan_all=scan_all_mia) if load_textures and load_mia else None

    for source_obj in data.objects:
        if not source_obj.materials:
            continue

        # Match mmdl2obj: one OBJ object per Object2, with Object3 records as material groups.
        tris = []
        tri_materials = []
        referenced = set()
        valid_materials = []
        for mat_index, src_mat in enumerate(source_obj.materials):
            valid_materials.append(src_mat)
            for i in range(src_mat.face_count):
                p = src_mat.face_offset + i * 3
                if p + 2 >= len(data.faces):
                    raise ValueError(f"Face range outside table in {source_obj.name}")
                tri = (data.faces[p], data.faces[p + 1], data.faces[p + 2])
                if max(tri) >= len(data.vertices):
                    raise ValueError(f"Vertex index outside table in {source_obj.name}")
                tris.append(tri)
                tri_materials.append(mat_index)
                referenced.update(tri)

        old_indices = sorted(referenced)
        remap = {old: new for new, old in enumerate(old_indices)}
        # Match the working OBJ pipeline. MMDL is Y-up; Blender is Z-up.
        # mmdl2obj first mirrors X, then Blender's OBJ importer maps (X,Y,Z) -> (X,-Z,Y).
        coords = [(-data.vertices[i][0], -data.vertices[i][2], data.vertices[i][1]) for i in old_indices]
        local_tris = [tuple(remap[i] for i in tri) for tri in tris]

        mesh = bpy.data.meshes.new(source_obj.name)
        mesh.from_pydata(coords, [], local_tris)
        mesh.update()
        obj = bpy.data.objects.new(source_obj.name, mesh)

        category = classify_name(source_obj.name)
        coll = subcollections.get(category)
        if coll is None:
            coll = bpy.data.collections.new(category)
            root.children.link(coll)
            subcollections[category] = coll
        coll.objects.link(obj)

        for src_mat in valid_materials:
            cache_key = (src_mat.material_name, src_mat.texture_name)
            mat = material_cache.get(cache_key)
            if mat is None:
                mat = make_material(src_mat, path, load_textures, mia_library)
                material_cache[cache_key] = mat
            mesh.materials.append(mat)
        for poly, mat_index in zip(mesh.polygons, tri_materials):
            poly.material_index = mat_index
            poly.use_smooth = False  # exactly matches OBJ converter's "s off"

        uv_layer = mesh.uv_layers.new(name="UVMap")
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                old_index = old_indices[mesh.loops[loop_index].vertex_index]
                vert = data.vertices[old_index]
                uv_layer.data[loop_index].uv = (vert[7], -vert[8])

        if use_vertex_colours:
            try:
                colour_layer = mesh.color_attributes.new(name="MMDL Colour", type='BYTE_COLOR', domain='POINT')
                for local_index, old_index in enumerate(old_indices):
                    colour_layer.data[local_index].color = unpack_colour(data.vertices[old_index][6])
            except Exception:
                pass

        if data.vertex_entry_size == 36:
            # Apply the same Y-up to Z-up rotation used for positions. Keep X as emitted
            # by the existing OBJ converter so the native import matches its result.
            normals = [(data.vertices[i][3], -data.vertices[i][5], data.vertices[i][4]) for i in old_indices]
            try:
                mesh.normals_split_custom_set_from_vertices(normals)
            except Exception:
                # Blender versions that removed explicit custom-normal setters still calculate valid normals.
                pass

        obj["mmdl_source"] = os.path.basename(path)
        obj["mmdl_object_id"] = source_obj.ident
        obj["mmdl_category_guess"] = category
        obj["mmdl_material_groups"] = len(source_obj.materials)
        obj["mmdl_original_metadata"] = list(source_obj.unknown_values)

        if hide_auxiliary and category in {"Collision", "Headlight Projection", "Auxiliary D"}:
            obj.hide_set(True)
            obj.hide_render = True

    root["mmdl_vertex_entry_size"] = data.vertex_entry_size
    root["mmdl_vertex_count"] = data.header["vertex_count"]
    root["mmdl_index_count"] = data.header["face_count"]
    if mia_library is not None:
        root["mia_archives_loaded"] = [os.path.basename(a.path) for a in mia_library.archives]
        root["mia_archive_errors"] = mia_library.errors
        root["mia_search_locations"] = mia_library.search_locations
    return root


class IMPORT_SCENE_OT_mmdl(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.mmdl"
    bl_label = "Import SBOL MMDL"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".mmdl"
    filter_glob: StringProperty(default="*.mmdl", options={'HIDDEN'})
    load_textures: BoolProperty(
        name="Load textures",
        description="Search beside the MMDL and in nearby textures folders",
        default=True,
    )
    load_mia: BoolProperty(
        name="Load MIA texture cabinets",
        description="Load MIA textures from the SB Online data/tex hierarchy",
        default=True,
    )
    scan_all_mia: BoolProperty(
        name="Search shared MIA libraries",
        description="Search data/tex/car, data/tex/cn and general data/tex MIA libraries after the matching car cabinet",
        default=True,
    )
    use_vertex_colours: BoolProperty(
        name="Import vertex colours",
        default=True,
    )
    hide_auxiliary: BoolProperty(
        name="Hide auxiliary meshes",
        description="Hide collision, headlight projection and D-group objects after import",
        default=False,
    )

    def execute(self, context):
        try:
            root = import_mmdl(
                context,
                self.filepath,
                load_textures=self.load_textures,
                load_mia=self.load_mia,
                scan_all_mia=self.scan_all_mia,
                use_vertex_colours=self.use_vertex_colours,
                hide_auxiliary=self.hide_auxiliary,
            )
            self.report({'INFO'}, f"Imported {root.name}")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_mmdl.bl_idname, text="SBOL MMDL (.mmdl)")


classes = (IMPORT_SCENE_OT_mmdl,)


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
