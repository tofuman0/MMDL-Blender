bl_info = {
    "name": "Tokyo Xtreme Racer 0 XMDL Toolkit",
    "author": "Danny / OpenAI Codex",
    "version": (0, 7, 2),
    "blender": (4, 2, 0),
    "location": "File > Import > Tokyo Xtreme Racer 0 (.xmdl)",
    "description": "Experimental importer for Genki PlayStation 2 XMDL files",
    "category": "Import-Export",
}

import math
import os
import struct

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper


class XMDLError(Exception):
    pass


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _container_sections(data):
    if len(data) < 16:
        raise XMDLError("File is too small to be an XMDL container")
    count = _u32(data, 0)
    if not 1 <= count <= 32 or 4 + count * 4 > len(data):
        raise XMDLError("Invalid XMDL section table")
    offsets = [_u32(data, 4 + i * 4) for i in range(count)]
    if offsets != sorted(offsets) or offsets[0] < 4 + count * 4 or offsets[-1] >= len(data):
        raise XMDLError("Invalid XMDL section offsets")
    return [(off, offsets[i + 1] if i + 1 < count else len(data)) for i, off in enumerate(offsets)]


def _tim2_sections(data, sections):
    return [(start, end) for start, end in sections if data[start:start + 4] == b"TIM2"]


def _parse_prts(data, sections):
    for start, end in sections:
        size = end - start
        if size < 0x10:
            continue
        table_count = _u32(data, start)
        if not 1 <= table_count <= 16 or size != 0x10 + table_count * 0xF0:
            continue
        return {
            "offset": start,
            "count": table_count,
            "reserved": struct.unpack_from("<3I", data, start + 4),
            "tables": [bytes(data[start + 0x10 + i * 0xF0:start + 0x10 + (i + 1) * 0xF0])
                       for i in range(table_count)],
        }
    return None


def _create_prts_report(prts, prefix):
    if not prts:
        return None
    text = bpy.data.texts.new(f"{prefix}_PRTS.txt")
    text.write(f"PRTS offset: 0x{prts['offset']:X}\nTable count: {prts['count']}\n")
    text.write("Reserved: " + ", ".join(f"0x{value:08X}" for value in prts["reserved"]) + "\n")
    text.write("Each table is shown as 15 fixed rows of 16 local object slots; -- is 0xFF/unused.\n")
    for table_index, table in enumerate(prts["tables"]):
        used = sum(value != 0xFF for value in table)
        text.write(f"\nTable {table_index} ({used} populated slots)\n")
        for row in range(15):
            values = table[row * 16:(row + 1) * 16]
            rendered = " ".join("--" if value == 0xFF else f"{value:02X}" for value in values)
            text.write(f"{row:02d}: {rendered}\n")
    return text


def _deswizzle_clut_256(palette):
    result = []
    for base in range(0, 256, 32):
        block = palette[base:base + 32]
        result.extend(block[:8])
        result.extend(block[16:24])
        result.extend(block[8:16])
        result.extend(block[24:32])
    return result


def _decode_clut(clut_data, color_count, clut_type):
    """Decode TIM2 palette entries to RGBA using the declared colour type."""
    color_type = clut_type & 0x3F
    if color_type == 1:  # A1B5G5R5
        required = color_count * 2
        if len(clut_data) < required:
            raise XMDLError("Truncated 16-bit TIM2 palette")
        palette = []
        for offset in range(0, required, 2):
            value = struct.unpack_from("<H", clut_data, offset)[0]
            r5 = value & 0x1F
            g5 = (value >> 5) & 0x1F
            b5 = (value >> 10) & 0x1F
            palette.append((
                (r5 << 3) | (r5 >> 2),
                (g5 << 3) | (g5 >> 2),
                (b5 << 3) | (b5 >> 2),
                128 if value & 0x8000 else 0,
            ))
        return palette
    if color_type in (2, 3):  # X8B8G8R8 or A8B8G8R8
        required = color_count * 4
        if len(clut_data) < required:
            raise XMDLError("Truncated 32-bit TIM2 palette")
        palette = [tuple(clut_data[i:i + 4]) for i in range(0, required, 4)]
        if color_type == 2:
            palette = [(r, g, b, 128) for r, g, b, _unused in palette]
        return palette
    raise XMDLError(f"Unsupported TIM2 palette colour type {color_type}")


