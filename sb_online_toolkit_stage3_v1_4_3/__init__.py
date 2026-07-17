bl_info = {
    "name": "SB Online Toolkit - Stage 3 CARMODS Parts Browser",
    "author": "tofuman0 / OpenAI",
    "version": (1, 4, 3),
    "blender": (3, 6, 0),
    "location": "File > Import > SBOL MMDL (.mmdl)",
    "description": "SB Online MMDL importer, diagnostics and CARMODS-driven parts browser",
    "category": "Import-Export",
}

import os
import struct
import json
import re
from dataclasses import dataclass, field

import bpy
from bpy.props import BoolProperty, StringProperty, EnumProperty, IntProperty
from bpy_extras.io_utils import ImportHelper


PART_SLOT_LIMITS = {
    "body": 6,
    "overfenders": 2,
    "frontbumper": 6,
    "rearbumper": 6,
    "bonnet": 3,
    "mirrors": 3,
    "sideskirts": 6,
    "rearspoiler": 6,
    "grill": 3,
    "lights": 2,
}

PART_CATEGORY_ITEMS = [("unclassified", "Unclassified", "Not assigned to a game part slot")] + [
    (key, key.replace("bumper", " bumper").replace("sideskirts", "side skirts").replace("rearspoiler", "rear spoiler").title(), f"Up to {count} slots")
    for key, count in PART_SLOT_LIMITS.items()
]



# Confirmed SB Online Object2 naming conventions and their CARMODS fields.
# The leading numeric ordering index is ignored; suffix 0/00 denotes stock.
# `rk`, `rf` and `bl` are paired under the single CARMODS `lights` stage.
SBOL_PART_DEFINITIONS = {
    "fs": {"label": "Front Bumper", "mode": "exclusive", "field": "frontbumper"},
    "rk": {"label": "Lights", "mode": "exclusive", "field": "lights"},
    "rf": {"label": "Lights", "mode": "exclusive", "field": "lights"},
    "bl": {"label": "Lights", "mode": "exclusive", "field": "lights"},
    "eb": {"label": "Bonnet", "mode": "exclusive", "field": "bonnet"},
    "bm": {"label": "Mirrors", "mode": "exclusive", "field": "mirrors"},
    "ss": {"label": "Side Skirts", "mode": "exclusive", "field": "sideskirts"},
    "rb": {"label": "Rear Bumper", "mode": "exclusive", "field": "rearbumper"},
    "rw": {"label": "Rear Spoiler", "mode": "exclusive", "field": "rearspoiler"},
    "mu": {"label": "Muffler", "mode": "exclusive", "field": "muffler"},
    "gk": {"label": "Grille", "mode": "exclusive", "field": "grill"},
    "of": {"label": "Overfenders", "mode": "exclusive", "field": "overfenders"},
    "in": {"label": "Interior / Body", "mode": "progressive", "field": "body"},
    "kage": {"label": "Collision Mesh", "mode": "toggle", "field": None},
    "nm": {"label": "Number Plate", "mode": "dependent", "field": None},
}

# One browser control per CARMODS field. A field may drive several mesh prefixes.
SBOL_PART_GROUPS = (
    {"field": "body", "label": "Interior / Body", "mode": "progressive", "prefixes": ("in",)},
    {"field": "muffler", "label": "Muffler", "mode": "exclusive", "prefixes": ("mu",)},
    {"field": "overfenders", "label": "Overfenders", "mode": "exclusive", "prefixes": ("of",)},
    {"field": "frontbumper", "label": "Front Bumper", "mode": "exclusive", "prefixes": ("fs",)},
    {"field": "bonnet", "label": "Bonnet", "mode": "exclusive", "prefixes": ("eb",)},
    {"field": "mirrors", "label": "Mirrors", "mode": "exclusive", "prefixes": ("bm",)},
    {"field": "sideskirts", "label": "Side Skirts", "mode": "exclusive", "prefixes": ("ss",)},
    {"field": "rearbumper", "label": "Rear Bumper", "mode": "exclusive", "prefixes": ("rb",)},
    {"field": "rearspoiler", "label": "Rear Spoiler", "mode": "exclusive", "prefixes": ("rw",)},
    {"field": "grill", "label": "Grille", "mode": "exclusive", "prefixes": ("gk",)},
    {"field": "lights", "label": "Lights", "mode": "exclusive", "prefixes": ("rk", "rf", "bl")},
    {"field": "collision", "label": "Collision Mesh", "mode": "toggle", "prefixes": ("kage",)},
)


def parse_sbol_part_name(name):
    """Return confirmed part metadata parsed from an Object2 name.

    Names generally contain a leading ordering number, then a short prefix and
    variant number, e.g. 68ss04_CAR_A. Stock is variant 0/00.
    """
    lower = name.casefold()
    for prefix in sorted(SBOL_PART_DEFINITIONS, key=len, reverse=True):
        match = re.search(rf"(?:^|\d){re.escape(prefix)}(?P<variant>\d{{1,2}})?", lower)
        if not match:
            continue
        raw = match.group("variant")
        variant = int(raw) if raw else 0
        definition = SBOL_PART_DEFINITIONS[prefix]
        return {
            "prefix": prefix,
            "category": definition["label"],
            "mode": definition["mode"],
            "field": definition.get("field"),
            "variant": variant,
            "stock": variant == 0,
            "confidence": 1.0,
        }
    return None


def _leading_object_number(name):
    """Read the stable numeric Object2 ordering prefix (for example 05fs -> 5)."""
    match = re.match(r"^(\d+)", name)
    return int(match.group(1)) if match else None


def _variant_from_object_order(name, prefix, parsed_variant):
    """Resolve the CARMODS stage from SBOL's stable Object2 layout.

    Many models do not encode the stage reliably in the text after the prefix
    (AE86T3 uses names such as 05fs, 06fs, ...).  The leading Object2 number
    is consistent across the cars examined and is therefore the authoritative
    source for these known groups.
    """
    number = _leading_object_number(name)
    if number is None:
        return parsed_variant

    if prefix == "fs" and 5 <= number <= 22:
        return (number - 5) // 3
    if prefix == "gk" and 23 <= number <= 25:
        return number - 23
    if prefix in {"rk", "rf", "bl"}:
        if 26 <= number <= 31:
            return (number - 26) // 2
        if 105 <= number <= 110:
            return (number - 105) // 2
    if prefix == "eb" and 32 <= number <= 34:
        return number - 32
    if prefix == "bm" and 35 <= number <= 37:
        return number - 35
    if prefix == "ss":
        order = [38, 39, 40, 41, 68, 69]
        if number in order:
            return order.index(number)
    if prefix == "rb":
        order = [42, 43, 44, 45, 70, 71]
        if number in order:
            return order.index(number)
    if prefix == "rw" and 46 <= number <= 51:
        return number - 46
    if prefix == "mu" and 52 <= number <= 60:
        return number - 52
    if prefix == "in" and 82 <= number <= 86:
        return number - 82

    return parsed_variant


def _root_objects(root):
    seen = set()
    for coll in [root] + list(root.children_recursive):
        for obj in coll.objects:
            if obj.name in seen or not obj.get("mmdl_source"):
                continue
            seen.add(obj.name)
            yield obj


def _part_objects(root, prefix):
    return [obj for obj in _root_objects(root) if obj.get("sbol_part_prefix") == prefix]


def _group_definition(field_name):
    return next((group for group in SBOL_PART_GROUPS if group["field"] == field_name), None)


def _group_objects(root, field_name):
    group = _group_definition(field_name)
    if not group:
        return []
    prefixes = set(group["prefixes"])
    return [obj for obj in _root_objects(root) if obj.get("sbol_part_prefix") in prefixes]


def _set_object_visible(obj, visible):
    obj.hide_set(not visible)
    obj.hide_render = not visible


ALPHA_MODE_ITEMS = [
    ("OPAQUE", "Opaque", "Ignore texture alpha for all materials"),
    ("SPECIAL", "Selected materials", "Use masked alpha only for likely glass, pane or PUNCH materials"),
    ("ALL", "Masked alpha for all textures", "Treat alpha 0 as transparent and every non-zero alpha value as opaque"),
]


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


def _material_alpha_strategy(src, alpha_mode):
    """Choose the shader treatment used by the original texture type.

    Glass/window textures retain their real alpha values. Light projection
    textures use their colour brightness as transparency because their MIA
    alpha channel is commonly fully opaque while the background is black.
    Other textured materials use a hard alpha cut-out when requested.
    """
    if alpha_mode == "OPAQUE":
        return "OPAQUE"

    text = f"{src.material_name} {src.texture_name}".upper()
    glass_tokens = ("GLASS", "WINDOW", "WINDSCREEN", "WINDSHIELD", "PANE", "IMPANE")
    light_tokens = ("ROAD_REF", "MDL_REFLECT", "HEADLIGHT", "HDL_", "LIGHT_ENV", "LIGHTBEAM")

    if any(token in text for token in glass_tokens):
        return "BLEND"
    if any(token in text for token in light_tokens):
        return "LIGHT"
    if alpha_mode == "ALL":
        return "MASK"
    if "PUNCH" in text:
        return "MASK"
    return "OPAQUE"


def _set_material_render_method(mat, strategy):
    """Configure Blender's viewport/render transparency for a strategy."""
    transparent = strategy != "OPAQUE"
    if not transparent:
        try:
            mat.surface_render_method = 'DITHERED'
        except Exception:
            pass
        try:
            mat.blend_method = 'OPAQUE'
        except Exception:
            pass
        return

    try:
        # Blender 4.2+: DITHERED supports smooth glass and shader-generated masks.
        mat.surface_render_method = 'DITHERED'
    except Exception:
        pass
    try:
        mat.blend_method = 'BLEND' if strategy == "BLEND" else 'CLIP'
        if strategy != "BLEND":
            mat.alpha_threshold = 0.01
        mat.show_transparent_back = True
    except Exception:
        pass

def _link_masked_texture_alpha(nodes, links, image_node, bsdf):
    """Convert image alpha to a binary mask: alpha == 0 is hidden, otherwise opaque."""
    if "Alpha" not in image_node.outputs or "Alpha" not in bsdf.inputs:
        return
    alpha_input = bsdf.inputs["Alpha"]
    for link in list(alpha_input.links):
        links.remove(link)
    mask = nodes.new("ShaderNodeMath")
    mask.operation = 'GREATER_THAN'
    mask.inputs[1].default_value = 0.01
    mask.label = "SBOL Alpha Mask"
    mask["sbol_generated"] = True
    links.new(image_node.outputs["Alpha"], mask.inputs[0])
    links.new(mask.outputs[0], alpha_input)