def _unswizzle_psmt4(indices, width, height):
    interlace = (0, 16, 2, 18, 17, 1, 19, 3)
    row_adjust = (0, 1, -1, 0)
    tile_adjust = (4, -4)
    result = [0] * (width * height)
    for y in range(height):
        odd_row = y & 1
        tile_y = (y // 4) & 1
        for x in range(width):
            tile_x = (x // 4) & 1
            group = ((x // 4) & 3) + (4 if odd_row else 0)
            source = (interlace[group] + ((x * 4) & 15) +
                      (x // 16) * 32 + ((y - 1) if odd_row else y) * width)
            dest_x = x + tile_y * tile_adjust[tile_x]
            dest_y = y + row_adjust[y & 3]
            dest = dest_y * width + dest_x
            if 0 <= source < len(indices) and 0 <= dest < len(result):
                result[dest] = indices[source]
    return result


def _unswizzle_psmt8(indices, width, height):
    result = [0] * (width * height)
    for y in range(height):
        for x in range(width):
            block = (y & ~15) * width + (x & ~15) * 2
            swap = (((y + 2) >> 2) & 1) * 4
            position_y = (((y & ~3) >> 1) + (y & 1)) & 7
            column = position_y * width * 2 + ((x + swap) & 7) * 4
            byte_number = ((y >> 1) & 1) + ((x >> 2) & 2)
            source = block + column + byte_number
            if source < len(indices):
                result[y * width + x] = indices[source]
    return result


def _decode_tim2(data, start=0, end=None):
    end = len(data) if end is None else end
    if start + 16 > end or data[start:start + 4] != b"TIM2":
        raise XMDLError("Not a TIM2 texture container")
    revision, alignment, picture_count = struct.unpack_from("<BBH", data, start + 4)
    if revision not in (3, 4):
        raise XMDLError(f"Unsupported TIM2 revision {revision}")
    offset = start + (0x80 if alignment else 0x10)
    pictures = []
    for picture_index in range(picture_count):
        if offset + 0x30 > end:
            raise XMDLError("Truncated TIM2 picture header")
        (total_size, clut_size, image_size, header_size, color_count,
         picture_format, mip_count, clut_type, image_type, width, height) = struct.unpack_from(
            "<IIIHHBBBBHH", data, offset
        )
        if total_size <= 0 or header_size < 0x30 or offset + total_size > end:
            raise XMDLError(f"Invalid TIM2 picture {picture_index}")
        image_offset = offset + header_size
        clut_offset = image_offset + image_size
        image_data = data[image_offset:clut_offset]
        clut_data = data[clut_offset:clut_offset + clut_size]
        pixel_count = width * height
        if image_type == 4:
            indices = []
            for value in image_data:
                indices.extend((value & 0x0F, value >> 4))
            indices = indices[:pixel_count]
            indices = _unswizzle_psmt4(indices, width, height)
        elif image_type == 5:
            indices = list(image_data[:pixel_count])
            indices = _unswizzle_psmt8(indices, width, height)
        elif image_type == 3:
            indices = None
        else:
            raise XMDLError(f"Unsupported TIM2 image type {image_type} in picture {picture_index}")

        if indices is not None:
            palette = _decode_clut(clut_data, color_count, clut_type)
            tex0 = struct.unpack_from("<Q", data, offset + 0x18)[0]
            csm = (tex0 >> 55) & 1
            if image_type == 5 and color_count >= 256 and csm == 0:
                palette = _deswizzle_clut_256(palette[:256]) + palette[256:]
            rgba = [palette[index] for index in indices]
        else:
            if len(image_data) < pixel_count * 4:
                raise XMDLError(f"Truncated TIM2 RGBA image {picture_index}")
            rgba = [tuple(image_data[i:i + 4]) for i in range(0, pixel_count * 4, 4)]
        # PS2 alpha uses a 0..128-ish range; expand it to Blender's 0..255.
        rgba = [(r, g, b, min(255, a * 2)) for r, g, b, a in rgba]
        pictures.append({
            "index": picture_index, "width": width, "height": height,
            "rgba": rgba, "format": image_type, "mip_count": mip_count,
            "picture_format": picture_format,
        })
        offset += total_size
    return pictures


def _create_blender_images(pictures, prefix, pack=True):
    images = []
    for picture in pictures:
        image = bpy.data.images.new(
            f"{prefix}_TIM2_{picture['index']:03d}",
            width=picture["width"], height=picture["height"], alpha=True
        )
        width, height = picture["width"], picture["height"]
        rgba = picture["rgba"]
        pixels = []
        # TIM2 rows are top-down; Blender image buffers are bottom-up.
        for y in range(height - 1, -1, -1):
            for r, g, b, a in rgba[y * width:(y + 1) * width]:
                pixels.extend((r / 255.0, g / 255.0, b / 255.0, a / 255.0))
        image.pixels.foreach_set(pixels)
        image.update()
        image["tim2_picture_index"] = picture["index"]
        image["tim2_image_type"] = picture["format"]
        if pack:
            image.pack()
        images.append(image)
    return images


def _create_texture_materials(images, prefix):
    materials = []
    for index, image in enumerate(images):
        material = bpy.data.materials.new(f"{prefix}_TIM2_Material_{index:03d}")
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        principled = nodes.get("Principled BSDF")
        texture = nodes.new("ShaderNodeTexImage")
        texture.name = "TIM2 Image"
        texture.label = f"TIM2 picture {index}"
        texture.image = image
        texture.interpolation = "Closest"
        links.new(texture.outputs["Color"], principled.inputs["Base Color"])
        links.new(texture.outputs["Alpha"], principled.inputs["Alpha"])
        materials.append(material)
    return materials


def _create_default_material(prefix):
    """Create the shared material used by draw groups with no TIM2 texture."""
    material = bpy.data.materials.new(f"{prefix}_Paint")
    material.use_nodes = True
    material.diffuse_color = (0.18, 0.18, 0.18, 1.0)
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = (0.18, 0.18, 0.18, 1.0)
        principled.inputs["Metallic"].default_value = 0.35
        principled.inputs["Roughness"].default_value = 0.28
    material["xmdl_untextured_default"] = True
    return material


def _find_model_sections(data, sections):
    result = []
    for start, end in sections:
        header = data[start:min(end, start + 64)]
        if b"XMDL" in header or b"LDMX" in header:
            result.append((start, end))
    if not result:
        raise XMDLError("No XMDL model section was found")
    return result


def _find_model_section(data, sections):
    return _find_model_sections(data, sections)[0]


def _find_object_starts(data, start, end):
    """Find model-object headers using their invariant transform and bounds."""
    hits = []
    for offset in range((start + 0xF) & ~0xF, end - 0x80, 0x10):
        if struct.unpack_from("<3I", data, offset + 4) != (0, 0, 1):
            continue
        minimum = struct.unpack_from("<4f", data, offset + 0x60)
        maximum = struct.unpack_from("<4f", data, offset + 0x70)
        if minimum[3] != 1.0 or maximum[3] != 1.0:
            continue
        if not all(math.isfinite(v) and abs(v) < 100.0 for v in minimum[:3] + maximum[:3]):
            continue
        if all(minimum[i] <= maximum[i] for i in range(3)):
            hits.append(offset)
    return hits


def _decode_vif_geometry(data, start, end):
    """Recover paired PS2 VIF V3-32 position and V4-16 normal batches."""
    batches = []
    for offset in range((start + 3) & ~3, end - 48, 4):
        word = _u32(data, offset)
        command = (word >> 24) & 0x7F
        count = (word >> 16) & 0xFF
        immediate = word & 0xFFFF
        if command != 0x68 or not 3 <= count <= 64 or not (immediate & 0x8000):
            continue
        positions_end = offset + 4 + count * 12
        if positions_end + 4 > end:
            continue
        normal_data = None
        uv_data = None
        attribute_offset = positions_end
        # Some XMDLs put V2-32 UVs and V4-8 colours between positions and the
        # V4-16 normal packet. Walk the actual VIF UNPACK sizes to find it.
        for _ in range(5):
            if attribute_offset + 4 > end:
                break
            attribute_word = _u32(data, attribute_offset)
            attribute_command = (attribute_word >> 24) & 0x7F
            attribute_count = (attribute_word >> 16) & 0xFF
            if attribute_command == 0x6D and attribute_count == count:
                normal_data = attribute_offset + 4
            if attribute_command == 0x64 and attribute_count == count:
                uv_data = attribute_offset + 4
            if not 0x60 <= attribute_command <= 0x6F or attribute_count != count:
                break
            component_count = ((attribute_command & 0x0F) >> 2) + 1
            bits = (32, 16, 8, 5)[attribute_command & 3]
            payload_size = (attribute_count * component_count * bits + 7) // 8
            attribute_offset += 4 + ((payload_size + 3) & ~3)
        if normal_data is None or normal_data + count * 8 > end:
            continue
        values = struct.unpack_from("<" + "f" * (count * 3), data, offset + 4)
        if not all(math.isfinite(v) and abs(v) < 1000.0 for v in values):
            continue
        # Genki model space is Y-up with the car roof at negative Y. Blender is
        # Z-up, so preserve handedness while turning the car right-side-up.
        positions = [(values[i], values[i + 2], -values[i + 1])
                     for i in range(0, len(values), 3)]
        span = max(max(p[a] for p in positions) - min(p[a] for p in positions) for a in range(3))
        if span < 1.0e-7:
            continue
        packed = struct.unpack_from("<" + "h" * (count * 4), data, normal_data)
        uvs = None
        if uv_data is not None:
            uv_values = struct.unpack_from("<" + "f" * (count * 2), data, uv_data)
            if all(math.isfinite(value) and abs(value) < 1000.0 for value in uv_values):
                uvs = list(zip(uv_values[0::2], uv_values[1::2]))
        normals = []
        flags = []
        for i in range(count):
            x, y, z, flag = packed[i * 4:i * 4 + 4]
            length = math.sqrt(x * x + y * y + z * z)
            normals.append((x / length, z / length, -y / length) if length else (0.0, 0.0, 1.0))
            flags.append(flag)
        batches.append((offset, positions, normals, flags, uvs))
    if not batches:
        raise XMDLError("No supported PS2 VIF geometry batches were found")
    return batches


def _decode_draw_groups(data, start, batches):
    """Decode component draw groups and their VIF-batch/material metadata."""
    groups = []
    marker = struct.pack("<I", 0xCCCCCCCC)
    previous_offset = start
    for batch_index, (position_offset, *_unused) in enumerate(batches):
        # Every recovered packet has a 0x48-byte VIF setup immediately before
        # its position UNPACK. A new draw group places its control record in
        # the gap preceding that setup.
        setup_offset = position_offset - 0x48
        if setup_offset <= previous_offset:
            previous_offset = position_offset
            continue
        region = data[previous_offset:setup_offset]
        marker_relative = region.rfind(marker)
        if marker_relative >= 0:
            marker_offset = previous_offset + marker_relative
            if marker_offset >= 0x60 and marker_offset + 12 <= setup_offset:
                texture_word = _u32(data, marker_offset - 0x60)
                packed_texture_index = (texture_word >> 16) & 0xFFFF
                # A completely zero material word selects the untextured/default
                # car material; it does not reference TIM2 picture zero.
                texture_index = (packed_texture_index
                                 if (texture_word & 0xFFFF) == 0
                                 and 0 < packed_texture_index < 0x100
                                 else None)
                group_batch_count = _u32(data, marker_offset + 8)
                if 1 <= group_batch_count <= len(batches) - batch_index:
                    group_word, _count, group_size = struct.unpack_from("<3I", data, marker_offset + 4)
                    groups.append({
                        "index": len(groups),
                        "first_batch": batch_index,
                        "batch_count": group_batch_count,
                        "texture_index": texture_index,
                        "texture_word": texture_word,
                        "group_word": group_word,
                        "group_size": group_size,
                        "record_offset": marker_offset,
                    })
        previous_offset = position_offset
    # Preserve ranges whose control record uses an unsupported layout. This
    # guarantees a complete, non-overlapping partition without inventing a
    # texture assignment for those batches.
    complete_groups = []
    cursor = 0
    for group in groups:
        if group["first_batch"] > cursor:
            complete_groups.append({
                "index": 0, "first_batch": cursor,
                "batch_count": group["first_batch"] - cursor,
                "texture_index": None, "texture_word": 0, "group_word": 0,
                "group_size": 0, "record_offset": batches[cursor][0],
                "synthetic": True,
            })
        if group["first_batch"] < cursor:
            continue
        group["synthetic"] = False
        complete_groups.append(group)
        cursor = group["first_batch"] + group["batch_count"]
    if cursor < len(batches):
        complete_groups.append({
            "index": 0, "first_batch": cursor,
            "batch_count": len(batches) - cursor,
            "texture_index": None, "texture_word": 0, "group_word": 0,
            "group_size": 0, "record_offset": batches[cursor][0],
            "synthetic": True,
        })
    for index, group in enumerate(complete_groups):
        group["index"] = index
    return complete_groups


def _decode_draw_group_materials(data, start, batches):
    """Map VIF batch indexes to TIM2 indexes from XMDL draw-group records."""
    materials = {}
    for group in _decode_draw_groups(data, start, batches):
        texture_index = group["texture_index"]
        if texture_index is None:
            continue
        for grouped_index in range(group["first_batch"],
                                   group["first_batch"] + group["batch_count"]):
            materials[grouped_index] = texture_index
    return materials


def _make_mesh(context, name, batches, use_normals, filter_bridges, correct_winding, weld_vertices):
    vertices, normals, faces, batch_ids, face_uvs = [], [], [], [], []
    vertex_lookup = {}
    for batch_id, (_offset, positions, batch_normals, _flags, batch_uvs) in enumerate(batches):
        local_indices = []
        for position, normal in zip(positions, batch_normals):
            key = None
            if weld_vertices:
                key = tuple(round(value, 6) for value in position)
                if use_normals:
                    key += tuple(round(value, 4) for value in normal)
            index = vertex_lookup.get(key) if key is not None else None
            if index is None:
                index = len(vertices)
                vertices.append(position)
                normals.append(normal)
                if key is not None:
                    vertex_lookup[key] = index
            local_indices.append(index)
        for i in range(2, len(positions)):
            local_tri = (i - 2, i - 1, i)
            tri = (local_indices[i - 2], local_indices[i - 1], local_indices[i])
            if i & 1:
                tri = (tri[1], tri[0], tri[2])
                local_tri = (local_tri[1], local_tri[0], local_tri[2])
            a, b, c = (vertices[j] for j in tri)
            cross = ((b[1]-a[1])*(c[2]-a[2])-(b[2]-a[2])*(c[1]-a[1]),
                     (b[2]-a[2])*(c[0]-a[0])-(b[0]-a[0])*(c[2]-a[2]),
                     (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
            if correct_winding:
                average_normal = tuple(
                    batch_normals[i - 2][axis] + batch_normals[i - 1][axis] + batch_normals[i][axis]
                    for axis in range(3)
                )
                if sum(cross[axis] * average_normal[axis] for axis in range(3)) < 0.0:
                    tri = (tri[1], tri[0], tri[2])
                    local_tri = (local_tri[1], local_tri[0], local_tri[2])
                    cross = (-cross[0], -cross[1], -cross[2])
            longest_edge = max(math.dist(a, b), math.dist(b, c), math.dist(c, a))
            # The VU stream does not expose its GS ADC restart bits directly.
            # Discard only implausibly long triangles produced across a hidden
            # restart; 0.75 model units is deliberately conservative for cars.
            if (not filter_bridges or longest_edge <= 0.75) and sum(value * value for value in cross) > 1.0e-16:
                faces.append(tri)
                batch_ids.append(batch_id)
                face_uvs.append(tuple(batch_uvs[index] for index in local_tri) if batch_uvs else None)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    batch_attr = mesh.attributes.new("xmdl_vif_batch", "INT", "FACE")
    batch_attr.data.foreach_set("value", batch_ids)
    if any(uvs is not None for uvs in face_uvs):
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for polygon, uvs in zip(mesh.polygons, face_uvs):
            if uvs is None:
                continue
            for loop_index, (u, v) in zip(polygon.loop_indices, uvs):
                uv_layer.data[loop_index].uv = (u, 1.0 - v)
    if use_normals and len(normals) == len(mesh.vertices):
        try:
            mesh.normals_split_custom_set_from_vertices(normals)
        except (AttributeError, RuntimeError):
            pass
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    obj["xmdl_vif_batches"] = len(batches)
    obj["xmdl_importer_version"] = "0.7.2"
    obj["xmdl_has_uvs"] = any(uvs is not None for uvs in face_uvs)
    return obj


def import_xmdl(context, filepath, use_normals=True, filter_bridges=False,
                base_model_only=False, show_all_models=False, correct_winding=True,
                weld_vertices=True, load_textures=True, texture_mapping="NONE",
                geometry_grouping="COMPONENT"):
    with open(filepath, "rb") as stream:
        data = stream.read()
    sections = _container_sections(data)
    model_sections = _find_model_sections(data, sections)
    root_name = os.path.splitext(os.path.basename(filepath))[0]
    prts = _parse_prts(data, sections)
    prts_report = _create_prts_report(prts, root_name)
    texture_images = []
    if load_textures:
        for texture_section_index, (texture_start, texture_end) in enumerate(_tim2_sections(data, sections)):
            pictures = _decode_tim2(data, texture_start, texture_end)
            texture_images.extend(_create_blender_images(
                pictures, f"{os.path.splitext(os.path.basename(filepath))[0]}_{texture_section_index:02d}"
            ))
    texture_materials = (_create_texture_materials(texture_images, root_name)
                         if texture_images and texture_mapping != "NONE" else [])
    default_material = (_create_default_material(root_name)
                        if texture_mapping == "DRAW_GROUP" else None)
    selected_sections = model_sections
    objects = []
    component_report_lines = [f"XMDL component report: {root_name}\n"]
    total_batches = total_vertices = total_faces = 0
    for model_index, (model_start, model_end) in enumerate(selected_sections):
        model_group_count = _u32(data, model_start + 0x20) if model_start + 0x24 <= model_end else 0
        declared_object_count = (_u32(data, model_start + 0x24)
                                 if model_start + 0x28 <= model_end else 0)
        object_starts = _find_object_starts(data, model_start + 0x20, model_end)
        component_report_lines.append(
            f"\nModel section {model_index}: 0x{model_start:X}-0x{model_end:X}\n"
            f"Declared logical objects: {declared_object_count}\n"
            f"Serialized component headers: {len(object_starts)}\n"
            f"Unserialized/unsupported logical slots: {max(0, declared_object_count - len(object_starts))}\n"
        )
        if len(object_starts) >= 2:
            object_ranges = [(start, object_starts[i + 1] if i + 1 < len(object_starts) else model_end)
                             for i, start in enumerate(object_starts)]
        else:
            object_ranges = [(model_start, model_end)]
        if base_model_only:
            object_ranges = object_ranges[:1]
        for object_index, (decode_start, decode_end) in enumerate(object_ranges):
            try:
                batches = _decode_vif_geometry(data, decode_start, decode_end)
            except XMDLError:
                continue
            draw_groups = _decode_draw_groups(data, decode_start, batches)
            draw_group_materials = {}
            for draw_group in draw_groups:
                texture_index = draw_group["texture_index"]
                if texture_index is None:
                    continue
                for batch_index in range(draw_group["first_batch"],
                                         draw_group["first_batch"] + draw_group["batch_count"]):
                    draw_group_materials[batch_index] = texture_index
            component_report_lines.append(
                f"Component {object_index:03d} @ 0x{decode_start:X}: "
                f"{len(batches)} batches, {len(draw_groups)} draw groups\n"
            )
            for draw_group in draw_groups:
                texture_label = ("--" if draw_group["texture_index"] is None
                                 else f"{draw_group['texture_index']:03d}")
                component_report_lines.append(
                    f"  draw {draw_group['index']:03d}: batches "
                    f"{draw_group['first_batch']:04d}-"
                    f"{draw_group['first_batch'] + draw_group['batch_count'] - 1:04d}, "
                    f"texture {texture_label}, record 0x{draw_group['record_offset']:X}"
                    f"{' [unsupported control layout]' if draw_group.get('synthetic') else ''}\n"
                )
            if len(selected_sections) == 1 and len(object_ranges) == 1:
                name = root_name
            else:
                name = f"{root_name}_model_{model_index:02d}_component_{object_index:03d}"
            lod_number = None
            lod_table_index = None
            lod_table_count = prts["count"] - 1 if prts else max(0, model_group_count - 1)
            if lod_table_count and len(object_ranges) > lod_table_count and object_index >= len(object_ranges) - lod_table_count:
                lod_table_index = object_index - (len(object_ranges) - lod_table_count) + 1
                lod_number = lod_table_index + 1
                name = f"{root_name}_LOD{lod_number}_object_{object_index:03d}"
            header_material_word = (_u32(data, decode_start + 0x8C)
                                    if decode_start + 0x90 <= decode_end else 0)
            header_material_index = (header_material_word >> 16) & 0xFFFF
            if geometry_grouping == "VIF_BATCHES":
                mesh_groups = [([batch], [batch_index], None)
                               for batch_index, batch in enumerate(batches)]
            elif geometry_grouping == "DRAW_GROUPS":
                mesh_groups = []
                for draw_group in draw_groups:
                    first = draw_group["first_batch"]
                    count = draw_group["batch_count"]
                    mesh_groups.append((batches[first:first + count], list(range(first, first + count)), draw_group))
            else:
                mesh_groups = [(batches, list(range(len(batches))), None)]
            for group_batches, source_batch_indexes, source_draw_group in mesh_groups:
                if geometry_grouping == "VIF_BATCHES":
                    group_name = f"{name}_batch_{source_batch_indexes[0]:04d}"
                elif geometry_grouping == "DRAW_GROUPS":
                    group_name = f"{name}_draw_{source_draw_group['index']:03d}"
                else:
                    group_name = name
                obj = _make_mesh(
                    context, group_name, group_batches, use_normals, filter_bridges,
                    correct_winding, weld_vertices
                )
                obj["xmdl_section_count"] = len(sections)
                obj["xmdl_model_section"] = model_index
                obj["xmdl_serialized_component_index"] = object_index
                obj["xmdl_internal_object"] = object_index  # compatibility
                obj["xmdl_declared_object_count"] = declared_object_count
                obj["xmdl_serialized_component_count"] = len(object_starts)
                obj["xmdl_missing_logical_slots"] = max(0, declared_object_count - len(object_starts))
                obj["xmdl_component_batch_count"] = len(batches)
                obj["xmdl_component_draw_group_count"] = len(draw_groups)
                obj["xmdl_geometry_grouping"] = geometry_grouping
                obj["xmdl_split_vif_batch"] = (source_batch_indexes[0]
                                                if geometry_grouping == "VIF_BATCHES" else -1)
                if source_draw_group is not None:
                    obj["xmdl_draw_group"] = source_draw_group["index"]
                    obj["xmdl_draw_group_first_batch"] = source_draw_group["first_batch"]
                    obj["xmdl_draw_group_batch_count"] = source_draw_group["batch_count"]
                    obj["xmdl_draw_group_texture_index"] = (source_draw_group["texture_index"]
                                                            if source_draw_group["texture_index"] is not None else -1)
                    obj["xmdl_draw_group_record_offset"] = source_draw_group["record_offset"]
                    obj["xmdl_draw_group_synthetic"] = bool(source_draw_group.get("synthetic"))
                if lod_number is not None:
                    obj["xmdl_lod"] = lod_number
                obj["xmdl_model_section_count"] = len(model_sections)
                obj["xmdl_prts_table_count"] = prts["count"] if prts else 0
                obj["xmdl_prts_table"] = lod_table_index if lod_table_index is not None else 0
                if lod_table_index is not None and prts:
                    populated = [(slot, value) for slot, value in enumerate(prts["tables"][lod_table_index])
                                 if value != 0xFF]
                    if len(populated) == 1:
                        obj["xmdl_prts_slot"] = populated[0][0]
                        obj["xmdl_prts_local_object"] = populated[0][1]
                if prts_report:
                    obj["xmdl_prts_report"] = prts_report.name
                obj["xmdl_imported_model_sections"] = len(selected_sections)
                obj["xmdl_detected_objects"] = len(object_starts)
                obj["xmdl_base_model_only"] = base_model_only
                obj["xmdl_source_file"] = filepath
                obj["xmdl_embedded_texture_count"] = len(texture_images)
                obj["xmdl_header_material_word"] = header_material_word
                obj["xmdl_header_material_index"] = header_material_index
                texture_index = None
                if texture_mapping == "DRAW_GROUP":
                    # Mesh polygons default to material index zero. Reserve that
                    # slot for untextured/paint faces before adding TIM2 slots.
                    # Otherwise every unassigned face inherits the first texture.
                    obj.data.materials.append(default_material)
                    used_indexes = sorted({draw_group_materials[index] for index in source_batch_indexes
                                           if index in draw_group_materials and
                                           draw_group_materials[index] < len(texture_materials)})
                    slots = {}
                    for mapped_index in used_indexes:
                        slots[mapped_index] = len(obj.data.materials)
                        obj.data.materials.append(texture_materials[mapped_index])
                    batch_attribute = obj.data.attributes.get("xmdl_vif_batch")
                    if batch_attribute and slots:
                        for polygon in obj.data.polygons:
                            local_batch = batch_attribute.data[polygon.index].value
                            source_batch = source_batch_indexes[local_batch]
                            mapped_index = draw_group_materials.get(source_batch)
                            if mapped_index in slots:
                                polygon.material_index = slots[mapped_index]
                        obj["xmdl_texture_mapping"] = "draw_group_material_index"
                        obj["xmdl_draw_group_material_count"] = len(slots)
                        if len(source_batch_indexes) == 1 and source_batch_indexes[0] in draw_group_materials:
                            obj["xmdl_tim2_picture_index"] = draw_group_materials[source_batch_indexes[0]]
                elif texture_mapping == "HEADER_INDEX":
                    texture_index = header_material_index
                elif texture_mapping == "OBJECT_INDEX":
                    texture_index = object_index
                if (texture_materials and texture_index is not None and
                        texture_index < len(texture_materials) and obj.get("xmdl_has_uvs")):
                    obj.data.materials.append(texture_materials[texture_index])
                    obj["xmdl_texture_mapping"] = ("experimental_header_material_index"
                                                   if texture_mapping == "HEADER_INDEX"
                                                   else "experimental_object_index")
                    obj["xmdl_tim2_picture_index"] = texture_index
                obj.select_set(True)
                if model_index > 0 and not show_all_models:
                    obj.hide_set(True)
                    obj.hide_render = True
                if lod_number is not None:
                    obj.hide_set(True)
                    obj.hide_render = True
                objects.append(obj)
                total_vertices += len(obj.data.vertices)
                total_faces += len(obj.data.polygons)
            total_batches += len(batches)
    if not objects:
        raise XMDLError("No importable XMDL objects were found")
    component_report = bpy.data.texts.new(f"{root_name}_COMPONENTS.txt")
    component_report.write("".join(component_report_lines))
    for obj in objects:
        obj["xmdl_component_report"] = component_report.name
    context.view_layer.objects.active = objects[0]
    return objects[0], total_batches, total_vertices, total_faces


class TXR0_OT_import_xmdl(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.txr0_xmdl"
    bl_label = "Import Tokyo Xtreme Racer 0 XMDL"
    bl_options = {"UNDO", "PRESET"}
    filename_ext = ".xmdl"
    filter_glob: StringProperty(default="*.xmdl;*.XMDL", options={"HIDDEN"})
    use_normals: BoolProperty(name="Import packed normals", default=True)
    filter_bridges: BoolProperty(
        name="Filter strip bridge triangles",
        description="Remove unusually long polygons created at hidden PS2 triangle-strip restarts",
        default=False,
    )
    base_model_only: BoolProperty(
        name="First internal object only",
        description="Import only the first internal object from each XMDL section",
        default=False,
    )
    show_all_models: BoolProperty(
        name="Show all model sections",
        description="Leave every imported XMDL section visible instead of hiding all except section 0",
        default=False,
    )
    correct_winding: BoolProperty(
        name="Correct face winding",
        description="Orient each generated triangle to agree with the packed XMDL vertex normals",
        default=True,
    )
    weld_vertices: BoolProperty(
        name="Weld matching strip vertices",
        description="Join duplicate VIF-strip boundary vertices when their position and normal match",
        default=True,
    )
    geometry_grouping: EnumProperty(
        name="Geometry grouping",
        description="Choose the XMDL hierarchy level represented by Blender objects",
        items=(
            ("COMPONENT", "Complete components", "One Blender object per serialized XMDL component, with draw groups as material regions"),
            ("DRAW_GROUPS", "Draw groups", "One Blender object per XMDL draw group"),
            ("VIF_BATCHES", "Raw VIF batches", "Diagnostic: one Blender object per small PS2 VIF batch"),
        ),
        default="COMPONENT",
    )
    load_textures: BoolProperty(
        name="Load embedded TIM2 textures",
        description="Decode embedded TIM2 pictures into packed Blender images",
        default=True,
    )
    texture_mapping: EnumProperty(
        name="Experimental texture mapping",
        description="Choose how embedded TIM2 pictures are assigned to XMDL geometry",
        items=(
            ("DRAW_GROUP", "Draw-group material index",
             "Assign TIM2 pictures using the confirmed per-draw-group texture field"),
            ("HEADER_INDEX", "Header material index (experimental)",
             "Use the upper 16 bits of the object header material word as the TIM2 picture index"),
            ("OBJECT_INDEX", "Object index → texture index", "Assign TIM2 picture N to internal object N"),
            ("NONE", "Do not assign", "Load TIM2 images and UVs without creating material assignments"),
        ),
        default="DRAW_GROUP",
    )

    def execute(self, context):
        try:
            _obj, batches, vertices, faces = import_xmdl(
                context, self.filepath, self.use_normals, self.filter_bridges,
                self.base_model_only, self.show_all_models, self.correct_winding,
                self.weld_vertices, self.load_textures, self.texture_mapping,
                self.geometry_grouping
            )
        except (OSError, struct.error, XMDLError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {vertices:,} vertices / {faces:,} faces in {batches:,} VIF batches")
        return {"FINISHED"}


class TXR0_OT_import_tim2(bpy.types.Operator, ImportHelper):
    bl_idname = "import_image.txr0_tim2"
    bl_label = "Import PlayStation 2 TIM2"
    bl_options = {"UNDO", "PRESET"}
    filename_ext = ".tm2"
    filter_glob: StringProperty(default="*.tm2;*.TM2;*.tim2;*.TIM2", options={"HIDDEN"})

    def execute(self, _context):
        try:
            with open(self.filepath, "rb") as stream:
                data = stream.read()
            pictures = _decode_tim2(data)
            prefix = os.path.splitext(os.path.basename(self.filepath))[0]
            images = _create_blender_images(pictures, prefix)
        except (OSError, struct.error, XMDLError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Loaded {len(images)} TIM2 picture(s)")
        return {"FINISHED"}


def _menu_import(self, _context):
    self.layout.operator(TXR0_OT_import_xmdl.bl_idname, text="Tokyo Xtreme Racer 0 (.xmdl)")
    self.layout.operator(TXR0_OT_import_tim2.bl_idname, text="PlayStation 2 TIM2 (.tm2/.tim2)")


classes = (TXR0_OT_import_xmdl, TXR0_OT_import_tim2)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