def _link_texture_alpha(nodes, links, image_node, bsdf):
    """Preserve the MIA texture's real alpha, including half-transparent glass."""
    if "Alpha" not in image_node.outputs or "Alpha" not in bsdf.inputs:
        return
    alpha_input = bsdf.inputs["Alpha"]
    for link in list(alpha_input.links):
        links.remove(link)
    links.new(image_node.outputs["Alpha"], alpha_input)


def _link_light_texture(nodes, links, image_node, bsdf):
    """Make black-backed projected-light textures transparent and emissive."""
    if "Alpha" not in bsdf.inputs:
        return
    # Value is max(R,G,B): black becomes zero while the coloured beam remains.
    rgb_to_bw = nodes.new("ShaderNodeRGBToBW")
    rgb_to_bw.label = "SBOL Light Brightness Mask"
    rgb_to_bw["sbol_generated"] = True
    links.new(image_node.outputs["Color"], rgb_to_bw.inputs["Color"])

    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.label = "SBOL Light Black Key"
    ramp["sbol_generated"] = True
    ramp.color_ramp.elements[0].position = 0.01
    ramp.color_ramp.elements[1].position = 0.08
    links.new(rgb_to_bw.outputs["Val"], ramp.inputs["Fac"])

    alpha_input = bsdf.inputs["Alpha"]
    for link in list(alpha_input.links):
        links.remove(link)
    links.new(ramp.outputs["Color"], alpha_input)

    # Projected light cards should glow rather than render as dark painted quads.
    if "Emission Color" in bsdf.inputs:
        links.new(image_node.outputs["Color"], bsdf.inputs["Emission Color"])
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 1.0
    elif "Emission" in bsdf.inputs:
        links.new(image_node.outputs["Color"], bsdf.inputs["Emission"])
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 1.0


def _enable_material_texture_alpha(mat):
    """Enable hard-masked image alpha on an existing node material."""
    if not mat or not mat.use_nodes or not mat.node_tree:
        return
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None or "Alpha" not in bsdf.inputs:
        return
    image_nodes = [node for node in nodes if node.type == 'TEX_IMAGE' and node.image]
    if image_nodes:
        _link_texture_alpha(nodes, links, image_nodes[0], bsdf)
    mat["sbol_alpha_enabled"] = True
    mat["sbol_alpha_mode"] = "BLEND"
    mat["sbol_alpha_threshold"] = 0.0
    _set_material_render_method(mat, "BLEND")


def _enable_body_object_alpha(obj):
    """Body textures contain windows and light-beam cut-outs, so preserve their alpha."""
    if obj.type != 'MESH':
        return
    for index, mat in enumerate(list(obj.data.materials)):
        if mat is None:
            continue
        text = f"{mat.name} {mat.get('mmdl_texture', '')}".upper()
        if not any(token in text for token in ("GLASS", "WINDOW", "WINDSCREEN", "WINDSHIELD", "PANE", "IMPANE")):
            continue
        # Do not alter a cached material shared by unrelated objects.
        body_mat = mat.copy()
        body_mat.name = f"{mat.name}_BodyGlass"
        obj.data.materials[index] = body_mat
        _enable_material_texture_alpha(body_mat)


def _number_plate_objects(root):
    return sorted(
        [obj for obj in _root_objects(root) if obj.get("sbol_part_prefix") == "nm"],
        key=lambda obj: int(obj.get("sbol_object_order", 1_000_000)),
    )


def _update_number_plates(root, front_stage=0):
    """Apply the observed plate ordering: stock front, stock rear, then front stages 1+."""
    plates = _number_plate_objects(root)
    if not plates:
        return
    for obj in plates:
        _set_object_visible(obj, False)
    # First plate is stock front; subsequent front variants start at the third entry.
    front_index = 0 if front_stage == 0 else front_stage + 1
    if front_index < len(plates):
        _set_object_visible(plates[front_index], True)
    # Second plate is the stock rear plate and remains visible where no rear alternatives exist.
    if len(plates) > 1:
        _set_object_visible(plates[1], True)


def make_material(src, model_path, load_textures=True, mia_library=None, alpha_mode="SPECIAL"):
    name = src.material_name or "MMDL_Material"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    alpha_strategy = _material_alpha_strategy(src, alpha_mode)
    use_alpha = alpha_strategy != "OPAQUE"
    mat.diffuse_color = (*src.kd, 1.0)
    mat["mmdl_texture"] = src.texture_name
    mat["mmdl_id"] = src.ident
    mat["mmdl_vertex_offset"] = src.vertex_offset
    mat["mmdl_vertex_count"] = src.vertex_count
    mat["mmdl_face_offset"] = src.face_offset
    mat["mmdl_face_count"] = src.face_count
    mat["mmdl_d_raw"] = src.opacity
    mat["mmdl_ka"] = list(src.ka)
    mat["mmdl_kd"] = list(src.kd)
    mat["mmdl_ks"] = list(src.ks)
    mat["mmdl_ni"] = src.ni
    mat["mmdl_ns"] = src.ns
    mat["mmdl_unknown_floats"] = list(src.unknown_floats)
    mat["mmdl_unknown7"] = src.unknown7
    mat["sbol_alpha_enabled"] = use_alpha

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    # Remove image nodes created by an earlier import so repeated imports do not stack nodes.
    for node in list(nodes):
        if node.get("sbol_generated"):
            nodes.remove(node)
    if bsdf:
        if "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = (*src.kd, 1.0)
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 1.0 - max(0.0, min(1.0, src.ns / 1000.0))
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = 1.0

    _set_material_render_method(mat, alpha_strategy)

    if load_textures and src.texture_name and bsdf:
        image = None
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
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = image
            tex.label = src.texture_name
            tex["sbol_generated"] = True
            links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if alpha_strategy == "BLEND":
                _link_texture_alpha(nodes, links, tex, bsdf)
                mat["sbol_alpha_mode"] = "BLEND"
                mat["sbol_alpha_threshold"] = 0.0
            elif alpha_strategy == "LIGHT":
                _link_light_texture(nodes, links, tex, bsdf)
                mat["sbol_alpha_mode"] = "LIGHT_BLACK_KEY"
                mat["sbol_alpha_threshold"] = 0.01
            elif alpha_strategy == "MASK":
                _link_masked_texture_alpha(nodes, links, tex, bsdf)
                mat["sbol_alpha_mode"] = "MASKED"
                mat["sbol_alpha_threshold"] = 0.01
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


def _read_matching_prts(mmdl_path):
    """Read the companion PRTS conservatively without assuming object mapping."""
    base = os.path.splitext(mmdl_path)[0]
    candidates = [base + ext for ext in (".PRTS", ".prts", ".Prts")]
    prts_path = next((candidate for candidate in candidates if os.path.isfile(candidate)), None)
    if prts_path is None:
        return None
    try:
        with open(prts_path, "rb") as stream:
            data = stream.read()
        if len(data) < 976:
            return {"path": prts_path, "size": len(data), "valid": False, "error": "File is shorter than 976 bytes"}
        tables = []
        for index in range(4):
            start = 16 + index * 240
            table = list(data[start:start + 240])
            active = [value for value in table if value != 0xFF]
            tables.append({"active_count": len(active), "max_value": max(active, default=None)})
        return {"path": prts_path, "size": len(data), "valid": True, "header": list(data[:16]), "tables": tables}
    except Exception as exc:
        return {"path": prts_path, "size": 0, "valid": False, "error": str(exc)}


def import_mmdl(context, path, load_textures=True, load_mia=True, scan_all_mia=True, use_vertex_colours=True, hide_auxiliary=False, alpha_mode="SPECIAL"):
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
                mat = make_material(src_mat, path, load_textures, mia_library, alpha_mode)
                material_cache[cache_key] = mat
            mesh.materials.append(mat)
        for poly, mat_index in zip(mesh.polygons, tri_materials):
            poly.material_index = mat_index
            poly.use_smooth = False  # exactly matches OBJ converter's "s off"

        if source_obj.name.casefold().startswith("00body"):
            _enable_body_object_alpha(obj)
            obj["sbol_body_alpha_forced"] = True

        uv_layer = mesh.uv_layers.new(name="UVMap")
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                old_index = old_indices[mesh.loops[loop_index].vertex_index]
                vert = data.vertices[old_index]
                uv_layer.data[loop_index].uv = (vert[7], vert[8])

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
        obj["mmdl_root_collection"] = root.name
        obj["mmdl_object_id"] = source_obj.ident
        obj["mmdl_category_guess"] = category
        obj["mmdl_material_groups"] = len(source_obj.materials)
        obj["mmdl_original_metadata"] = list(source_obj.unknown_values)
        obj["mmdl_vertex_count_local"] = len(mesh.vertices)
        obj["mmdl_triangle_count_local"] = len(mesh.polygons)
        obj["mmdl_material_names"] = [m.material_name for m in source_obj.materials]
        obj["mmdl_texture_names"] = [m.texture_name for m in source_obj.materials]
        part_info = parse_sbol_part_name(source_obj.name)
        if part_info:
            obj["sbol_part_prefix"] = part_info["prefix"]
            obj["sbol_part_category"] = part_info["category"]
            obj["sbol_part_mode"] = part_info["mode"]
            obj["sbol_carmods_field"] = part_info.get("field") or ""
            resolved_variant = _variant_from_object_order(
                source_obj.name, part_info["prefix"], part_info["variant"]
            )
            obj["sbol_part_variant"] = resolved_variant
            obj["sbol_part_is_stock"] = resolved_variant == 0
            obj["sbol_object_order"] = _leading_object_number(source_obj.name) or -1
            obj["sbol_part_confidence"] = part_info["confidence"]

        if not part_info:
            obj["sbol_part_category"] = "Main Body" if source_obj.name.lower().startswith("00body") else "Unclassified"
            obj["sbol_part_index"] = 0
        # Conservative placeholder hint only; it never deletes or auto-hides geometry.
        dims = obj.dimensions
        near_origin = obj.location.length < 0.01
        boxlike = len(mesh.polygons) <= 12 and len(mesh.vertices) <= 24
        tiny = max(dims) < 0.5 if len(dims) else False
        obj["sbol_probable_placeholder"] = bool(near_origin and boxlike and tiny)

        # Collision geometry is editing/reference data and should start hidden.
        # Test the parsed prefix directly because source category guesses vary by model.
        if part_info and part_info["prefix"] == "kage":
            _set_object_visible(obj, False)
        elif hide_auxiliary and category in {"Collision", "Headlight Projection", "Auxiliary D"}:
            _set_object_visible(obj, False)

    root["mmdl_vertex_entry_size"] = data.vertex_entry_size
    root["mmdl_vertex_count"] = data.header["vertex_count"]
    root["mmdl_index_count"] = data.header["face_count"]
    root["sbol_parts_version"] = 5
    prts = _read_matching_prts(path)
    if prts is not None:
        root["sbol_prts_path"] = prts.get("path", "")
        root["sbol_prts_size"] = int(prts.get("size", 0))
        root["sbol_prts_valid"] = bool(prts.get("valid", False))
        root["sbol_prts_error"] = prts.get("error", "")
        if prts.get("valid"):
            root["sbol_prts_header"] = prts.get("header", [])
            root["sbol_prts_active_counts"] = [table["active_count"] for table in prts["tables"]]
            root["sbol_prts_max_values"] = [(-1 if table["max_value"] is None else table["max_value"]) for table in prts["tables"]]
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
    alpha_mode: EnumProperty(
        name="Texture alpha",
        description="Use hard-masked alpha so alpha 0 is transparent without making panels semi-transparent",
        items=ALPHA_MODE_ITEMS,
        default="ALL",
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
                alpha_mode=self.alpha_mode,
            )
            self.report({'INFO'}, f"Imported {root.name}")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}




def _find_import_root(context):
    """Return the root Collection belonging to the selected imported mesh.

    Stage 1 stores model summary properties on the root collection, not on an
    object. The previous implementation incorrectly searched scene objects.
    """
    active = context.active_object
    if active is None:
        return None

    root_name = active.get("mmdl_root_collection")
    if root_name:
        root = bpy.data.collections.get(root_name)
        if root is not None and root.get("mmdl_vertex_count") is not None:
            return root

    # Backward-compatible fallback for scenes imported with v1.0.0.
    source = active.get("mmdl_source")
    if source:
        stem = os.path.splitext(os.path.basename(source))[0]
        root = bpy.data.collections.get(stem)
        if root is not None and root.get("mmdl_vertex_count") is not None:
            return root

    return None


def _vec3_text(vec):
    return f"{vec[0]:.4f}, {vec[1]:.4f}, {vec[2]:.4f}"


def _active_mmdl_material(obj):
    if obj is None or obj.type != 'MESH' or not obj.material_slots:
        return None
    index = max(0, min(obj.active_material_index, len(obj.material_slots) - 1))
    return obj.material_slots[index].material


def _json_safe(value):
    """Convert Blender/RNA/IDProperty values into JSON-native values."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}

    # Blender IDPropertyArray exposes to_list() on supported versions.
    to_list = getattr(value, "to_list", None)
    if callable(to_list):
        try:
            return [_json_safe(v) for v in to_list()]
        except Exception:
            pass

    # Some Blender arrays support slicing even when normal iteration is odd.
    try:
        if value.__class__.__name__ == "IDPropertyArray":
            return [_json_safe(v) for v in value[:]]
    except Exception:
        pass

    # mathutils vectors/colours, tuples, lists, and other sequence-like values.
    try:
        return [_json_safe(v) for v in list(value)]
    except Exception:
        pass

    # Numeric scalar wrappers.
    try:
        return int(value)
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        pass

    return str(value)


def _material_to_dict(mat):
    if mat is None:
        return None
    return {
        "name": mat.name,
        "texture": _json_safe(mat.get("mmdl_texture", "")),
        "id": _json_safe(mat.get("mmdl_id")),
        "vertex_offset": _json_safe(mat.get("mmdl_vertex_offset")),
        "vertex_count": _json_safe(mat.get("mmdl_vertex_count")),
        "face_offset": _json_safe(mat.get("mmdl_face_offset")),
        "face_count": _json_safe(mat.get("mmdl_face_count")),
        "d_raw": _json_safe(mat.get("mmdl_d_raw")),
        "Ka": _json_safe(mat.get("mmdl_ka", [])),
        "Kd": _json_safe(mat.get("mmdl_kd", [])),
        "Ks": _json_safe(mat.get("mmdl_ks", [])),
        "Ni": _json_safe(mat.get("mmdl_ni")),
        "Ns": _json_safe(mat.get("mmdl_ns")),
        "unknown_floats": _json_safe(mat.get("mmdl_unknown_floats", [])),
        "unknown7": _json_safe(mat.get("mmdl_unknown7")),
        "alpha_enabled": bool(mat.get("sbol_alpha_enabled", False)),
    }


def _model_diagnostic(root):
    objects = []
    for coll in [root] + list(root.children_recursive):
        for obj in coll.objects:
            if not obj.get("mmdl_source"):
                continue
            objects.append({
                "name": obj.name,
                "object_id": obj.get("mmdl_object_id"),
                "category_guess": obj.get("mmdl_category_guess", "Unclassified"),
                "vertices": len(obj.data.vertices) if obj.type == 'MESH' else 0,
                "triangles": len(obj.data.polygons) if obj.type == 'MESH' else 0,
                "dimensions": list(obj.dimensions),
                "unknown_values": list(obj.get("mmdl_original_metadata", [])),
                "probable_placeholder": bool(obj.get("sbol_probable_placeholder", False)),
                "part": {
                    "prefix": obj.get("sbol_part_prefix", ""),
                    "category": obj.get("sbol_part_category", "Unclassified"),
                    "mode": obj.get("sbol_part_mode", ""),
                    "carmods_field": obj.get("sbol_carmods_field", ""),
                    "variant": obj.get("sbol_part_variant"),
                    "stock": bool(obj.get("sbol_part_is_stock", False)),
                    "confidence": obj.get("sbol_part_confidence", 0.0),
                },
                "materials": [_material_to_dict(slot.material) for slot in obj.material_slots if slot.material],
            })
    return {
        "model": root.name,
        "vertex_entry_size": root.get("mmdl_vertex_entry_size"),
        "vertex_count": root.get("mmdl_vertex_count"),
        "index_count": root.get("mmdl_index_count"),
        "mia_archives": list(root.get("mia_archives_loaded", [])),
        "mia_errors": list(root.get("mia_archive_errors", [])),
        "prts": {
            "path": root.get("sbol_prts_path", ""),
            "size": root.get("sbol_prts_size", 0),
            "valid": bool(root.get("sbol_prts_valid", False)),
            "error": root.get("sbol_prts_error", ""),
            "header": list(root.get("sbol_prts_header", [])),
            "active_counts": list(root.get("sbol_prts_active_counts", [])),
            "max_values": list(root.get("sbol_prts_max_values", [])),
        },
        "objects": objects,
    }


class SBOL_OT_export_diagnostics(bpy.types.Operator, ImportHelper):
    bl_idname = "sbol.export_diagnostics"
    bl_label = "Export MMDL Diagnostics"
    bl_description = "Export model, object and material metadata as JSON"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        root = _find_import_root(context)
        if root is None:
            self.report({'ERROR'}, "Select an imported MMDL object")
            return {'CANCELLED'}
        self.filepath = root.name + "_diagnostics.json"
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        root = _find_import_root(context)
        if root is None:
            self.report({'ERROR'}, "Select an imported MMDL object")
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(_model_diagnostic(root), f, indent=2, ensure_ascii=False)
            self.report({'INFO'}, f"Exported {self.filepath}")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SBOL_PT_core_info(bpy.types.Panel):
    bl_label = "SB Online Model"
    bl_idname = "SBOL_PT_core_info"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SB Online"

    def draw(self, context):
        layout = self.layout
        root = _find_import_root(context)
        active = context.active_object
        if root is None:
            layout.label(text="Select an imported MMDL object")
            layout.label(text="Stage 3: CARMODS Parts Browser")
            return

        layout.label(text=root.name, icon='OUTLINER_COLLECTION')
        box = layout.box()
        box.label(text="Model")
        box.label(text=f"Vertices: {root.get('mmdl_vertex_count', 0):,}")
        box.label(text=f"Indices: {root.get('mmdl_index_count', 0):,}")
        box.label(text=f"Vertex format: {root.get('mmdl_vertex_entry_size', '?')} bytes")

        archives = root.get("mia_archives_loaded", [])
        texbox = layout.box()
        texbox.label(text=f"MIA archives: {len(archives)}")
        for name in list(archives)[:6]:
            texbox.label(text=name, icon='IMAGE_DATA')
        if len(archives) > 6:
            texbox.label(text=f"…and {len(archives)-6} more")

        prts_path = root.get("sbol_prts_path", "")
        prts_box = layout.box()
        prts_box.label(text="PRTS Companion", icon='FILE')
        if prts_path:
            prts_box.label(text=os.path.basename(prts_path))
            if root.get("sbol_prts_valid", False):
                counts = list(root.get("sbol_prts_active_counts", []))
                prts_box.label(text=f"Size: {root.get('sbol_prts_size', 0)} bytes")
                prts_box.label(text="Active entries: " + ", ".join(str(v) for v in counts))
                prts_box.label(text="Inspection only; mapping remains unproven", icon='INFO')
            else:
                prts_box.label(text=root.get("sbol_prts_error", "Invalid PRTS"), icon='ERROR')
        else:
            prts_box.label(text="No matching .PRTS found beside MMDL")

        layout.operator(SBOL_OT_export_diagnostics.bl_idname, icon='FILE_TICK')


class SBOL_PT_object_inspector(bpy.types.Panel):
    bl_label = "Object Inspector"
    bl_idname = "SBOL_PT_object_inspector"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SB Online"
    bl_parent_id = "SBOL_PT_core_info"

    @classmethod
    def poll(cls, context):
        return _find_import_root(context) is not None and context.active_object is not None

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        layout.label(text=obj.name, icon='MESH_DATA')
        col = layout.column(align=True)
        col.label(text=f"Object ID: {obj.get('mmdl_object_id', '?')}")
        col.label(text=f"Category: {obj.get('mmdl_category_guess', 'Unclassified')}")
        if obj.type == 'MESH':
            col.label(text=f"Vertices: {len(obj.data.vertices):,}")
            col.label(text=f"Triangles: {len(obj.data.polygons):,}")
            col.label(text=f"Material groups: {len(obj.material_slots)}")
            col.label(text=f"Dimensions: {_vec3_text(obj.dimensions)}")
        if obj.get('sbol_probable_placeholder', False):
            layout.label(text="Probable placeholder", icon='INFO')

        prefix = obj.get("sbol_part_prefix", "")
        if prefix:
            part_box = layout.box()
            part_box.label(text="Stage 3 Part Mapping", icon='MODIFIER')
            part_box.label(text=f"Category: {obj.get('sbol_part_category', 'Unclassified')}")
            part_box.label(text=f"Prefix: {prefix}")
            field = obj.get("sbol_carmods_field", "")
            part_box.label(text=f"CARMODS field: {field or '(none)'}")
            part_box.label(text=f"Stage: {obj.get('sbol_part_variant', '?')}")
            part_box.label(text=f"Mode: {obj.get('sbol_part_mode', '?')}")
            part_box.label(text=f"Stock: {'Yes' if obj.get('sbol_part_is_stock', False) else 'No'}")
            part_box.label(text=f"Object order: {obj.get('sbol_object_order', '?')}")

        meta = list(obj.get("mmdl_original_metadata", []))
        if meta:
            box = layout.box()
            box.label(text=f"Raw Object2 values ({len(meta)})")
            for i, value in enumerate(meta[:8]):
                box.label(text=f"[{i}] {value:.6g}")
            if len(meta) > 8:
                box.label(text=f"…{len(meta)-8} more in JSON export")


class SBOL_PT_material_inspector(bpy.types.Panel):
    bl_label = "Material Inspector"
    bl_idname = "SBOL_PT_material_inspector"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SB Online"
    bl_parent_id = "SBOL_PT_core_info"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return _find_import_root(context) is not None and obj is not None and obj.type == 'MESH' and len(obj.material_slots) > 0

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        layout.prop(obj, "active_material_index", text="Material slot")
        mat = _active_mmdl_material(obj)
        if mat is None:
            layout.label(text="No material in active slot")
            return

        layout.label(text=mat.name, icon='MATERIAL')
        texture = mat.get("mmdl_texture", "")
        layout.label(text=f"Texture: {texture or '(none)'}", icon='IMAGE_DATA')
        layout.label(text=f"Alpha enabled: {'Yes' if mat.get('sbol_alpha_enabled', False) else 'No'}")

        ranges = layout.box()
        ranges.label(text="Geometry range")
        ranges.label(text=f"Vertex offset/count: {mat.get('mmdl_vertex_offset', '?')} / {mat.get('mmdl_vertex_count', '?')}")
        ranges.label(text=f"Face offset/count: {mat.get('mmdl_face_offset', '?')} / {mat.get('mmdl_face_count', '?')}")
        ranges.label(text=f"Material ID: {mat.get('mmdl_id', '?')}")

        props = layout.box()
        props.label(text="Raw material values")
        props.label(text=f"d: {mat.get('mmdl_d_raw', 0):.6g}")
        props.label(text=f"Ni: {mat.get('mmdl_ni', 0):.6g}")
        props.label(text=f"Ns: {mat.get('mmdl_ns', 0):.6g}")
        props.label(text=f"Unknown7: {mat.get('mmdl_unknown7', 0):.6g}")
        for key, label in (("mmdl_ka", "Ka"), ("mmdl_kd", "Kd"), ("mmdl_ks", "Ks")):
            value = mat.get(key, [])
            if len(value) >= 3:
                props.label(text=f"{label}: {value[0]:.4g}, {value[1]:.4g}, {value[2]:.4g}")
        unknown = list(mat.get("mmdl_unknown_floats", []))
        if unknown:
            props.label(text="Unknown floats: " + ", ".join(f"{v:.4g}" for v in unknown))

class SBOL_OT_select_part_variant(bpy.types.Operator):
    bl_idname = "sbol.select_part_variant"
    bl_label = "Select Part Stage"
    bl_description = "Apply a CARMODS stage to all mesh prefixes driven by this field"
    bl_options = {'UNDO'}

    field_name: StringProperty()
    variant: IntProperty(default=0)

    def execute(self, context):
        root = _find_import_root(context)
        if root is None:
            self.report({'ERROR'}, "Select an imported MMDL object")
            return {'CANCELLED'}
        group = _group_definition(self.field_name)
        objects = _group_objects(root, self.field_name)
        if not group or not objects:
            self.report({'WARNING'}, "No matching parts found")
            return {'CANCELLED'}

        for obj in objects:
            value = int(obj.get("sbol_part_variant", 0))
            prefix = obj.get("sbol_part_prefix", "")
            if self.field_name == "overfenders":
                # CARMODS 0 means no overfender mesh. Stage 1 selects mesh variant 0.
                visible = self.variant > 0 and value == self.variant - 1
            elif self.field_name == "lights" and prefix == "bl":
                # BL is the shared road/floor beam card, not a style variant.
                visible = True
            else:
                visible = value <= self.variant if group["mode"] == "progressive" else value == self.variant
            _set_object_visible(obj, visible)

        if self.field_name == "frontbumper":
            _update_number_plates(root, self.variant)

        root[f"sbol_carmods_{self.field_name}"] = self.variant
        text = "Stock" if self.variant == 0 else f"Stage {self.variant}"
        self.report({'INFO'}, f"{group['label']}: {text}")
        return {'FINISHED'}


class SBOL_OT_toggle_part_group(bpy.types.Operator):
    bl_idname = "sbol.toggle_part_group"
    bl_label = "Toggle Part Group"
    bl_options = {'UNDO'}

    field_name: StringProperty()

    def execute(self, context):
        root = _find_import_root(context)
        if root is None:
            return {'CANCELLED'}
        objects = _group_objects(root, self.field_name)
        if not objects:
            return {'CANCELLED'}
        show = all(obj.hide_get() for obj in objects)
        for obj in objects:
            _set_object_visible(obj, show)
        return {'FINISHED'}


class SBOL_OT_show_all_parts(bpy.types.Operator):
    bl_idname = "sbol.show_all_parts"
    bl_label = "Show All Parts"
    bl_options = {'UNDO'}

    def execute(self, context):
        root = _find_import_root(context)
        if root is None:
            return {'CANCELLED'}
        for obj in _root_objects(root):
            _set_object_visible(obj, True)
        return {'FINISHED'}


class SBOL_OT_apply_stock_parts(bpy.types.Operator):
    bl_idname = "sbol.apply_stock_parts"
    bl_label = "Apply Stock CARMODS"
    bl_description = "Set all known CARMODS visual fields to stage 0"
    bl_options = {'UNDO'}

    def execute(self, context):
        root = _find_import_root(context)
        if root is None:
            return {'CANCELLED'}
        changed = 0
        for group in SBOL_PART_GROUPS:
            objects = _group_objects(root, group["field"])
            if not objects:
                continue
            if group["mode"] == "toggle":
                for obj in objects:
                    _set_object_visible(obj, False)
                    changed += 1
                continue
            for obj in objects:
                if group["field"] == "overfenders":
                    _set_object_visible(obj, False)
                elif group["field"] == "lights" and obj.get("sbol_part_prefix", "") == "bl":
                    _set_object_visible(obj, True)
                else:
                    _set_object_visible(obj, int(obj.get("sbol_part_variant", 0)) == 0)
                changed += 1
            root[f"sbol_carmods_{group['field']}"] = 0

        _update_number_plates(root, 0)

        # Compatibility fallback for models imported with v1.3.0 metadata.
        for obj in _root_objects(root):
            if "kage" in obj.name.casefold():
                _set_object_visible(obj, False)

        self.report({'INFO'}, f"Applied stock visibility to {changed} part objects")
        return {'FINISHED'}


class SBOL_PT_parts_browser(bpy.types.Panel):
    bl_label = "CARMODS Parts Browser"
    bl_idname = "SBOL_PT_parts_browser"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SB Online"
    bl_parent_id = "SBOL_PT_core_info"

    @classmethod
    def poll(cls, context):
        return _find_import_root(context) is not None

    def draw(self, context):
        layout = self.layout
        root = _find_import_root(context)
        row = layout.row(align=True)
        row.operator(SBOL_OT_apply_stock_parts.bl_idname, icon='HOME')
        row.operator(SBOL_OT_show_all_parts.bl_idname, icon='HIDE_OFF')

        found = False
        for group in SBOL_PART_GROUPS:
            objects = _group_objects(root, group["field"])
            if not objects:
                continue
            found = True
            box = layout.box()
            box.label(text=group["label"], icon='MODIFIER')
            if group["mode"] == "toggle":
                op = box.operator(SBOL_OT_toggle_part_group.bl_idname, text="Show / Hide")
                op.field_name = group["field"]
                continue

            variants = sorted({int(obj.get("sbol_part_variant", 0)) for obj in objects})
            selected = int(root.get(f"sbol_carmods_{group['field']}", -1))
            flow = box.grid_flow(row_major=True, columns=3, even_columns=True, even_rows=False, align=True)
            for variant in variants:
                label = "Stock (0)" if variant == 0 else f"Stage {variant}"
                if variant == selected:
                    label = "• " + label
                op = flow.operator(SBOL_OT_select_part_variant.bl_idname, text=label, depress=(variant == selected))
                op.field_name = group["field"]
                op.variant = variant

            prefixes = ", ".join(group["prefixes"])
            box.label(text=f"CARMODS: {group['field']} | meshes: {prefixes}", icon='INFO')
            if group["mode"] == "progressive":
                box.label(text="Progressive: selected stage includes all earlier interior stages", icon='ADD')

        if not found:
            layout.label(text="No confirmed CARMODS mesh prefixes found", icon='INFO')


def menu_func_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_mmdl.bl_idname, text="SBOL MMDL (.mmdl)")


classes = (
    IMPORT_SCENE_OT_mmdl,
    SBOL_OT_export_diagnostics,
    SBOL_OT_select_part_variant,
    SBOL_OT_toggle_part_group,
    SBOL_OT_show_all_parts,
    SBOL_OT_apply_stock_parts,
    SBOL_PT_core_info,
    SBOL_PT_parts_browser,
    SBOL_PT_object_inspector,
    SBOL_PT_material_inspector,
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
